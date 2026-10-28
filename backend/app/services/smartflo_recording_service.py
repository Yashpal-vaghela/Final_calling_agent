"""
Fetch a call recording URL from the Smartflo Call Records API.

GET https://api-smartflo.tatateleservices.com/v1/call/records
  ?from_date=<Y-m-d H:i:s>&to_date=<Y-m-d H:i:s>&call_id=<id>

This module is purely additive: it enriches an existing call record
after hangup and never blocks webhook routing or delivery.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from backend.app.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_DELAY = int(getattr(settings, "SMARTFLO_RECORDING_FETCH_DELAY_SEC", 10))
_MAX_ATTEMPTS = 3
_RETRY_SLEEP = 5


def _window(anchor: datetime, minutes_before: int = 30, minutes_after: int = 15) -> tuple[str, str]:
    """Return from_date and to_date strings straddling the given anchor datetime.
    
    Smartflo expects Y-m-d H:i:s (IST or server time).
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    # Convert to IST (+05:30) if timezone-aware, else treat as UTC converted to IST
    if anchor.tzinfo is not None:
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        ist_time = anchor.astimezone(ist_tz)
    else:
        ist_time = anchor
    start = ist_time - timedelta(minutes=minutes_before)
    end = ist_time + timedelta(minutes=minutes_after)
    return start.strftime(fmt), end.strftime(fmt)


async def fetch_recording_url(
    call_id: Optional[str] = None,
    uuid: Optional[str] = None,
    anchor: Optional[datetime] = None,
    delay_sec: Optional[int] = None,
) -> Optional[str]:
    """
    Fetch the recording_url for a completed call from the Smartflo CDR API.

    Parameters
    ----------
    call_id  : Smartflo call_id (preferred filter).
    uuid     : Smartflo uuid (fallback match).
    anchor   : Approximate call time (used to build date window).
               Defaults to now(timezone.utc) when omitted.
    delay_sec: Seconds to sleep before the first attempt. Defaults to
               SMARTFLO_RECORDING_FETCH_DELAY_SEC (10s).

    Returns
    -------
    The recording URL string, or None if not found or API unavailable.
    """
    token = settings.SMARTFLO_BEARER_TOKEN
    if not token:
        logger.debug("[Recording] SMARTFLO_BEARER_TOKEN not set; skipping CDR fetch.")
        return None

    clean_call_id = str(call_id).strip() if call_id and not str(call_id).startswith("$") else None
    clean_uuid = str(uuid).strip() if uuid and not str(uuid).startswith("$") else None

    if not clean_call_id and not clean_uuid:
        logger.debug("[Recording] Neither valid call_id nor uuid provided; skipping CDR fetch.")
        return None

    base = (getattr(settings, "SMARTFLO_BASE_URL", "") or "https://api-smartflo.tatateleservices.com").rstrip("/")
    url = f"{base}/v1/call/records"
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {token}",
    }

    anchor_time = anchor or datetime.now(timezone.utc)
    from_date, to_date = _window(anchor_time)

    params: dict[str, Any] = {
        "from_date": from_date,
        "to_date": to_date,
        "limit": 50,
    }
    if clean_call_id:
        params["call_id"] = clean_call_id

    wait_initial = _DEFAULT_DELAY if delay_sec is None else delay_sec
    if wait_initial > 0:
        logger.debug("[Recording] Waiting %ss before first CDR fetch for call_id=%s uuid=%s", wait_initial, clean_call_id, clean_uuid)
        await asyncio.sleep(wait_initial)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            logger.info(
                "[Recording] Fetching CDR records (attempt %d/%d) url=%s call_id=%s uuid=%s",
                attempt, _MAX_ATTEMPTS, url, clean_call_id, clean_uuid
            )
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers, params=params)

            if resp.status_code not in (200, 201):
                logger.warning(
                    "[Recording] CDR API returned HTTP %s (attempt %d/%d): %s",
                    resp.status_code, attempt, _MAX_ATTEMPTS, resp.text[:200]
                )
            else:
                data = resp.json()
                results = data.get("results") if isinstance(data, dict) else []
                if isinstance(results, list):
                    for rec in results:
                        if not isinstance(rec, dict):
                            continue
                        rec_cid = str(rec.get("call_id") or "").strip()
                        rec_uuid = str(rec.get("uuid") or "").strip()
                        rec_url = rec.get("recording_url")

                        matched = False
                        if clean_call_id and rec_cid == clean_call_id:
                            matched = True
                        elif clean_uuid and rec_uuid == clean_uuid:
                            matched = True

                        if matched and isinstance(rec_url, str) and rec_url.strip() and not rec_url.strip().startswith("$"):
                            found_url = rec_url.strip()
                            logger.info("[Recording] Successfully fetched recording_url for call_id=%s uuid=%s: %s", clean_call_id, clean_uuid, found_url)
                            return found_url

                logger.debug(
                    "[Recording] recording_url not yet available in CDR response (attempt %d/%d, records returned: %d)",
                    attempt, _MAX_ATTEMPTS, len(results) if isinstance(results, list) else 0
                )
        except Exception as e:
            logger.exception("[Recording] Error querying CDR API (attempt %d/%d): %s", attempt, _MAX_ATTEMPTS, e)

        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_SLEEP)

    logger.info("[Recording] recording_url not found after %d attempts for call_id=%s uuid=%s", _MAX_ATTEMPTS, clean_call_id, clean_uuid)
    return None


async def enrich_recording(
    attempt_id: str,
    call_id: Optional[str] = None,
    uuid: Optional[str] = None,
    anchor: Optional[datetime] = None,
    delay_sec: Optional[int] = None,
) -> None:
    """
    Fire-and-forget coroutine: fetches the recording URL from Smartflo CDR
    and updates the call record in the durable store.
    """
    if not attempt_id:
        return
    try:
        recording_url = await fetch_recording_url(call_id=call_id, uuid=uuid, anchor=anchor, delay_sec=delay_sec)
        if not recording_url:
            return
        from backend.app.services.call_record_service import get_call_store
        store = get_call_store()
        await asyncio.to_thread(_patch_recording, store, attempt_id, recording_url)
    except Exception:
        logger.exception("[Recording] enrich_recording failed unexpectedly for attempt_id=%s", attempt_id)


def _patch_recording(store, attempt_id: str, recording_url: str) -> None:
    """Synchronous helper using the store method to patch recording and queue Zoho delivery."""
    try:
        if hasattr(store, "save_recording_url"):
            updated = store.save_recording_url(attempt_id, recording_url)
            if updated:
                logger.info("[Recording] Patched recording_url for attempt_id=%s and queued delivery", attempt_id)
                print(f"[Recording] 🎙️ Recording URL: {recording_url}")
            return

        with store._document() as doc:
            rec = doc.get("calls", {}).get(attempt_id)
            if rec is None:
                logger.warning("[Recording] attempt_id=%s not found in call store; cannot patch recording_url", attempt_id)
                return
            existing = rec.get("recording_url") or ""
            if existing and not str(existing).startswith("$"):
                logger.debug("[Recording] Valid recording_url already exists for %s; skipping overwrite", attempt_id)
                return
            rec["recording_url"] = recording_url
            rec.setdefault("timestamps", {})["recording_enriched_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("[Recording] Patched recording_url for attempt_id=%s in call store", attempt_id)
            print(f"[Recording] 🎙️ Recording URL: {recording_url}")
    except Exception:
        logger.exception("[Recording] Failed to patch recording_url into store for attempt_id=%s", attempt_id)
