import os
import json
from typing import List

_COVERED_CITIES_CACHE: List[str] = []
_CITIES_LOADED: bool = False

def get_covered_cities() -> List[str]:
    """
    Returns a cached list of covered cities from data/covered_cities.json.
    """
    global _COVERED_CITIES_CACHE, _CITIES_LOADED
    
    if not _CITIES_LOADED:
        json_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "covered_cities.json")
        )
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                _COVERED_CITIES_CACHE = json.load(f)
        _CITIES_LOADED = True
        
    return _COVERED_CITIES_CACHE

def is_city_covered(city_name: str) -> bool:
    """
    Case-insensitive check if a city is in the covered list.
    """
    if not city_name:
        return False
    
    cities = get_covered_cities()
    lower_city = city_name.strip().lower()
    return lower_city in (c.lower() for c in cities)
