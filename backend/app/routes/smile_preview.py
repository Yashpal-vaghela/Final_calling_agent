import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.app.services.smartflo_service import smartflo_client
from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS, register_caller_context
from backend.app.services.city_service import is_city_covered, is_city_supported, validate_city_pincode_match_async
from agent.utils.smile_diff import analyze_smile_preview_diff, resolve_smile_media_url

router = APIRouter()




class SmilePreviewSubmission(BaseModel):
    name: str = Field(..., min_length=1, description="Caller full name")
    phone: str = Field(..., min_length=5, description="Caller phone number")
    city: str = Field(..., min_length=1, description="Caller city")
    pincode: str = Field(..., min_length=6, max_length=6, description="Caller 6-digit area pincode")
    email: str = Field(..., min_length=3, description="Caller email address")
    before_image: Optional[str] = Field(
        default=None,
        description="Relative media path or full URL for the user's BEFORE photo (e.g. smile_design/before/before_1790406333_871c47f961.jpg)"
    )
    after_image: Optional[str] = Field(
        default=None,
        description="Relative media path or full URL for the user's AFTER AI Smile Preview photo (e.g. smile_design/after/after_1790406333_871c47f961.jpg)"
    )
    before_image_url: Optional[str] = Field(
        default=None,
        description="Optional alias if full or relative before image URL is sent as before_image_url"
    )
    after_image_url: Optional[str] = Field(
        default=None,
        description="Optional alias if full or relative after image URL is sent as after_image_url"
    )


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
    Receives submitted smile preview lead details and Before/After image paths,
    resolves complete https://ultimatesmiledesign.com/media/... URLs, analyzes the
    Before vs. After visual smile differences in memory, caches caller context,
    and initiates outbound Click-to-Call via Tata Smartflo.
    """
    if not is_city_supported(form_data.city):
        raise HTTPException(status_code=400, detail=f"City '{form_data.city}' is not covered. Please select a valid city.")

    is_valid_pin, pin_err = await validate_city_pincode_match_async(form_data.city, form_data.pincode)
    if not is_valid_pin:
        raise HTTPException(status_code=400, detail=pin_err)


    lead_id = str(uuid.uuid4())
    raw_name = form_data.name.strip()
    name_parts = raw_name.split(maxsplit=1) if raw_name else []
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else "."

    raw_before = (form_data.before_image or form_data.before_image_url or "").strip()
    raw_after = (form_data.after_image or form_data.after_image_url or "").strip()
    resolved_before_url = resolve_smile_media_url(raw_before)
    resolved_after_url = resolve_smile_media_url(raw_after)

    if resolved_before_url or resolved_after_url:
        print(f"[SmilePreview] Resolved Before Image URL: {resolved_before_url}")
        print(f"[SmilePreview] Resolved After Image URL:  {resolved_after_url}")

    # 1. Complete Before/After vision analysis FIRST before initiating the call
    smile_diff_result = None
    if resolved_before_url and resolved_after_url:
        print(f"[SmilePreview] Analyzing Before/After smile diff before initiating call...")
        smile_diff_result = await analyze_smile_preview_diff(
            before_image=resolved_before_url,
            after_image=resolved_after_url,
        )
        print(
            f"[SmilePreview] Smile diff analysis complete (has_valid_comparison="
            f"{smile_diff_result.get('has_valid_comparison') if isinstance(smile_diff_result, dict) else False}). "
            f"Now initiating call..."
        )

    context_data: dict[str, Any] = {
        "id": lead_id,
        "name": raw_name,
        "first_name": first_name,
        "last_name": last_name,
        "phone": form_data.phone.strip(),
        "email": form_data.email.strip(),
        "city": form_data.city.strip(),
        "pincode": form_data.pincode.strip(),
        "before_image": raw_before,
        "after_image": raw_after,
        "before_image_url": resolved_before_url,
        "after_image_url": resolved_after_url,
        "smile_diff": smile_diff_result,
        "intent": "outbound_smile_preview",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # 2. Register into active lookup cache with smile_diff already populated
    register_caller_context(context_data)
    print(f"[SmilePreview] Registered caller context for {context_data['name']} ({context_data['phone']}) - Lead ID: {lead_id}")

    # 3. Initiate Smartflo Click-to-Call AFTER smile diff is ready
    custom_params = {
        "lead_id": lead_id,
        "opening_intent": "outbound_smile_preview",
        "city": form_data.city.strip(),
        "pincode": form_data.pincode.strip()
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
        "before_image_url": resolved_before_url,
        "after_image_url": resolved_after_url,
        "smile_diff": smile_diff_result,
        "smartflo_response": result
    }
