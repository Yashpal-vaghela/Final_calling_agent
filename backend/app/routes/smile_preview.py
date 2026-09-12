import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.app.services.smartflo_service import smartflo_client
from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS, register_caller_context
from backend.app.services.city_service import is_city_covered

router = APIRouter()

class SmilePreviewSubmission(BaseModel):
    name: str = Field(..., min_length=1, description="Caller full name")
    phone: str = Field(..., min_length=5, description="Caller phone number")
    city: str = Field(..., min_length=1, description="Caller city")
    email: str = Field(..., min_length=3, description="Caller email address")

@router.get("/smile-preview", response_class=HTMLResponse)
async def get_smile_preview_form():
    """Serves the Smile Preview Lead Capture Form webpage."""
    template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates", "smile_preview_form.html"))
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Smile preview template not found.")
    
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)

@router.post("/api/smile-preview/submit")
async def submit_smile_preview_form(form_data: SmilePreviewSubmission):
    """
    Receives submitted smile preview lead details, caches context, logs lead,
    and initiates outbound Click-to-Call via Tata Smartflo.
    """
    if not is_city_covered(form_data.city):
        raise HTTPException(status_code=400, detail=f"City '{form_data.city}' is not covered. Please select a valid city.")

    lead_id = str(uuid.uuid4())
    raw_name = form_data.name.strip()
    name_parts = raw_name.split(maxsplit=1) if raw_name else []
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else "."

    context_data = {
        "id": lead_id,
        "name": raw_name,
        "first_name": first_name,
        "last_name": last_name,
        "phone": form_data.phone.strip(),
        "email": form_data.email.strip(),
        "city": form_data.city.strip(),
        "intent": "outbound_smile_preview",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Register into active lookup cache & persist lead
    register_caller_context(context_data)
    print(f"[SmilePreview] Registered caller context for {context_data['name']} ({context_data['phone']}) - Lead ID: {lead_id}")

    # Initiate Smartflo Click-to-Call
    custom_params = {
        "lead_id": lead_id,
        "opening_intent": "outbound_smile_preview"
    }

    result = await smartflo_client.initiate_click_to_call(
        customer_number=form_data.phone.strip(),
        custom_params=custom_params
    )

    print(f"[SmilePreview] Smartflo click-to-call response: {result}")
    if isinstance(result, dict):
        resp_call_id = (
            result.get("call_id") or result.get("call_uuid") or
            result.get("id") or result.get("ref_id") or
            (result.get("data") if isinstance(result.get("data"), dict) else {}).get("call_id")
        )
        if resp_call_id:
            ACTIVE_CALLER_CONTEXTS[str(resp_call_id)] = context_data
            print(f"[SmilePreview] Associated Smartflo response callId '{resp_call_id}' to lead '{lead_id}'")

    return {
        "success": True,
        "lead_id": lead_id,
        "customer_number": form_data.phone.strip(),
        "smartflo_response": result
    }
