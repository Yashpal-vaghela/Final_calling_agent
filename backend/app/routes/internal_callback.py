import os
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from backend.app.settings import settings
from backend.app.models.call import Call
from backend.app.services.smartflo_service import smartflo_client

import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from agent.utils.call_logger import save_calls, load_calls

router = APIRouter()

class TriggerCallbackRequest(BaseModel):
    lead_id: str

# In-memory rate limiting dictionary: { phone_number: last_called_timestamp }
rate_limit_cache = {}

def get_internal_key(request: Request):
    key = request.headers.get("X-Internal-Key")
    if not key or key != settings.INTERNAL_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return key

@router.post("/internal/trigger-callback")
async def trigger_callback(payload: TriggerCallbackRequest, request: Request, _ = Depends(get_internal_key)):
    """
    Triggers an outbound follow-up call to a lead via Tata Smartflo Click-to-Call API.
    """
    # 1. Load lead from active in-memory context
    from backend.app.services.caller_context import lookup_caller_context
    lead = lookup_caller_context(payload.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found or expired")
        
    phone = lead.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Lead has no phone number")
        
    # Format phone for E.164 if needed (assuming India +91 if not specified)
    if not phone.startswith("+"):
        if len(phone) == 10:
            phone = "+91" + phone
        else:
            phone = "+" + phone
            
    # 2. Check ALLOWED_NUMBERS
    allowed = [n.strip() for n in settings.ALLOWED_NUMBERS.split(",") if n.strip()]
    if allowed and phone not in allowed:
        raise HTTPException(status_code=403, detail=f"Phone number {phone} is not in ALLOWED_NUMBERS")
        
    # 3. Check rate limits (1 call per 5 minutes)
    now = datetime.now(timezone.utc).timestamp()
    last_called = rate_limit_cache.get(phone, 0)
    if now - last_called < 300:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait 5 minutes before calling this number again.")
        
    # 4. Initiate Outbound Click-to-Call via Tata Smartflo
    custom_params = {
        "lead_id": payload.lead_id,
        "opening_intent": "follow-up"
    }

    result = await smartflo_client.initiate_click_to_call(
        customer_number=phone,
        custom_params=custom_params
    )
    
    rate_limit_cache[phone] = now
    
    return {
        "status": "success",
        "lead_id": payload.lead_id,
        "phone": phone,
        "smartflo_response": result
    }
