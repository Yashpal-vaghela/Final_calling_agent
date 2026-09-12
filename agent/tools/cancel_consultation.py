import json
import httpx
from typing import Optional

API_URL = "http://192.168.0.161:5252/api/consult-with-dentist/"

def cancel_consultation(
    lead_id: str, 
    reason: str = "",
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    email: str = "",
    city: str = "",
    doctor_name: str = ""
) -> dict:
    """
    Cancel an existing in-call consultation via the backend API.
    ONLY call this tool AFTER the user has explicitly insisted on canceling, 
    and AFTER you have first asked them why they want to cancel and attempted to help them.
    """
    clean_lead_id = int(str(lead_id).strip()) if (lead_id and str(lead_id).strip().isdigit()) else ""
    
    if not clean_lead_id:
        return {
            "status": "error",
            "message": "Cannot cancel the appointment because no valid lead_id was found. Please inform the user."
        }

    payload = {
        "lead_id": clean_lead_id,
        "first_name": first_name.strip(),
        "last_name": last_name.strip() or ".",
        "phone": phone.strip(),
        "email": email.strip(),
        "is_cancel": True,
        "message": f"CANCELLATION REASON: {reason}".strip() if reason else "CANCELLED",
        "source": "calling_agent"
    }
    
    if city.strip():
        payload["city"] = city.strip()
    if doctor_name.strip():
        payload["doctor_name"] = doctor_name.strip()

    print("\n" + "="*70)
    print(">>> [CONSULTATION CANCELLATION] Submitting Cancellation to API:")
    print(f"Target URL: {API_URL}")
    print(f"Payload sent:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    print("="*70)

    try:
        response = httpx.post(API_URL, json=payload, timeout=10.0)
        response.raise_for_status()
        print("\n" + "="*70)
        print(f"[+] [CANCELLATION API SUCCESS] HTTP {response.status_code}")
        print(f"API Response:\n{response.text}")
        print("="*70 + "\n")
    except Exception as e:
        print("\n" + "="*70)
        print(f"[-] [CANCELLATION API FAILED] Error: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            print(f"API Error Body: {e.response.text}")
        print("="*70 + "\n")
        return {
            "status": "error",
            "message": f"An error occurred while canceling the consultation: {str(e)}"
        }
        
    return {
        "status": "success",
        "message": "The consultation has been successfully canceled in the system."
    }
