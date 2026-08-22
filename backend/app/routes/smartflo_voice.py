import json
import os
import asyncio
import urllib.parse
from typing import Optional, Any
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, Response

from backend.app.models.call import Call
from backend.app.services.smartflo_service import smartflo_client
from backend.app.routes.contact_form import lookup_caller_context, remove_caller_context, ACTIVE_CALLER_CONTEXTS
from agent.utils.call_logger import save_calls, load_calls
from agent.pipeline import VoicePipelineOrchestrator

router = APIRouter()

@router.api_route("/smartflo/voice", methods=["GET", "POST"])
@router.api_route("/", methods=["POST"])
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
    print("🚨🚨🚨 LOCAL SMARTFLO HTTP WEBHOOK HIT 🚨🚨🚨")
    print(f"[LOCAL RECEIVER PROOF] method={request.method}")
    print(f"[LOCAL RECEIVER PROOF] url={request.url}")
    print(f"[LOCAL RECEIVER PROOF] headers={dict(request.headers)}")
    print(f"[LOCAL RECEIVER PROOF] query_params={dict(request.query_params)}")

    # Parse parameters from query parameters, JSON body, and Form data
    params: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        try:
            body_json = await request.json()
            if isinstance(body_json, dict):
                params.update(body_json)
        except Exception:
            try:
                form = await request.form()
                params.update({k: str(v) for k, v in form.items()})
            except Exception:
                pass

    print(f"[Smartflo Dynamic Endpoint Debug] ALL incoming params: {json.dumps(params)}")
    print(f"[Smartflo Dynamic Endpoint Debug] Headers: {dict(request.headers)}")

    call_id = (
        params.get("callId") or params.get("$callId") or
        params.get("call_id") or params.get("callSid") or params.get("$callSid") or ""
    )
    from_number = (
        params.get("fromNumber") or params.get("$fromNumber") or
        params.get("from") or params.get("$from") or ""
    )
    to_number = (
        params.get("toNumber") or params.get("$toNumber") or
        params.get("to") or params.get("$to") or params.get("customer_number") or ""
    )
    status = params.get("status") or params.get("$status") or "ringing"
    
    raw_intent = params.get("opening_intent") or params.get("intent") or request.query_params.get("opening_intent")
    raw_lead_id = params.get("lead_id") or params.get("leadId") or request.query_params.get("lead_id")
    opening_intent = str(raw_intent) if raw_intent is not None else None
    lead_id = str(raw_lead_id) if raw_lead_id is not None else None

    # Match caller context from active contact form submission if available (exact lookup by lead_id, to_number, or from_number)
    caller_ctx = (
        lookup_caller_context(lead_id)
        or lookup_caller_context(to_number)
        or lookup_caller_context(from_number)
    )
    if caller_ctx:
        lead_id = str(caller_ctx.get("id") or lead_id or "")
        if not opening_intent:
            opening_intent = str(caller_ctx.get("intent", "outbound_contact_form"))
        if call_id:
            ACTIVE_CALLER_CONTEXTS[call_id] = caller_ctx
            print(f"[Smartflo Dynamic Endpoint] Linked callId '{call_id}' to lead '{caller_ctx.get('name')}' (Phone: {caller_ctx.get('phone')}, Lead ID: {lead_id})")

    # Log call record if call_id is present
    # if call_id:
    #     call_log = Call(
    #         call_sid=call_id,
    #         direction="outbound" if from_number else "inbound",
    #         from_number=from_number,
    #         to_number=to_number
    #     )
    #     calls_db = load_calls()
    #     calls_db[call_id] = call_log.to_dict()
    #     save_calls(calls_db)

    # Determine host for WebSocket URL (Smartflo regex schema requires wss://)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost:8000")
    
    # Smartflo schema strictly requires wss:// pattern (^wss://.+)
    # Encode lead_id into call_sid to survive Smartflo URL truncation
    target_sid = call_id or "smartflo_session"
    if lead_id:
        composite_sid = f"{target_sid}__lead__{lead_id}"
    else:
        composite_sid = target_sid

    ws_url = f"wss://{host}/smartflo/media-stream?call_sid={urllib.parse.quote(composite_sid)}"
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
    print("🚨🚨🚨 LOCAL SMARTFLO WEBSOCKET HIT 🚨🚨🚨")
    print(f"[LOCAL RECEIVER PROOF] ws_url={websocket.url}")
    print(f"[LOCAL RECEIVER PROOF] headers={dict(websocket.headers)}")

    await websocket.accept()
    raw_query_sid = websocket.query_params.get("call_sid", "")
    call_sid = call_sid or raw_query_sid
    query_lead_id = websocket.query_params.get("lead_id")
    lead_id = lead_id or query_lead_id
    opening_intent = opening_intent or websocket.query_params.get("opening_intent")

    # Unpack composite call_sid if lead_id was encoded into call_sid
    if "__lead__" in call_sid:
        parts = call_sid.split("__lead__", 1)
        call_sid = parts[0]
        if not lead_id:
            lead_id = parts[1]

    print(f"[Smartflo WS] Connection accepted for CallSid: {call_sid} (Lead ID: {lead_id})")

    # Look up full caller context strictly by exact keys
    caller_ctx = (
        lookup_caller_context(lead_id)
        or lookup_caller_context(call_sid)
    )
    if not opening_intent and caller_ctx:
        opening_intent = str(caller_ctx.get("intent", "outbound_contact_form"))

    lead_name = caller_ctx.get("name") if caller_ctx else None
    lead_city = caller_ctx.get("city") if caller_ctx else None
    lead_phone = caller_ctx.get("phone") if caller_ctx else None
    lead_email = caller_ctx.get("email") if caller_ctx else None
    lead_subject = caller_ctx.get("subject") if caller_ctx else None
    lead_message = (caller_ctx.get("message") or caller_ctx.get("notes")) if caller_ctx else None

    if caller_ctx:
        print(f"[Smartflo WS] Successfully bound caller context for {lead_name} (City: {lead_city}, Phone: {lead_phone})")
    else:
        print(f"[Smartflo WS] Initial check: No pre-existing caller context found for call_sid: '{call_sid}' (Will check start payload)")

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
                print(f"[Smartflo WS] Raw start event payload: {json.dumps(start_obj)}")
                
                stream_sid = start_obj.get("streamSid") or data.get("streamSid", "")
                call_sid = start_obj.get("callSid") or call_sid
                from_num = start_obj.get("from")
                to_num = start_obj.get("to")
                
                from_num_stripped = from_num.replace("+", "") if from_num else None
                to_num_stripped = to_num.replace("+", "") if to_num else None
                
                custom_params = start_obj.get("customParameters", {}) or {}
                
                print(f"[Smartflo WS] Extracted customParameters: {custom_params}")
                
                custom_lead_id = custom_params.get("lead_id") or custom_params.get("leadId")
                custom_intent = custom_params.get("opening_intent") or custom_params.get("intent")

                print(f"[Smartflo WS Debug] lead_id exists in customParameters: {'lead_id' in custom_params or 'leadId' in custom_params}")
                print(f"[Smartflo WS Debug] exact values passed to lookup: custom_lead_id={custom_lead_id}, lead_id={lead_id}, from_num={from_num_stripped}, to_num={to_num_stripped}, call_sid={call_sid}")
                print(f"[Smartflo WS Debug] ACTIVE_CALLER_CONTEXTS keys: {list(ACTIVE_CALLER_CONTEXTS.keys())}")

                # If caller context wasn't resolved at WS query param time, resolve it now from start payload
                if not caller_ctx:
                    caller_ctx = (
                        lookup_caller_context(query_lead_id)
                        or lookup_caller_context(custom_lead_id)
                        or lookup_caller_context(lead_id)
                        or lookup_caller_context(from_num_stripped)
                        or lookup_caller_context(to_num_stripped)
                        or lookup_caller_context(call_sid)
                    )
                    
                    print(f"[DEBUG CRITICAL] WS Payload customParams: {custom_params} | Lookup Tried: {[query_lead_id, custom_lead_id, from_num_stripped, to_num_stripped]} | Active Memory Keys: {list(ACTIVE_CALLER_CONTEXTS.keys())} | Lookup Result: {'SUCCESS' if caller_ctx else 'FAILED'}")
                    
                    if caller_ctx:
                        lead_id = str(caller_ctx.get("id") or lead_id or custom_lead_id or "")
                        opening_intent = str(caller_ctx.get("intent") or opening_intent or custom_intent or "outbound_contact_form")
                        lead_name = caller_ctx.get("name")
                        lead_city = caller_ctx.get("city")
                        lead_phone = caller_ctx.get("phone")
                        lead_email = caller_ctx.get("email")
                        lead_subject = caller_ctx.get("subject")
                        lead_message = caller_ctx.get("message") or caller_ctx.get("notes")
                        print(f"[Smartflo WS] Resolved caller context on start event for {lead_name} (City: {lead_city}, Phone: {lead_phone})")

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
        
        # Purge caller context from memory across all indexed keys on call termination
        phone_to_clean = lead_phone or (caller_ctx.get("phone") if caller_ctx else None)
        remove_caller_context(lead_id=lead_id, phone=phone_to_clean, call_id=call_sid)
        print(f"[Smartflo WS] Cleaned up stream and purged caller context from memory for CallSid: {call_sid} (Lead ID: {lead_id})")


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
