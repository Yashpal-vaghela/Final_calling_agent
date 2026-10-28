"""Durable call metadata. Only authenticated provider events can supply call status.

The legacy calls.json is read-only. A single locked, atomically replaced document
contains new records, unmatched evidence, and the Zoho outbox, including on Windows.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterator
import uuid

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
TRIGGERS = {"customer_answered", "hangup_answered", "hangup_missed"}
CAUSE_FIELDS = (
    "hangup_cause_key", "hangup_cause_code", "hangup_cause_description",
    "reason_key", "hangup_cause", "sip_response_code",
)
# Validation only: these descriptions never become generated statuses or reasons.
Q850_KEYS = {1: "UNALLOCATED_NUMBER", 16: "NORMAL_CLEARING", 17: "USER_BUSY",
            18: "NO_USER_RESPONSE", 19: "NO_ANSWER", 20: "SUBSCRIBER_ABSENT",
            21: "CALL_REJECTED", 27: "DESTINATION_OUT_OF_ORDER", 28: "INVALID_NUMBER_FORMAT"}
if os.name == "nt":
    import msvcrt
    fcntl = None
else:
    msvcrt = None
    try:
        import fcntl
    except ImportError:
        fcntl = None

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_tls = threading.local()


class CallStoreError(RuntimeError):
    pass


class CorrelationError(CallStoreError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(value: Any) -> str | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value if value and not value.startswith("$") and value.lower() != "none" else None


def provider_ids(payload: dict) -> dict[str, str]:
    custom = payload.get("custom_identifier")
    custom = custom if isinstance(custom, dict) else {}
    aliases = {"attempt_id": (payload.get("attempt_id"), custom.get("attempt_id")),
               "ref_id": (payload.get("ref_id"),),
               "call_id": (payload.get("call_id"), payload.get("callId"), payload.get("$callId")),
               "uuid": (payload.get("uuid"), payload.get("call_uuid")),
               "media_call_id": (payload.get("callSid"), payload.get("call_sid"))}
    return {kind: next(v for x in values if (v := identifier(x)))
            for kind, values in aliases.items() if any(identifier(x) for x in values)}


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip() or value.startswith("$"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return parsed.timestamp()
    except ValueError:
        return None


def _provider_value(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and not value.strip().startswith("$"))


def agent_status_for(trigger: str, payload: dict) -> str:
    """Status name sent to the AI-agent webhook for a Smartflo trigger."""
    if trigger == "customer_answered":
        return "answered"
    if trigger == "hangup_answered":
        return "completed"
    key = payload.get("hangup_cause_key", payload.get("hangupcause_key"))
    key = key.strip().upper() if isinstance(key, str) and _provider_value(key) else ""
    if key == "USER_BUSY":
        return "busy"
    if key in ("NO_ANSWER", "NO_USER_RESPONSE"):
        return "no_answer"
    return "failed"


class CallRecordStore:
    def __init__(self, path: str | Path, legacy_path: str | Path | None = None):
        self.path = Path(path).resolve()
        self.legacy_path = Path(legacy_path or ROOT / "data/calls.json").resolve()
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.path), threading.RLock())

    @contextmanager
    def _document(self, write: bool = True) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        store_key = str(self.path)
        active_docs = getattr(_tls, "active_docs", None)
        if active_docs is None:
            active_docs = {}
            _tls.active_docs = active_docs

        if store_key in active_docs:
            # Re-entrant call in the same thread: reuse active in-memory document to avoid Windows file-locking deadlocks
            yield active_docs[store_key]
            return

        with self._lock:
            lock_path = self.path.with_suffix(self.path.suffix + ".lock")
            with open(lock_path, "a+b") as lock:
                if os.fstat(lock.fileno()).st_size == 0:
                    lock.write(b"\0")
                    lock.flush()
                deadline = time.monotonic() + 10
                while True:
                    try:
                        lock.seek(0)
                        if os.name == "nt":
                            if msvcrt is not None:
                                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            if fcntl is not None:
                                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise CallStoreError("Timed out acquiring call-store lock") from exc
                        time.sleep(.02)
                try:
                    # 1. Read document from disk
                    doc: dict[str, Any]
                    if self.path.exists():
                        try:
                            with open(self.path, encoding="utf-8") as source:
                                loaded = json.load(source)
                        except (OSError, ValueError) as exc:
                            raise CallStoreError("Cannot read or persist call store; existing data was not reset") from exc
                        if not isinstance(loaded, dict) or loaded.get("version") != 1:
                            raise CallStoreError("Unsupported call-store format")
                        for key in ("calls", "events", "deliveries", "orphan_summaries"):
                            if not isinstance(loaded.get(key), dict):
                                raise CallStoreError("Invalid call-store structure")
                        doc = loaded
                    else:
                        doc = {"version": 1, "sequence": 0, "calls": {}, "events": {},
                               "deliveries": {}, "orphan_summaries": {}}

                    active_docs[store_key] = doc
                    try:
                        # 2. Yield document to caller (do not mask caller's business exceptions)
                        yield doc
                    finally:
                        active_docs.pop(store_key, None)

                    # 3. Write document to disk if requested
                    if write:
                        temp_name = None
                        try:
                            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as target:
                                temp_name = target.name
                                json.dump(doc, target, ensure_ascii=False, indent=2)
                                target.flush()
                                os.fsync(target.fileno())
                            for attempt in range(5):
                                try:
                                    os.replace(temp_name, self.path)
                                    break
                                except PermissionError:
                                    if attempt == 4:
                                        raise
                                    time.sleep(0.05 * (attempt + 1))
                            if os.name != "nt":
                                directory = os.open(self.path.parent, os.O_RDONLY)
                                try:
                                    os.fsync(directory)
                                finally:
                                    os.close(directory)
                        except (OSError, ValueError) as exc:
                            raise CallStoreError("Cannot read or persist call store; existing data was not reset") from exc
                        finally:
                            if temp_name and os.path.exists(temp_name):
                                try:
                                    os.unlink(temp_name)
                                except OSError:
                                    pass
                finally:
                    try:
                        lock.seek(0)
                        if os.name == "nt":
                            if msvcrt is not None:
                                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            if fcntl is not None:
                                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass

    def create_attempt(self, lead_id: str | None, customer_number: str, source: str,
                       user_info: dict | None = None) -> str:
        attempt_id = uuid.uuid4().hex
        with self._document() as doc:
            doc["calls"][attempt_id] = {
                "attempt_id": attempt_id, "call_id": None, "lead_id": identifier(lead_id),
                "customer_number": customer_number, "source": source,
                "record_source": "smartflo", "created_at": now_iso(),
                "identifiers": {}, "call_status": None, "call_status_source": None,
                "summary": None, "transcript_turn_count": 0, "timestamps": {},
                "user_info": deepcopy(user_info or {}), "webhook_event_ids": [],
                **{key: None for key in CAUSE_FIELDS},
            }
        return attempt_id

    def _resolve(self, doc: dict, ids: dict) -> str | None:
        matches = set()
        aid = ids.get("attempt_id")
        if aid in doc["calls"]:
            matches.add(aid)
        for kind, value in ids.items():
            if kind == "attempt_id":
                continue
            for key, rec in doc["calls"].items():
                if value in rec["identifiers"].get(kind, []):
                    matches.add(key)
        if len(matches) > 1:
            raise CorrelationError("Provider identifiers refer to different attempts")
        # An explicit unknown attempt must not be silently replaced by another.
        if aid and matches and aid not in matches:
            raise CorrelationError("Explicit attempt does not match provider identifiers")
        return next(iter(matches), None)

    def _attach(self, doc: dict, aid: str, ids: dict):
        resolved = self._resolve(doc, ids)
        if resolved and resolved != aid:
            raise CorrelationError("Identifier already belongs to another attempt")
        rec = doc["calls"][aid]
        for kind, value in ids.items():
            if kind == "attempt_id":
                continue
            values = rec["identifiers"].setdefault(kind, [])
            if value not in values:
                values.append(value)
            if kind in ("ref_id", "media_call_id"):
                rec[kind] = value
            if kind == "call_id" or (kind == "media_call_id" and not rec.get("call_id")):
                rec["call_id"] = value
        for media_id in rec["identifiers"].get("media_call_id", []):
            orphan = doc["orphan_summaries"].pop(media_id, None)
            if orphan:
                self._merge_summary(rec, orphan)

    def bind_identifiers(self, ids: dict, attempt_id: str | None = None) -> str | None:
        ids = {key: value for key, raw in ids.items() if (value := identifier(raw))}
        if attempt_id:
            ids["attempt_id"] = attempt_id
        with self._document() as doc:
            aid = self._resolve(doc, ids)
            if aid:
                self._attach(doc, aid, ids)
                self._reconcile(doc)
            return aid

    def save_request_result(self, aid: str, result: dict):
        with self._document() as doc:
            rec = doc["calls"][aid]
            rec["request_response"] = deepcopy(result)
            rec["request_response_received_at"] = now_iso()
            nested = result.get("data")
            ids = provider_ids(nested if isinstance(nested, dict) else {})
            ids.update(provider_ids(result))
            ids["attempt_id"] = aid
            self._attach(doc, aid, ids)
            self._reconcile(doc)
            # Smartflo sends no ringing webhook; an accepted request means the phone is ringing.
            # Skip if a webhook already arrived, so "ringing" never follows a later status.
            if result.get("success") and not rec.get("selected_event_id"):
                self._queue_agent_delivery(doc, rec, f"ringing:{aid}", "ringing")

    def record_media_start(self, media_id: str, attempt_id: str | None = None) -> str | None:
        ids = {"media_call_id": media_id}
        if attempt_id:
            ids["attempt_id"] = attempt_id
        with self._document() as doc:
            aid = self._resolve(doc, ids)
            if aid:
                self._attach(doc, aid, ids)
                doc["calls"][aid]["timestamps"]["media_started_at"] = now_iso()
                self._queue_agent_delivery(doc, doc["calls"][aid], f"media_started:{aid}", "answered")
                self._reconcile(doc)
            return aid


    @staticmethod
    def _merge_summary(rec: dict, update: dict):
        for key in ("lead_id", "booking_lead_id", "summary", "transcript", "transcript_turn_count", "user_info"):
            if update.get(key) is not None:
                if key == "user_info":
                    rec.setdefault(key, {}).update(update[key])
                else:
                    rec[key] = deepcopy(update[key])
        rec.setdefault("timestamps", {}).update(update.get("timestamps") or {})
        rec["summary_updated_at"] = now_iso()

    def save_summary(self, call_id: str, *, attempt_id: str | None = None,
                     summary: dict | None = None, transcript: list | None = None,
                     include_transcript: bool = False, lead_id: str | None = None,
                     booking_lead_id: str | None = None, timestamps: dict | None = None,
                     user_info: dict | None = None):
        if not identifier(call_id):
            raise ValueError("A media call identifier is required")
        update: dict[str, Any] = {
            "lead_id": identifier(lead_id), "booking_lead_id": identifier(booking_lead_id),
            "summary": deepcopy(summary), "timestamps": timestamps or {}, "user_info": user_info,
        }
        if transcript is not None:
            update["transcript_turn_count"] = len(transcript)
            if include_transcript:
                update["transcript"] = deepcopy(transcript)
        ids = {"media_call_id": call_id}
        if attempt_id:
            ids["attempt_id"] = attempt_id
        with self._document() as doc:
            aid = self._resolve(doc, ids)
            if aid:
                self._attach(doc, aid, ids)
                self._merge_summary(doc["calls"][aid], update)
                self._reconcile(doc)
            else:
                orphan = doc["orphan_summaries"].setdefault(call_id, {
                    "call_id": call_id, "record_source": "media_observation", "call_status": None,
                    "call_status_source": None, "created_at": now_iso()})
                self._merge_summary(orphan, update)

    def save_recording_url(self, attempt_id: str, recording_url: str) -> bool:
        if not identifier(attempt_id) or not identifier(recording_url):
            return False
        with self._document() as doc:
            rec = doc.get("calls", {}).get(attempt_id)
            if rec is None:
                return False
            existing = rec.get("recording_url") or ""
            if existing and not str(existing).startswith("$"):
                return False
            rec["recording_url"] = recording_url
            rec.setdefault("timestamps", {})["recording_enriched_at"] = now_iso()

            # If lead_id exists, queue a distinct Zoho delivery event for the recording URL
            if rec.get("lead_id"):
                key = f"rec:{attempt_id}"
                if key not in doc["deliveries"]:
                    doc["sequence"] += 1
                    selected_eid = rec.get("selected_event_id")
                    selected_event = doc.get("events", {}).get(selected_eid) if selected_eid else None
                    current_status = agent_status_for(selected_event["trigger"], selected_event["payload"]) if selected_event else None
                    doc["deliveries"][key] = {
                        "event_id": key, "attempt_id": rec["attempt_id"],
                        "sequence": doc["sequence"], "state": "pending", "attempts": 0, "next_attempt_at": 0,
                        "payload": {"event": "call_status_update", "event_id": key,
                                    "event_sequence": doc["sequence"], "attempt_id": rec["attempt_id"],
                                    "attempt_created_at": rec["created_at"], "timestamp": now_iso(),
                                    "lead_id": rec["lead_id"], "call_id": rec.get("call_id"),
                                    "ref_id": rec.get("ref_id"), "phone": rec["customer_number"],
                                    "smartflo_trigger": rec.get("smartflo_trigger"),
                                    "status": current_status,
                                    "call_status": rec.get("call_status"),
                                    "status_event_id": rec.get("status_event_id"),
                                    "recording_url": recording_url,
                                    **{k: rec.get(k) for k in CAUSE_FIELDS}},
                    }
            return True

    def receive_event(self, trigger: str, payload: dict) -> dict:
        if trigger not in TRIGGERS or not isinstance(payload, dict):
            raise ValueError("A supported trigger and JSON object are required")
        digest = hashlib.sha256(json.dumps([trigger, payload], sort_keys=True,
                                          ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with self._document() as doc:
            duplicate = digest in doc["events"]
            if not duplicate:
                doc["sequence"] += 1
                doc["events"][digest] = {
                    "event_id": digest, "trigger": trigger, "payload": deepcopy(payload),
                    "received_at": now_iso(), "sequence": doc["sequence"],
                    "attempt_id": None, "applied": False, "diagnostics": [],
                }
            self._reconcile(doc)
            event = doc["events"][digest]
            return {"success": True, "event_id": digest, "duplicate": duplicate,
                    "attempt_id": event["attempt_id"],
                    "matched": event["attempt_id"] is not None, "applied": event["applied"],
                    "diagnostics": list(event["diagnostics"])}

    def _reconcile(self, doc: dict):
        # Repeat: an event may introduce a provider alias needed by an earlier event.
        while True:
            processed = 0
            for event in sorted(doc["events"].values(), key=lambda e: e["sequence"]):
                if event.get("processed"):
                    continue
                try:
                    ids = provider_ids(event["payload"])
                    aid = self._resolve(doc, ids)
                    if not aid:
                        continue
                    self._attach(doc, aid, ids)
                except CorrelationError as exc:
                    event["diagnostics"] = [str(exc)]
                    continue
                event["attempt_id"] = aid
                self._apply_event(doc, aid, event)
                event["processed"] = True
                processed += 1
            if not processed:
                break
        # An event can have arrived before a lead identifier was known.
        for aid, rec in doc["calls"].items():
            if rec.get("lead_id") and rec.get("selected_event_id"):
                self._queue_delivery(doc, rec, doc["events"][rec["selected_event_id"]])

    def _apply_event(self, doc: dict, aid: str, event: dict):
        rec = doc["calls"][aid]
        rec["webhook_event_ids"].append(event["event_id"])
        rec["last_webhook_received_at"] = event["received_at"]
        payload = event["payload"]
        if not _provider_value(payload.get("call_status")):
            event["diagnostics"].append("call_status missing, empty, or an unresolved template")
        cause_key = payload.get("hangup_cause_key", payload.get("hangupcause_key"))
        cause_code = payload.get("hangup_cause_code", payload.get("hangupcause_code"))
        if identifier(cause_code) and str(cause_code).isdigit() and _provider_value(cause_key):
            expected = Q850_KEYS.get(int(cause_code))
            if expected and cause_key.upper() != expected:
                event["diagnostics"].append("Provider Q.850 key and code disagree; original fields retained")
        previous = doc["events"].get(rec.get("selected_event_id"))
        if previous:
            old_terminal = previous["trigger"].startswith("hangup_")
            terminal = event["trigger"].startswith("hangup_")
            if old_terminal and not terminal:
                return
            field = "end_stamp" if terminal else "answer_stamp"
            old_time = _timestamp(previous["payload"].get(field))
            new_time = _timestamp(payload.get(field))
            if old_terminal == terminal:
                if old_time is not None and new_time is not None:
                    if new_time < old_time or (new_time == old_time and event["sequence"] < previous["sequence"]):
                        return
                elif event["sequence"] < previous["sequence"]:
                    return  # Reconciliation order must not replace provider/receipt order.
            if old_terminal and terminal and previous["trigger"] != event["trigger"]:
                if old_time is None or new_time is None or new_time == old_time:
                    event["diagnostics"].append("Conflicting hangup triggers without an unambiguous provider order")
                    return
        if _provider_value(payload.get("call_status")):
            rec["call_status"] = payload["call_status"]
            rec["call_status_source"] = "smartflo_webhook"
            rec["status_event_id"] = event["event_id"]
        # Only hangup events supply cause fields; late answer events cannot clear them.
        if event["trigger"].startswith("hangup_"):
            aliases = {"hangup_cause_key": "hangupcause_key", "hangup_cause_code": "hangupcause_code",
                       "hangup_cause_description": "hangupcause_desc"}
            for key in CAUSE_FIELDS:
                value = payload.get(key)
                alias = aliases.get(key)
                if value is None and alias:
                    value = payload.get(alias)
                elif alias and alias in payload and payload[alias] != value:
                    event["diagnostics"].append(f"Conflicting {key} aliases; raw payload retained")
                valid = _provider_value(value) or (isinstance(value, int) and not isinstance(value, bool))
                if valid:
                    rec[key] = value
                elif value is not None:
                    event["diagnostics"].append(f"Invalid or unresolved {key}; raw payload retained")
        for key in ("recording_url", "recording_name", "duration", "billsec", "call_connected"):
            value = payload.get(key)
            if value is not None and (not isinstance(value, str) or _provider_value(value)):
                rec[key] = deepcopy(value)
        for key in ("start_stamp", "answer_stamp", "end_stamp"):
            if _provider_value(payload.get(key)):
                rec["timestamps"][key] = payload[key]
        rec["selected_event_id"] = event["event_id"]
        rec["smartflo_trigger"] = event["trigger"]
        event["applied"] = True
        self._queue_delivery(doc, rec, event)
        if event["diagnostics"]:
            logger.warning("Smartflo event %s: %s", event["event_id"], "; ".join(event["diagnostics"]))

    @staticmethod
    def _queue_agent_delivery(doc: dict, rec: dict, key: str, status: str, sequence: int | None = None):
        key = f"agent:{key}"
        if not rec.get("lead_id") or key in doc["deliveries"]:
            return
        if sequence is None:
            doc["sequence"] += 1
            sequence = doc["sequence"]
        doc["deliveries"][key] = {
            "event_id": key, "target": "ai_agent", "attempt_id": rec["attempt_id"],
            "sequence": sequence, "state": "pending", "attempts": 0, "next_attempt_at": 0,
            "payload": {"lead_id": rec["lead_id"], "status": status},
        }

    @staticmethod
    def _queue_delivery(doc: dict, rec: dict, event: dict):
        if rec.get("lead_id"):
            CallRecordStore._queue_agent_delivery(
                doc, rec, event["event_id"], agent_status_for(event["trigger"], event["payload"]),
                event["sequence"])
        if not rec.get("lead_id") or event["event_id"] in doc["deliveries"]:
            return
        doc["deliveries"][event["event_id"]] = {
            "event_id": event["event_id"], "attempt_id": rec["attempt_id"],
            "sequence": event["sequence"], "state": "pending", "attempts": 0, "next_attempt_at": 0,
            "payload": {"event": "call_status_update", "event_id": event["event_id"],
                        "event_sequence": event["sequence"], "attempt_id": rec["attempt_id"],
                        "attempt_created_at": rec["created_at"], "timestamp": event["received_at"],
                        "lead_id": rec["lead_id"], "call_id": rec.get("call_id"),
                        "ref_id": rec.get("ref_id"), "phone": rec["customer_number"],
                        "smartflo_trigger": rec["smartflo_trigger"],
                        "status": agent_status_for(event["trigger"], event["payload"]),
                        "call_status": rec["call_status"], "status_event_id": rec.get("status_event_id"),
                        "recording_url": rec.get("recording_url"),
                        **{key: rec.get(key) for key in CAUSE_FIELDS}},
        }

    def claim_delivery(self, at: float | None = None, target: str = "zoho") -> dict | None:
        at = time.time() if at is None else at
        with self._document() as doc:
            jobs = sorted((j for j in doc["deliveries"].values() if j.get("target", "zoho") == target),
                      key=lambda j: j["sequence"])
            for job in jobs:
                if job["state"] == "delivered":
                    continue
                if job.get("lease_until", 0) > at or job["next_attempt_at"] > at:
                    continue
                earlier = [j for j in jobs if j["sequence"] < job["sequence"] and
                           j["attempt_id"] == job["attempt_id"] and j["state"] != "delivered"]
                same_lead_busy = any(j.get("lease_until", 0) > at and j["payload"]["lead_id"] == job["payload"]["lead_id"]
                                     for j in jobs if j is not job)
                if earlier or same_lead_busy:
                    continue
                job.update(state="inflight", claim_token=uuid.uuid4().hex, lease_until=at + 60,
                           attempts=job["attempts"] + 1)
                payload = job["payload"]
                if target == "ai_agent":
                    return deepcopy(job)
                latest = max((r for r in doc["calls"].values() if r.get("lead_id") == payload["lead_id"]),
                             key=lambda r: r["created_at"])
                payload["update_lead_status"] = latest["attempt_id"] == job["attempt_id"]
                return deepcopy(job)
        return None

    def finish_delivery(self, event_id: str, claim_token: str, success: bool, at: float | None = None):
        at = time.time() if at is None else at
        with self._document() as doc:
            job = doc["deliveries"].get(event_id)
            if not job or job.get("claim_token") != claim_token or job["state"] != "inflight":
                return
            job["lease_until"] = 0
            job["last_attempt_at"] = at
            if success:
                job.update(state="delivered", delivered_at=now_iso())
            else:
                delay = (1, 5, 30, 120, 300)[min(job["attempts"] - 1, 4)]
                job.update(state="pending", next_attempt_at=at + delay, last_error="Zoho delivery failed")

    def list_records(self) -> dict[str, dict[str, Any]]:
        legacy = {}
        if self.legacy_path.exists():
            try:
                with open(self.legacy_path, encoding="utf-8") as source:
                    legacy = json.load(source)
                if not isinstance(legacy, dict):
                    raise ValueError("Expected record object")
            except (OSError, ValueError) as exc:
                raise CallStoreError("Cannot read legacy calls; file was not changed") from exc
        records: dict[str, dict[str, Any]] = {
            key: {**deepcopy(rec), "record_source": "legacy_unverified"}
            for key, rec in legacy.items() if isinstance(rec, dict)
        }
        with self._document(write=False) as doc:
            records.update(deepcopy(doc["orphan_summaries"]))
            records.update(deepcopy(doc["calls"]))
        return records

    def find_record(self, value: str) -> dict[str, Any] | None:
        ident = identifier(value)
        if not ident:
            return None
        records = self.list_records()
        if ident in records:
            return records[ident]
        exact = [r for r in records.values() if ident in (r.get("call_id"), r.get("media_call_id"), r.get("ref_id")) or
                 any(ident in values for values in r.get("identifiers", {}).values())]
        if len(exact) == 1:
            return exact[0]
        if exact:
            raise CorrelationError("Ambiguous call identifier")
        matches = [r for r in records.values() if str(r.get("lead_id") or (r.get("summary") or {}).get("lead_id")) == ident]
        return max(matches, key=lambda r: r.get("created_at") or (r.get("timestamps") or {}).get("initiated_at") or
                   (r.get("timestamps") or {}).get("updated_at") or "", default=None)

    def get_attempt(self, aid: str) -> dict | None:
        with self._document(write=False) as doc:
            rec = deepcopy(doc["calls"].get(aid))
            if rec is None:
                return None
            rec["webhook_events"] = [deepcopy(doc["events"][eid]) for eid in rec["webhook_event_ids"]]
            for name, target in (("zoho_deliveries", "zoho"), ("ai_agent_deliveries", "ai_agent")):
                rec[name] = [{key: value for key, value in job.items() if key != "claim_token"}
                             for job in doc["deliveries"].values()
                             if job["attempt_id"] == aid and job.get("target", "zoho") == target]
            return rec

    def health(self) -> dict:
        with self._document(write=False) as doc:
            return {"unmatched_events": sum(not e.get("processed") for e in doc["events"].values()),
                    "pending_deliveries": sum(j["state"] != "delivered" for j in doc["deliveries"].values()),
                    "attempts_without_webhook": sum(not r["webhook_event_ids"] for r in doc["calls"].values())}


_default_store: CallRecordStore | None = None


def get_call_store() -> CallRecordStore:
    global _default_store
    if _default_store is None:
        from backend.app.settings import settings
        _default_store = CallRecordStore(settings.SMARTFLO_STATE_FILE or ROOT / "data/smartflo_call_state.json")
    return _default_store
