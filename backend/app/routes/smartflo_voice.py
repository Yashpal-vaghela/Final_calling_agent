import json
import os
import asyncio
import urllib.parse
from typing import Optional
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, Response

from backend.app.models.call import Call
from backend.app.services.smartflo_service import smartflo_client
from backend.app.routes.contact_form import lookup_caller_context
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

    call_id = str(params.get("callId") or params.get("call_id") or params.get("callSid") or "")
    from_number = str(params.get("fromNumber") or params.get("from") or "")
    to_number = str(params.get("toNumber") or params.get("to") or "")
    status = str(params.get("status") or "ringing")
    
    raw_intent = params.get("opening_intent") or request.query_params.get("opening_intent")
    raw_lead_id = params.get("lead_id") or request.query_params.get("lead_id")
    opening_intent = str(raw_intent) if raw_intent is not None else None
    lead_id = str(raw_lead_id) if raw_lead_id is not None else None

    # Match caller context from active contact form submission if available
    caller_ctx = lookup_caller_context(lead_id) or lookup_caller_context(from_number) or lookup_caller_context(to_number)
    if caller_ctx:
        lead_id = str(caller_ctx.get("id") or lead_id or "")
        if not opening_intent:
            opening_intent = str(caller_ctx.get("intent", "outbound_contact_form"))

    # Log call record if call_id is present
    if call_id:
        call_log = Call(
            call_sid=call_id,
            direction="outbound" if from_number else "inbound",
            from_number=from_number,
            to_number=to_number
        )
        calls_db = load_calls()
        calls_db[call_id] = call_log.to_dict()
        save_calls(calls_db)

    # Determine host for WebSocket URL (Smartflo regex schema requires wss://)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost:8000")
    
    # Smartflo schema strictly requires wss:// pattern (^wss://.+)
    target_sid = call_id or "smartflo_session"
    ws_url = f"wss://{host}/smartflo/media-stream?call_sid={urllib.parse.quote(target_sid)}"
    if opening_intent:
        ws_url += f"&opening_intent={urllib.parse.quote(opening_intent)}"
    if lead_id:
        ws_url += f"&lead_id={urllib.parse.quote(lead_id)}"

    print(f"[Smartflo Dynamic Endpoint] Resolved wss_url: {ws_url} for callId: {call_id}")

    # Return strict Tata Smartflo JSON response schema (supporting both 'sucess' and 'success')
    return JSONResponse(
        status_code=200,
        content={
            "sucess": True,
            "success": True,
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
    lead_id = lead_id or websocket.query_params.get("lead_id")
    opening_intent = opening_intent or websocket.query_params.get("opening_intent")
    print(f"[Smartflo WS] Connection accepted for CallSid: {call_sid} (Lead ID: {lead_id})")

    # Look up full caller context
    caller_ctx = lookup_caller_context(lead_id) or lookup_caller_context(call_sid)
    lead_name = caller_ctx.get("name") if caller_ctx else None
    lead_city = caller_ctx.get("city") if caller_ctx else None
    lead_phone = caller_ctx.get("phone") if caller_ctx else None
    lead_email = caller_ctx.get("email") if caller_ctx else None
    lead_subject = caller_ctx.get("subject") if caller_ctx else None
    lead_message = (caller_ctx.get("message") or caller_ctx.get("notes")) if caller_ctx else None

    if caller_ctx:
        print(f"[Smartflo WS] Successfully bound caller context for {lead_name} (City: {lead_city}, Phone: {lead_phone})")
    else:
        print(f"[Smartflo WS] No pre-existing caller context found for lead_id: {lead_id}")

    orchestrator = None

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event_type = data.get("event")

            if event_type == "connected":
                print("[Smartflo WS] Connected event received from Smartflo.")
                await websocket.send_text(json.dumps({"event": "connected"}))

            elif event_type == "start":
                start_obj = data.get("start", {})
                stream_sid = start_obj.get("streamSid") or data.get("streamSid", "")
                call_sid = start_obj.get("callSid") or call_sid
                print(f"[Smartflo WS] Stream started. StreamSid: {stream_sid}, CallSid: {call_sid}")

                orchestrator = VoicePipelineOrchestrator(
                    websocket=websocket,
                    call_id=call_sid,
                    stream_sid=stream_sid,
                    opening_intent=opening_intent,
                    lead_id=lead_id,
                    lead_name=lead_name,
                    lead_city=lead_city,
                    lead_phone=lead_phone,
                    lead_email=lead_email,
                    lead_subject=lead_subject,
                    lead_message=lead_message,
                    caller_context=caller_ctx
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
