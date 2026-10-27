"""
Resend an existing conversation summary without sending any telephony status.
Usage: python scripts/resend_call_summary.py [call/ref/attempt/lead identifier]
Reads both provider records and read-only legacy history through the shared store.
"""

import asyncio
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services.zoho_service import send_call_summary_to_zoho
from backend.app.services.call_record_service import get_call_store

async def main():
    store = get_call_store()
    if len(sys.argv) > 1:
        record = await asyncio.to_thread(store.find_record, sys.argv[1])
    else:
        records = await asyncio.to_thread(store.list_records)
        candidates = [rec for rec in records.values() if rec.get("summary")]
        record = max(candidates, key=lambda rec: rec.get("created_at") or
                     (rec.get("timestamps") or {}).get("updated_at") or "", default=None)
    if record is None:
        print("No matching call summary found.")
        return
    target_sid = record.get("call_id") or record.get("attempt_id")
    summary = record.get("summary") or {}
    lead_id = record.get("lead_id") or summary.get("lead_id")
    booking_lead_id = record.get("booking_lead_id") or summary.get("booking_lead_id")
    transcript = record.get("transcript")

    # Reconstruct user_info
    user_info = {
        "lead_id": lead_id,
        "booking_lead_id": booking_lead_id,
    }
    # If summary contains user's name (e.g. 'Raj Kothiya expressed interest...'), attempt extraction
    first_word = summary.get("summary", "").split()
    if len(first_word) >= 2 and first_word[0].isalpha() and first_word[1].isalpha():
        user_info["name"] = f"{first_word[0]} {first_word[1]}"

    print(f"Resending call '{target_sid}' (Lead ID: {lead_id}) to Zoho Flow:")
    print(f"Summary: {summary.get('summary')}")

    success = await send_call_summary_to_zoho(
        call_id=target_sid,
        user_info=user_info,
        summary=summary,
        transcript_history=transcript,
        lead_id=lead_id,
        booking_lead_id=booking_lead_id,
    )

    if success:
        print(f"\n[SUCCESS] Call summary for '{target_sid}' successfully dispatched to Zoho Flow!")
    else:
        print(f"\n[FAILED] Could not dispatch call summary for '{target_sid}'.")


if __name__ == "__main__":
    asyncio.run(main())
