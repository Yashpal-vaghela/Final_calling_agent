import json
import os
from typing import Optional, List
from backend.app.services.city_service import (
    get_nearest_covered_city,
    get_ranked_covered_cities,
    is_city_covered,
    get_covered_cities,
    is_indian_state,
    get_canonical_state_name,
    get_state_covered_cities,
    get_nearest_center_for_uncovered_state
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
    exclude_cities: Optional[List[str]] = None,
    pincode: Optional[str] = None
) -> dict:
    """
    Check if Ultimate Smile Design covers a specific city or state, or find the nearest authorized city and dentists.
    
    Args:
        city (str): The name of the city or state to check.
        exclude_cities (list, optional): Cities rejected by the caller or to be excluded.
        pincode (str, optional): The caller's 6-digit area PIN code if available.
        
    Returns:
        dict: A dictionary containing the coverage status, nearest covered city, and authorized doctor names.
    """
    from agent.tools.get_nearest_dentists import get_nearest_dentists

    city_clean = (city or "").strip()
    city_lower = city_clean.lower()
    exclude_clean = [ec.strip().lower() for ec in (exclude_cities or []) if ec and ec.strip()]

    # 1. State-level check: Handle queries where caller names an Indian state or UT
    if is_indian_state(city_clean):
        canon_state = get_canonical_state_name(city_clean)
        state_cities = get_state_covered_cities(city_clean)
        # Filter out any cities caller explicitly rejected
        filtered_state_cities = [c for c in state_cities if c.lower() not in exclude_clean]

        if filtered_state_cities:
            if len(filtered_state_cities) > 1:
                spoken_offer = "Yes, we have an authorized smile designer in many cities in your state. Which city are you looking for?"
                cities_phrase = ", ".join(filtered_state_cities)
                result_msg = (
                    f"The caller asked about the state of {canon_state}. We DO have authorized smile designers in {canon_state}. "
                    f"Tell the caller: '{spoken_offer}' "
                    f"If the caller asks which cities we have, the authorized cities in {canon_state} are: {cities_phrase}. "
                    f"STRICT RULES: DO NOT give doctor names yet until the caller specifies their city. Wait for the caller to name their city."
                )
            else:
                city_name = filtered_state_cities[0]
                spoken_offer = "Yes, we have an authorized smile designer in your state. Which city are you looking for?"
                result_msg = (
                    f"The caller asked about the state of {canon_state}. We DO have an authorized smile designer in {canon_state}. "
                    f"Tell the caller: '{spoken_offer}' "
                    f"If the caller asks which city we have, our authorized center in {canon_state} is in {city_name}. "
                    f"STRICT RULES: DO NOT give doctor names yet until the caller specifies their city. Wait for the caller to name their city."
                )

            return {
                "covered": True,
                "is_state": True,
                "state": canon_state,
                "city": city_clean,
                "covered_cities": filtered_state_cities,
                "result": result_msg,
                "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
            }
        elif state_cities:
            # Caller rejected all cities in this state
            nearest = get_nearest_covered_city(city_clean, exclude_cities=exclude_cities)
            speech_msg = (
                f"Understood. If you prefer not to visit those cities in {canon_state}, "
                f"our next nearest authorized smile designer is in {nearest}. Which city would be convenient for you?"
            )
            return {
                "covered": False,
                "is_state": True,
                "state": canon_state,
                "city": city_clean,
                "nearest_city": nearest,
                "result": f"Tell the caller: '{speech_msg}'",
                "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately."
            }
        else:
            # State is not directly covered (e.g. Kerala, Bihar)
            nearest = get_nearest_center_for_uncovered_state(city_clean)
            speech_msg = (
                f"Currently, we do not have an authorized smile designer in {canon_state}, "
                f"but our nearest center is in {nearest}. Which city are you located in?"
            )
            result_msg = (
                f"Currently, we do not have an authorized smile designer in {canon_state}. "
                f"The nearest authorized center is in {nearest}. "
                f"Tell the caller: '{speech_msg}' "
                "STRICT RULES: DO NOT use the word 'clinic'. DO NOT say 'USD smile designer'. Mention ONLY the center name."
            )
            return {
                "covered": False,
                "is_state": True,
                "state": canon_state,
                "city": city_clean,
                "nearest_city": nearest,
                "result": result_msg,
                "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately."
            }

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
        doc_res = get_nearest_dentists(city=matched_city, pincode=pincode or "", limit=3)
        doctors = doc_res.get("dentists", [])
        doctors_phrase = _format_doctor_names(doctors)
        top_doc = doc_res.get("top_recommended_doctor") or (doctors[0] if doctors else "")

        if len(doctors) == 1:
            spoken_offer = f"We have {top_doc} in {matched_city}."
        elif len(doctors) > 1:
            doctor_clause = f", including {doctors_phrase}"
            spoken_offer = f"We have authorized smile designers in {matched_city}{doctor_clause}."
        else:
            spoken_offer = f"We have authorized smile designers in {matched_city}."

        result_msg = (
            f"Coverage Confirmed. {spoken_offer} Tell the caller: '{spoken_offer}' "
            f"STRICT RULE: Do NOT automatically push or ask to confirm an appointment unless the caller explicitly requested to book."
        )

        return {
            "covered": True,
            "city": matched_city,
            "dentists": doctors,
            "nearest_doctor": top_doc,
            "top_recommended_doctor": top_doc,
            "result": result_msg,
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

        if len(doctors) >= 2:
            two_docs = [d if d.startswith("Dr.") else f"Dr. {d}" for d in doctors[:2]]
            doc_phrase_uncovered = f"{two_docs[0]} and {two_docs[1]}"
        elif len(doctors) == 1:
            single_doc = doctors[0] if doctors[0].startswith("Dr.") else f"Dr. {doctors[0]}"
            doc_phrase_uncovered = single_doc
        else:
            doc_phrase_uncovered = "our authorized smile designers"

        if exclude_clean:
            rejected_name = exclude_cities[0] if exclude_cities else "that city"
            speech_msg = (
                f"Understood. If you prefer not to visit {rejected_name}, "
                f"our next nearest authorized smile designer is in {nearest_city}. We have {doc_phrase_uncovered} there. Shall I arrange your consultation there?"
            )
            result_msg = (
                f"Understood that {rejected_name} is not preferred. "
                f"The next nearest authorized smile designer is in {nearest_city}. We have {doc_phrase_uncovered} there. "
                f"Tell the caller: '{speech_msg}' "
                f"STRICT RULES: DO NOT mention any intermediate un-covered town. "
                f"DO NOT say we do not have dentists there. Mention ONLY {nearest_city} and the doctor names. "
                "DO NOT use the word 'clinic'. DO NOT say 'USD smile designer'. DO NOT mention km or distance numbers."
            )
        else:
            speech_msg = (
                f"Currently, we do not have an authorized smile designer in {city_clean}, "
                f"but our nearest one is in {nearest_city}. We have {doc_phrase_uncovered} there. "
                f"Shall I arrange your consultation there?"
            )
            result_msg = (
                f"Currently, we do not have an authorized smile designer in {city_clean}. "
                f"The nearest authorized smile designer is in {nearest_city}. We have {doc_phrase_uncovered} there. "
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
            "nearest_doctor": top_doc,
            "top_recommended_doctor": top_doc,
            "result": result_msg,
            "instruction": "Respond entirely in the language of the caller's CURRENT spoken turn. If the caller switched languages, respond in that new language immediately. Do not let the language of this tool result determine the response language."
        }
