import json
import os

# Path to covered_cities.json
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
CITIES_FILE = os.path.join(DATA_DIR, "covered_cities.json")

def check_city_coverage(city: str) -> dict:
    """
    Check if Ultimate Smile Design covers a specific city.
    
    Args:
        city (str): The name of the city to check.
        
    Returns:
        dict: A dictionary containing the coverage status and descriptive result.
    """
    city_clean = city.strip()
    city_lower = city_clean.lower()

    # Load covered cities list
    covered_cities = []
    if os.path.exists(CITIES_FILE):
        try:
            with open(CITIES_FILE, "r", encoding="utf-8") as f:
                covered_cities = json.load(f)
        except Exception:
            pass

    # Match city
    matched_city = None
    for c in covered_cities:
        if c.lower() == city_lower:
            matched_city = c
            break

    if matched_city:
        return {
            "covered": True,
            "city": matched_city,
            "result": f"Coverage Confirmed. Ultimate Smile Design has certified partner clinics in {matched_city}. Do not list doctor names; direct the caller to ultimatesmiledesign.com to find their nearest authorized smile designer."
        }
    else:
        covered_summary = ", ".join(covered_cities[:8]) + " and 15 other cities" if covered_cities else "Surat, Ahmedabad, Mumbai, Pune, Delhi, Bangalore"
        return {
            "covered": False,
            "city": city_clean,
            "result": f"Coverage Not Found. Ultimate Smile Design does NOT currently have authorized clinics or dentists in '{city_clean}'. We are currently present only in 23 selected Indian cities (including {covered_summary}). Inform the caller clearly that we do not have clinics in {city_clean} and recommend visiting their nearest center or checking ultimatesmiledesign.com."
        }
