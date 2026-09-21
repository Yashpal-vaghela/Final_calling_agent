import json
import os
import re
import httpx
from typing import Optional

from backend.app.services.caller_context import validate_phone_number

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
CITIES_FILE = os.path.join(DATA_DIR, "covered_cities.json")
AUTH_DENTISTS_FILE = os.path.join(DATA_DIR, "authorized_dentists.json")
API_URL = "https://ultimatesmiledesign.com/api/consult-with-dentist/"

def book_consultation(
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    email: str = "",
    city: str = "",
    doctor_name: str = "",
    message: str = "",
    lead_id: str = ""
) -> dict:
    """
    Book OR update an in-call consultation via the backend API.
    ONLY call this tool AFTER the user has explicitly confirmed they want to submit OR update the request. 
    You MUST summarize what will be submitted (city, doctor_name) and wait for their 'yes' before calling this tool.
    """
    # 0. Validate phone number strictly (10 national digits)
    phone_res = validate_phone_number(phone)
    if not phone_res["valid"]:
        return {
            "status": "invalid_phone",
            "valid": False,
            "code": phone_res["code"],
            "received_digits": phone_res["received_digits"],
            "expected_digits": 10,
            "message": f"I cannot proceed with the booking because the phone number provided is invalid. A complete 10-digit mobile number is required ({phone_res['message']}). Please provide a valid 10-digit mobile number.",
            "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
        }
    validated_phone = phone_res["phone"]

    city_clean = city.strip()
    city_lower = city_clean.lower()
    
    # 1. Check city coverage
    covered_cities = []
    if os.path.exists(CITIES_FILE):
        try:
            with open(CITIES_FILE, "r", encoding="utf-8") as f:
                covered_cities = json.load(f)
        except Exception:
            pass
            
    matched_city = None
    for c in covered_cities:
        if c.lower() == city_lower:
            matched_city = c
            break
            
    if not matched_city:
        return {
            "status": "city_not_covered",
            "message": f"I'm sorry, but Ultimate Smile Design does not currently have authorized clinics in {city_clean}. I cannot submit the consultation request.",
            "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
        }
        
    # 2. Check authorized dentist if doctor_name is provided
    matched_doctor = None
    doc_clean = doctor_name.strip()
    
    if doc_clean:
        auth_dentists = {}
        if os.path.exists(AUTH_DENTISTS_FILE):
            try:
                with open(AUTH_DENTISTS_FILE, "r", encoding="utf-8") as f:
                    auth_dentists = json.load(f)
            except Exception:
                pass
                
        city_dentists = auth_dentists.get(matched_city, [])
        from agent.tools.check_dentist import find_best_doctor_match
        matched_doctor = find_best_doctor_match(doc_clean, city_dentists)

        if not matched_doctor:
            return {
                "status": "not_authorized",
                "doctor_requested": doctor_name,
                "city": matched_city,
                "message": f"The requested dentist is not an authorized smile designer in {matched_city}. You may proceed without specifying a dentist and our coordinator will assign the appropriate specialist.",
                "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
            }
            
    # 3. Build API payload
    clean_last = last_name.strip()
    if not clean_last:
        clean_last = "."

    # CRITICAL: The external CRM API expects 'lead_id' to be an integer database primary key for updates,
    # or an empty string for new bookings.
    # NEVER send UUID strings (like in-memory caller_context IDs) as they cause Django to crash with:
    # "Field 'id' expected a number but got '<UUID>'".
    clean_lead_id = int(lead_id.strip()) if (lead_id and lead_id.strip().isdigit()) else ""

    payload = {
        "lead_id": clean_lead_id,
        "first_name": first_name.strip(),
        "last_name": clean_last,
        "phone": validated_phone,
        "email": email.strip(),
        "city": matched_city,
        "message": message.strip(),
        "is_cancel": False,
        "source": "calling_agent"
    }

    # Include authorized doctor_name so it appears in the admin panel
    if matched_doctor:
        payload["doctor_name"] = matched_doctor
        
    # 4. Make the API call
    print("\n" + "="*70)
    print(">>> [CONSULTATION SUBMISSION] Submitting Booking to API:")
    print(f"Target URL: {API_URL}")
    print(f"Payload sent:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    print("="*70)

    returned_lead_id = ""
    try:
        response = httpx.post(API_URL, json=payload, timeout=10.0)
        response.raise_for_status()
        print("\n" + "="*70)
        print(f"[+] [CONSULTATION API SUCCESS] HTTP {response.status_code}")
        print(f"API Response:\n{response.text}")
        print("="*70 + "\n")
        try:
            resp_data = response.json()
            if isinstance(resp_data.get("data"), dict) and "id" in resp_data["data"]:
                returned_lead_id = str(resp_data["data"]["id"])
            elif "lead_id" in resp_data:
                returned_lead_id = str(resp_data["lead_id"])
            elif "id" in resp_data:
                returned_lead_id = str(resp_data["id"])
        except Exception:
            pass
    except Exception as e:
        print("\n" + "="*70)
        print(f"[-] [CONSULTATION API FAILED] Error: {str(e)}")
        if hasattr(e, "response") and e.response is not None:
            print(f"API Error Body: {e.response.text}")
        print("="*70 + "\n")
        return {
            "status": "error",
            "message": f"An error occurred while submitting the consultation request: {str(e)}",
            "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
        }
        
    # 5. Return success
    return {
        "status": "success",
        "message": "Your consultation has been booked successfully. Our team will call you soon to verify your details. Tell the caller: 'Our team will call you as soon as possible to verify your details.' NEVER mention a specific time such as hours, days, or 'tomorrow'.",
        "lead_id": returned_lead_id,
        "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
    }

