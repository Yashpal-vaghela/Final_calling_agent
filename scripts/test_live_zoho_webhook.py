"""
Live Verification Script for Zoho Flow Webhook Integration.
Sends a test post-call lead summary to the live Zoho Flow URL to verify
connectivity, authentication (zapikey), and payload parsing.
"""

import asyncio
import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services.zoho_service import send_call_summary_to_zoho
from backend.app.settings import settings


async def main():
    print("=" * 60)
    print("Zoho Flow Webhook Live Verification")
    print(f"Target URL: {settings.ZOHO_FLOW_WEBHOOK_URL}")
    print("=" * 60)

    test_call_id = "test-live-call-001"
    test_user_info = {
        "name": "Live Test Caller",
        "phone": "9876543210",
        "email": "testcaller@example.com",
        "city": "Ahmedabad",
        "pincode": "380015",
        "intent": "Clear Aligners Consultation",
        "notes": "Verified integration from Final Calling Agent",
    }
    test_summary = {
        "summary": "Caller was busy in a meeting and requested a callback tomorrow afternoon. Inquired about invisible aligners for gap correction.",
        "key_concerns": ["Invisible aligners", "Treatment pricing", "Clinic location"],
        "call_outcome": "callback_requested",
        "callback_time": "Tomorrow at 4:00 PM",
        "booking_status": "in_progress",
        "customer_sentiment": "positive",
        "language_used": "English",
        "next_actions": [
            "Call back customer tomorrow at 4:00 PM",
            "Send WhatsApp clinic brochure before call"
        ],
    }

    print("\nSending live test summary payload to Zoho Flow...")
    success = await send_call_summary_to_zoho(
        call_id=test_call_id,
        user_info=test_user_info,
        summary=test_summary,
        lead_id="test-lead-live-01",
        include_transcript=False
    )

    print("\n" + "=" * 60)
    if success:
        print("[SUCCESS] Zoho Flow received and accepted the payload.")
    else:
        print("[FAILED] Zoho Flow webhook did not return a success code.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
