import os
import sys
import asyncio

# Ensure project root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.app.services.smartflo_service import smartflo_client

async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_smartflo_outbound.py <customer_number> [opening_intent] [lead_id]")
        print("Example: python scripts/test_smartflo_outbound.py +918758657212")
        print("Example: python scripts/test_smartflo_outbound.py +918758657212 consultation lead_001")
        sys.exit(1)

    customer_number = sys.argv[1]
    opening_intent = sys.argv[2] if len(sys.argv) > 2 else "follow-up"
    lead_id = sys.argv[3] if len(sys.argv) > 3 else None

    custom_params = {}
    if opening_intent:
        custom_params["opening_intent"] = opening_intent
    if lead_id:
        custom_params["lead_id"] = lead_id

    print(f"[Smartflo Script] Initiating outbound call to: {customer_number}...")
    result = await smartflo_client.initiate_click_to_call(
        customer_number=customer_number,
        custom_params=custom_params
    )
    print(f"[Smartflo Script] Response: {result}")

if __name__ == "__main__":
    asyncio.run(main())
