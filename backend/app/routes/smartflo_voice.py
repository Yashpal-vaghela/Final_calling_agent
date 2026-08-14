import json
import os
import asyncio
import urllib.parse
from typing import Optional
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, Response

from backend.app.models.call import Call
from backend.app.services.smartflo_service import smartflo_client
from agent.utils.call_logger import save_calls, load_calls
from agent.pipeline import VoicePipelineOrchestrator

router = APIRouter()

@router.api_route("/smartflo/voice", methods=["GET", "POST"])
async def smartflo_dynamic_endpoint(request: Request):
    """
    Dynamic Endpoint called by Tata Smartflo when a call connects.
    Smartflo passes parameters like $callId, $fromNumber, $toNumber, $status.
    
    MUST respond within 2000ms with HTTP 200 and JSON:
    {
        "sucess": true,
        "wss_url": "wss://<your-host>/smartflo/media-stream?call_sid=..."
    }
    (Note: 'sucess' with single 'c' as per Tata Smartflo specification)
    """
    # Parse parameters from either JSON body (POST) or Query params (GET)
    params = {}
    if request.method == "POST":
        try:
            params = await request.json()
        except Exception:
            form = await request.form()
            params = dict(form)
    else:
        params = dict(request.query_params)

    call_id = params.get("callId") or params.get("call_id") or params.get("callSid") or ""
    from_number = params.get("fromNumber") or params.get("from") or ""
    to_number = params.get("toNumber") or params.get("to") or ""
    status = params.get("status") or "ringing"
    
    opening_intent = params.get("opening_intent") or request.query_params.get("opening_intent")
    lead_id = params.get("lead_id") or request.query_params.get("lead_id")

    # Log call record if call_id is present
    if call_id:
        call_log = Call(
            twilio_call_sid=str(call_id),
            direction="outbound" if from_number else "inbound",
            from_number=str(from_number),
            to_number=str(to_number)
        )
        calls_db = load_calls()
        calls_db[str(call_id)] = call_log.to_dict()
        save_calls(calls_db)

    # Determine host for WebSocket URL
    host = request.headers.get("host", "localhost:8000")
    protocol = "wss" if "ngrok" in host or request.url.scheme == "https" else "ws"
    
    ws_url = f"{protocol}://{host}/smartflo/media-stream?call_sid={urllib.parse.quote(str(call_id))}"
    if opening_intent:
        ws_url += f"&opening_intent={urllib.parse.quote(str(opening_intent))}"
    if lead_id:
        ws_url += f"&lead_id={urllib.parse.quote(str(lead_id))}"

    print(f"[Smartflo Dynamic Endpoint] Resolved wss_url: {ws_url} for callId: {call_id}")

    # Return strict Tata Smartflo JSON response schema
    return JSONResponse(
        status_code=200,
        content={
            "sucess": True,
            "wss_url": ws_url
        }
    )


@router.websocket("/smartflo/media-stream")
async def smartflo_media_stream(
    websocket: WebSocket,
    call_sid: str = "",
    opening_intent: Optional[str] = None,
    lead_id: Optional[str] = None
):
    """
    Bi-directional audio streaming WebSocket endpoint for Tata Smartflo.
    Exchanges 8kHz µ-law audio chunks and lifecycle events with VoicePipelineOrchestrator.
    """
    await websocket.accept()
    call_sid = call_sid or websocket.query_params.get("call_sid", "")
    print(f"[Smartflo WS] Connection accepted for CallSid: {call_sid}")

    lead_name = None
    lead_city = None
    if lead_id:
        try:
            leads_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "leads.json")
            if os.path.exists(leads_path):
                with open(leads_path, "r", encoding="utf-8") as f:
                    leads = json.load(f)
                    for l in leads:
                        if l.get("id") == lead_id:
                            lead_name = l.get("name")
                            lead_city = l.get("city")
                            break
        except Exception as e:
            print(f"[Smartflo WS] Could not look up lead details for {lead_id}: {e}")

    orchestrator = None

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event_type = data.get("event")

            if event_type == "connected":
                print("[Smartflo WS] Connected event received from Smartflo.")

            elif event_type == "start":
                start_obj = data.get("start", {})
                stream_sid = start_obj.get("streamSid") or data.get("streamSid", "")
                call_sid = start_obj.get("callSid") or call_sid
                print(f"[Smartflo WS] Stream started. StreamSid: {stream_sid}, CallSid: {call_sid}")

                orchestrator = VoicePipelineOrchestrator(
                    websocket=websocket,
                    call_id=str(call_sid),
                    stream_sid=str(stream_sid),
                    opening_intent=opening_intent,
                    lead_id=lead_id,
                    lead_name=lead_name,
                    lead_city=lead_city
                )
                await orchestrator.start()

            elif event_type == "media":
                if orchestrator is not None:
                    media_obj = data.get("media", {})
                    payload = media_obj.get("payload", "")
                    if payload:
                        await orchestrator.handle_media_payload(payload)

            elif event_type == "dtmf":
                digit = data.get("dtmf", {}).get("digit")
                print(f"[Smartflo WS] DTMF digit received: {digit}")

            elif event_type == "stop":
                print(f"[Smartflo WS] Stream stopped by Smartflo.")
                break

    except WebSocketDisconnect:
        print(f"[Smartflo WS] WebSocket disconnected for CallSid: {call_sid}")
    except Exception as e:
        print(f"[Smartflo WS] Error handling stream for CallSid {call_sid}: {e}")
    finally:
        if orchestrator is not None:
            await orchestrator.stop()
        print(f"[Smartflo WS] Cleaned up stream for CallSid: {call_sid}")


@router.post("/api/smartflo/outbound")
async def trigger_smartflo_outbound(request: Request):
    """
    Trigger an outbound AI call to a customer using Tata Smartflo Click-to-Call Support.
    """
    body = await request.json()
    customer_number = body.get("customer_number")
    lead_id = body.get("lead_id")
    opening_intent = body.get("opening_intent", "follow-up")

    if not customer_number:
        raise HTTPException(status_code=400, detail="customer_number is required")

    custom_params = {}
    if lead_id:
        custom_params["lead_id"] = lead_id
    if opening_intent:
        custom_params["opening_intent"] = opening_intent

    result = await smartflo_client.initiate_click_to_call(
        customer_number=customer_number,
        custom_params=custom_params
    )
    return result
