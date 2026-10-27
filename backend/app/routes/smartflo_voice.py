import json
import asyncio
import urllib.parse
from typing import Optional, Any
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse

from backend.app.services.smartflo_service import smartflo_client
from backend.app.services.caller_context import (
    lookup_caller_context,
    async_lookup_caller_context,
    remove_caller_context,
    pop_pending_caller_context_by_phone,
    ACTIVE_CALLER_CONTEXTS
)
from agent.audio.greeting_cache import remove_dynamic_greeting
from agent.pipeline import VoicePipelineOrchestrator
from agent.utils.summarizer import (
    generate_call_summary,
    save_call_record,
    find_call_record,
)
from backend.app.services.zoho_service import send_call_summary_to_zoho
from backend.app.services.call_record_service import get_call_store, provider_ids, now_iso, CorrelationError
from backend.app.services.outbound_call_service import initiate_tracked_call

router = APIRouter()


async def _attempt_context(attempt_id: str):
    context = lookup_caller_context(attempt_id)
    if context and context.get("attempt_id") == attempt_id:
        return context
    record = await asyncio.to_thread(get_call_store().get_attempt, attempt_id)
    return record.get("user_info") if record else None

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
    print("[SMARTFLO WEBHOOK] >>> LOCAL SMARTFLO HTTP WEBHOOK HIT <<<")
    print(f"[LOCAL RECEIVER PROOF] method={request.method}")
    print(f"[LOCAL RECEIVER PROOF] url={request.url}")
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
    
    raw_intent = params.get("opening_intent") or params.get("intent") or request.query_params.get("opening_intent")
    raw_lead_id = params.get("lead_id") or params.get("leadId") or request.query_params.get("lead_id")
    opening_intent = str(raw_intent) if raw_intent is not None else None
    lead_id = str(raw_lead_id) if raw_lead_id is not None else None

    # Hangup notifications belong to the authenticated status receiver.
    if "end_stamp" in params or "hangup_cause_key" in params:
        raise HTTPException(400, "Configure lifecycle callbacks at /api/smartflo/webhook")

    ids = provider_ids(params)
    attempt_id = await asyncio.to_thread(get_call_store().bind_identifiers, ids) if ids else None
    caller_ctx = None
    if attempt_id:
        caller_ctx = lookup_caller_context(attempt_id)
        if not caller_ctx:
            tracked = await asyncio.to_thread(get_call_store().get_attempt, attempt_id)
            caller_ctx = tracked.get("user_info") if tracked else None

    # 1. If explicit lead_id is provided, look up by lead_id first
    if lead_id and not caller_ctx:
        caller_ctx = lookup_caller_context(lead_id)

    # 2. If no lead_id (standard Smartflo dynamic webhook), pop from pending FIFO queue for to_number
    if not caller_ctx and to_number:
        caller_ctx = pop_pending_caller_context_by_phone(to_number)

    # 3. Fallback to candidate keys in RAM L1
    if not caller_ctx:
        candidates = [k for k in [lead_id, to_number, from_number] if k and str(k).strip() and str(k).lower() != "none"]
        for cand in candidates:
            caller_ctx = lookup_caller_context(cand)
            if caller_ctx:
                break

    if not caller_ctx and lead_id:
        caller_ctx = await async_lookup_caller_context(lead_id)

    if caller_ctx:
        lead_id = str(caller_ctx.get("id") or lead_id or "")
        if not opening_intent:
            opening_intent = str(caller_ctx.get("intent", "outbound_contact_form"))
        if call_id:
            ACTIVE_CALLER_CONTEXTS[call_id] = caller_ctx
            print(f"[Smartflo Dynamic Endpoint] Linked callId '{call_id}' to lead '{caller_ctx.get('name')}' (Phone: {caller_ctx.get('phone')}, Lead ID: {lead_id})")

    if not attempt_id and caller_ctx:
        attempt_id = caller_ctx.get("attempt_id")
    if attempt_id:
        await asyncio.to_thread(get_call_store().bind_identifiers, ids, attempt_id)

    # Determine host for WebSocket URL (Smartflo regex schema requires wss://)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost:8000")
    
    # Smartflo schema strictly requires wss:// pattern (^wss://.+)
    # Encode lead_id into call_sid to survive Smartflo URL truncation
    target_sid = call_id or "smartflo_session"
    if lead_id:
        composite_sid = f"{target_sid}__lead__{lead_id}"
    else:
        composite_sid = target_sid

    if attempt_id:
        composite_sid += f"__attempt__{attempt_id}"
    ws_url = f"wss://{host}/smartflo/media-stream?call_sid={urllib.parse.quote(composite_sid)}"
    if attempt_id:
        ws_url += f"&attempt_id={urllib.parse.quote(attempt_id)}"
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
    lead_id: Optional[str] = None,
    attempt_id: Optional[str] = None
):
    """
    Bi-directional audio streaming WebSocket endpoint for Tata Smartflo.
    Exchanges 8kHz µ-law audio chunks and lifecycle events with VoicePipelineOrchestrator.
    """
    print("[SMARTFLO WEBSOCKET] >>> LOCAL SMARTFLO WEBSOCKET HIT <<<")
    print(f"[LOCAL RECEIVER PROOF] ws_url={websocket.url}")

    await websocket.accept()
    raw_query_sid = websocket.query_params.get("call_sid", "")
    call_sid = call_sid or raw_query_sid
    query_lead_id = websocket.query_params.get("lead_id")
    lead_id = lead_id or query_lead_id
    opening_intent = opening_intent or websocket.query_params.get("opening_intent")

    attempt_id = attempt_id or websocket.query_params.get("attempt_id")
    if "__attempt__" in call_sid:
        call_sid, embedded_attempt = call_sid.split("__attempt__", 1)
        attempt_id = attempt_id or embedded_attempt

    # Unpack composite call_sid if lead_id was encoded into call_sid
    if "__lead__" in call_sid:
        parts = call_sid.split("__lead__", 1)
        call_sid = parts[0]
        if not lead_id:
            lead_id = parts[1]

    print(f"[Smartflo WS] Connection accepted for CallSid: {call_sid} (Lead ID: {lead_id})")

    # Look up full caller context strictly in RAM L1 (0.001ms, non-blocking)
    caller_ctx = await _attempt_context(attempt_id) if attempt_id else None
    if not attempt_id:
        candidates = [k for k in [lead_id, call_sid] if k and k.strip() and k.lower() != "none"]
        for cand in candidates:
            caller_ctx = lookup_caller_context(cand)
            if caller_ctx:
                break
    if caller_ctx and not lead_id:
        lead_id = caller_ctx.get("id")
    if not opening_intent and caller_ctx:
        opening_intent = str(caller_ctx.get("intent", "outbound_contact_form"))

    lead_name = caller_ctx.get("name") if caller_ctx else None
    lead_city = caller_ctx.get("city") if caller_ctx else None
    lead_phone = caller_ctx.get("phone") if caller_ctx else None
    lead_email = caller_ctx.get("email") if caller_ctx else None
    lead_pincode = caller_ctx.get("pincode") if caller_ctx else None
    lead_subject = caller_ctx.get("subject") if caller_ctx else None
    lead_message = (caller_ctx.get("message") or caller_ctx.get("notes")) if caller_ctx else None

    if caller_ctx:
        print(f"[Smartflo WS] Successfully bound caller context for {lead_name} (City: {lead_city}, Phone: {lead_phone}, Pincode: {lead_pincode})")
    else:
        print(f"[Smartflo WS] Initial check: No pre-existing caller context found for call_sid: '{call_sid}' (Will check start payload)")

    orchestrator = None
    media_log_count = 0

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
                attempt_id = attempt_id or custom_params.get("attempt_id")
                if not attempt_id and caller_ctx:
                    attempt_id = caller_ctx.get("attempt_id")
                attempt_id = await asyncio.to_thread(
                    get_call_store().record_media_start, call_sid, attempt_id
                ) or attempt_id
                custom_intent = custom_params.get("opening_intent") or custom_params.get("intent")
                if attempt_id:
                    caller_ctx = await _attempt_context(attempt_id)

                # If caller context wasn't resolved at WS query param time, resolve it now from start payload
                if not caller_ctx and not attempt_id:
                    raw_cands = [query_lead_id, custom_lead_id, lead_id, from_num_stripped, to_num_stripped, call_sid]
                    cands = list(dict.fromkeys([c for c in raw_cands if c and str(c).strip() and str(c).lower() != "none"]))
                    for cand in cands:
                        caller_ctx = lookup_caller_context(cand)
                        if caller_ctx:
                            break

                    # Fast async fallback if lead_id was supplied
                    if not caller_ctx and (custom_lead_id or query_lead_id or lead_id):
                        primary_lead = custom_lead_id or query_lead_id or lead_id
                        caller_ctx = await async_lookup_caller_context(primary_lead)
                    
                    print(f"[DEBUG CRITICAL] WS Payload customParams: {custom_params} | Lookup Result: {'SUCCESS' if caller_ctx else 'FAILED'}")
                    
                if caller_ctx:
                    lead_id = str(caller_ctx.get("id") or lead_id or custom_lead_id or "")
                    opening_intent = str(caller_ctx.get("intent") or opening_intent or custom_intent or "outbound_contact_form")
                    lead_name = caller_ctx.get("name")
                    lead_city = caller_ctx.get("city")
                    lead_phone = caller_ctx.get("phone")
                    lead_email = caller_ctx.get("email")
                    lead_pincode = caller_ctx.get("pincode")
                    lead_subject = caller_ctx.get("subject")
                    lead_message = caller_ctx.get("message") or caller_ctx.get("notes")

                if caller_ctx and call_sid:
                    try:
                        from agent.cache import cache_set, build_call_context_key, TTL_CALL_CONTEXT
                        asyncio.create_task(cache_set(build_call_context_key(call_sid), caller_ctx, ttl_seconds=TTL_CALL_CONTEXT))
                    except Exception as e:
                        print(f"[Smartflo WS Warning] Could not persist context to Redis: {e}")

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
                    lead_pincode=lead_pincode,
                    caller_context=caller_ctx
                )
                await orchestrator.start()

            elif event_type == "media":
                if orchestrator is not None:
                    media_obj = data.get("media", {})
                    # Low-volume diagnostic logging for the first 5 media events to inspect metadata keys
                    if media_log_count < 5:
                        media_log_count += 1
                        meta_info = {k: v for k, v in media_obj.items() if k != "payload"}
                        print(f"[Smartflo WS Media Diagnostic #{media_log_count}] top_keys={list(data.keys())}, media_meta={meta_info}")
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
            # Snapshot conversation history, collected user entities, and call identifiers
            history_snapshot = list(orchestrator.session.conversation_history)
            user_info_snapshot = dict(orchestrator.session.collected_user_info)
            call_id_val = orchestrator.call_id
            intake_lead_id_val = orchestrator.lead_id or lead_id
            booking_lead_id_val = getattr(orchestrator, "booking_lead_id", None)

            await orchestrator.stop()

            # Trigger structured conversation summarization asynchronously in the background
            async def _run_post_call_summary():
                from datetime import datetime, timezone
                media_ended_at = datetime.now(timezone.utc).isoformat()
                try:
                    summary = await generate_call_summary(
                        history_snapshot,
                        user_info_snapshot,
                        lead_id=intake_lead_id_val,
                        booking_lead_id=booking_lead_id_val
                    )
                    # Conversation updates cannot change provider status or causes.
                    await asyncio.to_thread(save_call_record,
                        call_id=call_id_val,
                        summary=summary,
                        transcript=history_snapshot,
                        lead_id=intake_lead_id_val,
                        booking_lead_id=booking_lead_id_val,
                        attempt_id=attempt_id,
                        user_info=user_info_snapshot,
                        timestamps={"media_ended_at": media_ended_at}
                    )
                    if summary:
                        print(f"📋 [Call Summary for {call_id_val}]:\n{json.dumps(summary, indent=2)}")
                        await send_call_summary_to_zoho(
                            call_id=call_id_val,
                            user_info=user_info_snapshot,
                            summary=summary,
                            transcript_history=history_snapshot,
                            lead_id=intake_lead_id_val,
                            booking_lead_id=booking_lead_id_val
                        )
                except Exception as ex:
                    print(f"[Smartflo WS] Error generating post-call summary for {call_id_val}: {ex}")
                    await asyncio.to_thread(save_call_record,
                        call_id=call_id_val,
                        summary=None,
                        transcript=history_snapshot,
                        lead_id=intake_lead_id_val,
                        booking_lead_id=booking_lead_id_val,
                        attempt_id=attempt_id,
                        user_info=user_info_snapshot,
                        timestamps={"media_ended_at": media_ended_at}
                    )

            asyncio.create_task(_run_post_call_summary())
        
        # Purge caller context and dynamic prewarmed greeting from memory across all indexed keys on call termination
        phone_to_clean = lead_phone or (caller_ctx.get("phone") if caller_ctx else None)
        remove_caller_context(lead_id=lead_id, phone=phone_to_clean, call_id=call_sid, attempt_id=attempt_id)
        remove_dynamic_greeting(lead_id=lead_id, phone=phone_to_clean)
        print(f"[Smartflo WS] Cleaned up stream and purged caller context & dynamic greeting from memory for CallSid: {call_sid} (Lead ID: {lead_id})")
        try:
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass  # Peer or ASGI server already closed the transport.


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
        custom_params["lead_id"] = str(lead_id).strip()
    if opening_intent:
        custom_params["opening_intent"] = opening_intent

    context = lookup_caller_context(lead_id) if lead_id else None
    attempt_id, result = await initiate_tracked_call(
        smartflo_client, customer_number, lead_id=lead_id, source="api_smartflo_outbound",
        custom_params=custom_params, caller_context=dict(context) if context else None,
    )
    return {**result, "attempt_id": attempt_id}


@router.get("/api/calls")
async def list_call_records():
    """Return provider records and unchanged, explicitly identified legacy history."""
    return {"calls": await asyncio.to_thread(get_call_store().list_records)}


@router.get("/api/call-attempts/{attempt_id}")
async def get_call_attempt(attempt_id: str):
    rec = await asyncio.to_thread(get_call_store().get_attempt, attempt_id)
    if rec is None:
        raise HTTPException(404, "Call attempt not found")
    return rec


@router.get("/api/calls/{identifier}/summary")
@router.get("/api/leads/{identifier}/summary")
async def get_call_summary(identifier: str):
    rec = await asyncio.to_thread(find_call_record, identifier)
    if rec is None:
        raise HTTPException(404, "Call record not found")
    return rec
