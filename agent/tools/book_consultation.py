import json
import os
import re
import httpx
from typing import Optional

from backend.app.services.caller_context import validate_phone_number
from backend.app.services.city_service import get_nearest_covered_city, get_default_pincode_for_city

from backend.app.services.dentist_service import get_dentist_service

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
CITIES_FILE = os.path.join(DATA_DIR, "covered_cities.json")
API_URL = "http://192.168.0.161:1020/api/consult-with-dentist/"

def book_consultation(
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    email: str = "",
    city: str = "",
    doctor_name: str = "",
    message: str = "",
    lead_id: str = "",
    pincode: str = ""
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
            
    is_uncovered = False
    if matched_city:
        nearest_covered_city = matched_city
        booking_city = matched_city
    else:
        is_uncovered = True
        nearest_covered_city = get_nearest_covered_city(city_clean)
        # CRITICAL: Always keep caller's actual city in the database payload
        booking_city = city_clean
        
    # 2. Check authorized dentist if doctor_name is provided
    matched_doctor = None
    doctor_city = nearest_covered_city
    doc_clean = doctor_name.strip()
    
    if doc_clean:
        auth_dentists = get_dentist_service().get_directory_sync()
                
        from agent.tools.check_dentist import find_best_doctor_match

        # First check in the caller's city or nearest covered city
        city_dentists = auth_dentists.get(nearest_covered_city, [])
        matched_doctor = find_best_doctor_match(doc_clean, city_dentists)
        if matched_doctor:
            doctor_city = nearest_covered_city
        else:
            # Check across all other covered cities
            for other_c, dentists in auth_dentists.items():
                found = find_best_doctor_match(doc_clean, dentists)
                if found:
                    matched_doctor = found
                    doctor_city = other_c
                    break

        if not matched_doctor:
            top_doc = city_dentists[0] if city_dentists else None
            if top_doc:
                msg = (
                    f"The requested dentist is not an authorized smile designer. "
                    f"We have {top_doc} in {nearest_covered_city}. "
                    f"Tell the caller: '{doctor_name} is not one of our authorized smile designers. "
                    f"I can book an appointment with {top_doc} in {nearest_covered_city}. Shall I confirm that for you?'"
                )
            else:
                msg = (
                    f"The requested dentist is not an authorized smile designer. "
                    f"Tell the caller: '{doctor_name} is not one of our authorized smile designers. "
                    f"Would you like me to check our nearest authorized smile designer for you?'"
                )
            return {
                "status": "not_authorized",
                "doctor_requested": doctor_name,
                "city": booking_city,
                "suggested_doctor": top_doc,
                "message": msg,
                "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
            }
            
    # 3. Build API payload
    clean_last = last_name.strip()
    if not clean_last:
        clean_last = "."

    # CRITICAL: The external CRM API expects 'lead_id' to be an integer database primary key for updates,
    # or an empty string for new bookings.
    clean_lead_id = int(lead_id.strip()) if (lead_id and lead_id.strip().isdigit()) else ""

    payload_message = message.strip()
    location_notes = []
    if is_uncovered:
        location_notes.append(f"[Caller City: {city_clean} | Nearest authorized smile designer in {nearest_covered_city}]")
    if matched_doctor and doctor_city.lower() != booking_city.lower():
        location_notes.append(f"[Preferred Doctor: {matched_doctor}, authorized smile designer in {doctor_city}]")

    if location_notes:
        note_str = " ".join(location_notes)
        payload_message = f"{payload_message} {note_str}".strip() if payload_message else note_str

    clean_pincode = pincode.strip() if pincode and pincode.strip() else get_default_pincode_for_city(booking_city)

    payload = {
        "lead_id": clean_lead_id,
        "first_name": first_name.strip(),
        "last_name": clean_last,
        "phone": validated_phone,
        "email": email.strip(),
        "pincode": clean_pincode,
        "city": booking_city,
        "message": payload_message,
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
        
    # 5. Return success with strict brand terminology (authorized smile designer)
    if is_uncovered:
        success_message = (
            f"Your consultation has been booked successfully with our authorized smile designer in {nearest_covered_city}. "
            f"Tell the caller: 'Your consultation has been booked with our authorized smile designer in {nearest_covered_city}. "
            f"Our team will call you as soon as possible to verify your details.'"
        )
    elif matched_doctor and doctor_city.lower() != booking_city.lower():
        success_message = (
            f"Your consultation with {matched_doctor} has been booked successfully. "
            f"Tell the caller: 'Your consultation with {matched_doctor}, our authorized smile designer in {doctor_city}, has been booked successfully. "
            f"Our team will call you as soon as possible to verify your details.'"
        )
    else:
        success_message = (
            f"Your consultation has been booked successfully with our authorized smile designer in {booking_city}. "
            f"Tell the caller: 'Your consultation has been booked with our authorized smile designer in {booking_city}. "
            f"Our team will call you as soon as possible to verify your details.'"
        )

    effective_lead_id = str(clean_lead_id) if clean_lead_id else returned_lead_id

    return {
        "status": "success",
        "message": success_message,
        "lead_id": effective_lead_id,
        "city": booking_city,
        "nearest_covered_city": nearest_covered_city,
        "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. Always say 'authorized smile designer'. Do not mention kilometers, distances, or specific timeframes."
    }
