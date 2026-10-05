import json
import os
from typing import Optional, List
from backend.app.services.city_service import (
    get_nearest_covered_city,
    get_ranked_covered_cities,
    is_city_covered,
    get_covered_cities
)

# Path to covered_cities.json
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
CITIES_FILE = os.path.join(DATA_DIR, "covered_cities.json")


def _format_doctor_names(doctors: List[str]) -> str:
    """Formats doctor names into a conversational natural speech string."""
    if not doctors:
        return ""
    # Filter to doctor titles
    clean_docs = [d if d.startswith("Dr.") else f"Dr. {d}" for d in doctors[:3]]
    if len(clean_docs) == 1:
        return clean_docs[0]
    elif len(clean_docs) == 2:
        return f"{clean_docs[0]} and {clean_docs[1]}"
    else:
        return f"{', '.join(clean_docs[:-1])}, and {clean_docs[-1]}"


def check_city_coverage(
    city: str,
    exclude_cities: Optional[List[str]] = None
) -> dict:
    """
    Check if Ultimate Smile Design covers a specific city, or find the nearest authorized city and dentists.
    
    Args:
        city (str): The name of the city to check.
        exclude_cities (list, optional): Cities rejected by the caller or to be excluded.
        
    Returns:
        dict: A dictionary containing the coverage status, nearest covered city, and authorized doctor names.
    """
    from agent.tools.get_nearest_dentists import get_nearest_dentists

    city_clean = (city or "").strip()
    city_lower = city_clean.lower()
    exclude_clean = [ec.strip().lower() for ec in (exclude_cities or []) if ec and ec.strip()]

    # Load covered cities list
    covered_cities = get_covered_cities()

    # Match city
    matched_city = None
    for c in covered_cities:
        if c.lower() == city_lower:
            matched_city = c
            break

    # If the city is covered and NOT in the exclusion list
    if matched_city and matched_city.lower() not in exclude_clean:
        doc_res = get_nearest_dentists(city=matched_city, limit=3)
        doctors = doc_res.get("dentists", [])
        doctors_phrase = _format_doctor_names(doctors)
        top_doc = doc_res.get("top_recommended_doctor") or (doctors[0] if doctors else "")

        if len(doctors) == 1:
            spoken_offer = f"We have {top_doc} in {matched_city}. Should I confirm your appointment with them as your nearest dentist?"
        elif len(doctors) > 1:
            doctor_clause = f", including {doctors_phrase}"
            spoken_offer = f"We have authorized smile designers in {matched_city}{doctor_clause}. Should I confirm your appointment with your nearest dentist, {top_doc}?"
        else:
            spoken_offer = f"We have authorized smile designers in {matched_city}. Would you like me to arrange your consultation there?"

        return {
            "covered": True,
            "city": matched_city,
            "dentists": doctors,
            "top_recommended_doctor": top_doc,
            "result": f"Coverage Confirmed. {spoken_offer} Tell the caller: '{spoken_offer}'",
            "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
        }
    else:
        # Resolve the closest authorized city from the 51-dentist API excluding rejected cities
        ranked_cities = get_ranked_covered_cities(city_clean, exclude_cities=exclude_cities, limit=3)
        nearest_city = ranked_cities[0] if ranked_cities else "Ahmedabad"

        doc_res = get_nearest_dentists(city=nearest_city, limit=3)
        doctors = doc_res.get("dentists", [])
        doctors_phrase = _format_doctor_names(doctors)
        top_doc = doc_res.get("top_recommended_doctor") or (doctors[0] if doctors else "")

        if len(doctors) == 1:
            doctor_clause = f" We have {top_doc} in {nearest_city}."
            ask_clause = f" Should I confirm your appointment with them as your nearest dentist?"
        elif len(doctors) > 1:
            doctor_clause = f" We have {doctors_phrase} in {nearest_city}."
            ask_clause = f" Should I confirm your appointment with your nearest dentist, {top_doc}?"
        else:
            doctor_clause = ""
            ask_clause = " Shall I book your appointment there?"

        if exclude_clean:
            rejected_name = exclude_cities[0] if exclude_cities else "that city"
            speech_msg = (
                f"Understood. If you prefer not to visit {rejected_name}, "
                f"our next nearest authorized smile designer is in {nearest_city}.{doctor_clause}{ask_clause}"
            )
            result_msg = (
                f"Understood that {rejected_name} is not preferred. "
                f"The next nearest authorized smile designer is in {nearest_city}.{doctor_clause} "
                f"Tell the caller: '{speech_msg}' "
                f"STRICT RULES: DO NOT mention any intermediate un-covered town (such as Bhavnagar). "
                f"DO NOT say we do not have dentists there. Mention ONLY {nearest_city} and the doctor names. "
                "DO NOT use the word 'clinic'. DO NOT say 'USD smile designer'. DO NOT mention km or distance numbers."
            )
        else:
            speech_msg = (
                f"Currently, we do not have an authorized smile designer in {city_clean}, "
                f"but our nearest one is in {nearest_city}.{doctor_clause}{ask_clause}"
            )
            result_msg = (
                f"Currently, we do not have an authorized smile designer in {city_clean}. "
                f"The nearest authorized smile designer is in {nearest_city}.{doctor_clause} "
                f"Tell the caller: '{speech_msg}' "
                f"STRICT RULES: DO NOT mention any other un-covered town. Mention ONLY {nearest_city} and the doctor names. "
                "DO NOT use the word 'clinic'. DO NOT say 'USD smile designer'. DO NOT mention km or distance numbers."
            )

        return {
            "covered": False,
            "city": city_clean,
            "nearest_city": nearest_city,
            "nearest_covered_city": nearest_city,
            "dentists": doctors,
            "top_recommended_doctor": top_doc,
            "result": result_msg,
            "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
        }
