"""Shared request bookkeeping for all outbound entrypoints; never assigns status."""
import asyncio
from copy import deepcopy
from typing import Any

from backend.app.services.call_record_service import get_call_store, provider_ids


async def initiate_tracked_call(client, customer_number: str, *, lead_id: str | None,
                                source: str, custom_params: dict[str, Any] | None = None,
                                caller_context: dict | None = None) -> tuple[str, dict]:
    store = get_call_store()
    aid = await asyncio.to_thread(store.create_attempt, lead_id, customer_number, source, caller_context)
    params = dict(custom_params or {})
    params["attempt_id"] = aid
    custom = dict(params.get("custom_identifier") or {})
    custom["attempt_id"] = aid
    params["custom_identifier"] = custom
    if caller_context is not None:
        # Form queues reference this context. Callback callers pass a copy, so an
        # earlier call's identity is not changed by another attempt for that lead.
        caller_context["attempt_id"] = aid
        from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS
        ACTIVE_CALLER_CONTEXTS[aid] = caller_context
        from agent.cache import cache_set, build_call_context_key, TTL_CALL_CONTEXT
        await cache_set(build_call_context_key(aid), caller_context, ttl_seconds=TTL_CALL_CONTEXT)
        # Connect Gemini Live before dialing so it is ready when the customer answers
        from backend.app.services.prewarm_service import start_prewarm
        await start_prewarm(aid, caller_context, lead_id=lead_id, opening_intent=params.get("opening_intent"))
    try:
        result = await client.initiate_click_to_call(customer_number=customer_number, custom_params=params)
        if not isinstance(result, dict):
            result = {"success": False, "error": "Smartflo returned an invalid response"}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    await asyncio.to_thread(store.save_request_result, aid, result)
    if caller_context is not None:
        from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS
        nested = result.get("data") if isinstance(result.get("data"), dict) else {}
        for value in {**provider_ids(nested), **provider_ids(result)}.values():
            ACTIVE_CALLER_CONTEXTS[value] = caller_context
    return aid, deepcopy(result)
