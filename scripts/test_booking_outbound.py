import os
import sys
import urllib.parse
import webbrowser

# Ensure project root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import asyncio
import httpx
from backend.app.settings import settings
from backend.app.services.smartflo_service import smartflo_client


async def trigger_direct_call(customer_number: str):
    print(f"\n[Smartflo] Direct call mode: Initiating call to {customer_number}...")
    print(f"[Smartflo] Configured Caller ID    : {settings.SMARTFLO_CALLER_ID}")
    print(f"[Smartflo] Configured Agent Number : {settings.SMARTFLO_AGENT_NUMBER}")

    res = await smartflo_client.initiate_click_to_call(
        customer_number=customer_number,
        custom_params={"opening_intent": "outbound_booking_form"}
    )
    print(f"[Smartflo] Response: {res}\n")
    if res.get("success"):
        print("[+] Call successfully queued by Smartflo!")
        print("    Note: Make sure your SMARTFLO_AGENT_NUMBER extension is active and answering.")
    else:
        print(f"[-] Call failed: {res}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  1. Open Web Form   : python scripts/test_booking_outbound.py <customer_number>")
        print("  2. Direct Call     : python scripts/test_booking_outbound.py <customer_number> --direct")
        print("Example:")
        print("  python scripts/test_booking_outbound.py 8758657212 --direct")
        sys.exit(1)

    customer_number = sys.argv[1].strip()
    is_direct = "--direct" in sys.argv or "-d" in sys.argv

    if is_direct:
        asyncio.run(trigger_direct_call(customer_number))
        return

    encoded_phone = urllib.parse.quote(customer_number)
    form_url = f"http://localhost:8050/booking-form?phone={encoded_phone}"

    print("\n" + "=" * 65)
    print("    ULTIMATE SMILE DESIGN — OUTBOUND BOOKING APPOINTMENT FORM")
    print("=" * 65)
    print(f"Target Destination Phone : {customer_number}")
    print(f"Opening Booking Form URL : {form_url}")
    print("=" * 65)
    print("Please enter the caller's First/Last Name, Email, City, and Message")
    print("in the browser form and click 'Submit Details & Start Call'.")
    print("Tip: Add '--direct' flag to place direct outbound call from terminal:")
    print(f"  python scripts/test_booking_outbound.py {customer_number} --direct")
    print("=" * 65 + "\n")

    try:
        webbrowser.open(form_url)
    except Exception as e:
        print(f"[Notice] Could not automatically open default browser: {e}")
        print(f"Please copy and open this URL manually in your browser:\n  {form_url}\n")


if __name__ == "__main__":
    main()
