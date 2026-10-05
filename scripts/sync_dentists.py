"""
Script to synchronize local authorized_dentists.json and covered_cities.json
directly from the Ultimate Smile Design production API and India Post Postal PIN Code API.
100% Asynchronous execution with httpx.AsyncClient.
"""

import os
import re
import json
import asyncio
import httpx
from typing import Dict, Any, List, Optional, Tuple

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
AUTH_DENTISTS_FILE = os.path.join(DATA_DIR, "authorized_dentists.json")
COVERED_CITIES_FILE = os.path.join(DATA_DIR, "covered_cities.json")

REMOTE_API_URL = "https://ultimatesmiledesign.com/api/dentists-list/pincode/"
LOCAL_API_URL = "http://192.168.0.161:1020/api/dentists-list/pincode/"
POSTAL_PINCODE_API_URL = "https://api.postalpincode.in/pincode/"

KNOWN_CITIES = [
    "Ahmedabad", "Bangalore", "Bharuch", "Chennai", "Dhrangadhra", "Faridkot",
    "Guntur", "Gurugram", "Guwahati", "Gwalior", "Halvad", "Hyderabad",
    "Indore", "Jamnagar", "Malda", "Mumbai", "New Delhi", "Pune",
    "Rajkot", "Sangli", "Sri Ganganagar", "Surat", "Vadodara"
]

CITY_ALIASES = {
    "delhi": "New Delhi",
    "new delhi": "New Delhi",
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "bengaluru": "Bangalore",
    "bangalore": "Bangalore",
    "surendranagar": "Dhrangadhra",
}


async def fetch_all_dentists_from_api_async(client: Optional[httpx.AsyncClient] = None) -> List[Dict[str, Any]]:
    """Fetches the complete dentists list from the production API asynchronously."""
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        should_close = True

    try:
        for base_url in [REMOTE_API_URL, LOCAL_API_URL]:
            try:
                resp = await client.get(base_url, params={"pincode": "395006"})
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("status") == "success":
                        dentists = data.get("data", [])
                        if dentists:
                            print(f"[Async Sync] Successfully fetched {len(dentists)} dentists from {base_url}")
                            return dentists
            except Exception as e:
                print(f"[Async Sync Warning] Failed to fetch from {base_url}: {e}")
                continue
    finally:
        if should_close:
            await client.aclose()
    return []


def fetch_all_dentists_from_api() -> List[Dict[str, Any]]:
    """Synchronous wrapper for fetch_all_dentists_from_api_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(fetch_all_dentists_from_api_async(), loop).result()
    except RuntimeError:
        pass
    return asyncio.run(fetch_all_dentists_from_api_async())


async def resolve_city_from_postal_api_async(pincode: str, client: httpx.AsyncClient) -> Optional[str]:
    """Asynchronously queries the India Post Postal PIN Code API to determine the district/city."""
    if not pincode or len(pincode) != 6:
        return None
    try:
        resp = await client.get(f"{POSTAL_PINCODE_API_URL}{pincode}", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and data[0].get("Status") == "Success":
                post_offices = data[0].get("PostOffice") or []
                if post_offices:
                    district = (post_offices[0].get("District") or "").strip()
                    if district:
                        return district
    except Exception:
        pass
    return None


def extract_city_from_address(address: str) -> Optional[str]:
    """Matches a known city from the doctor's address string using aliases and known cities."""
    for alias, canonical in CITY_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", address, re.IGNORECASE):
            return canonical

    for city in KNOWN_CITIES:
        if re.search(r"\b" + re.escape(city) + r"\b", address, re.IGNORECASE):
            return city

    return None


async def sync_dentist_data_async() -> Dict[str, Any]:
    """
    Asynchronously syncs local read-only files with the latest dentists from the API.
    Updates:
      1. data/authorized_dentists.json (by city with preserved phonetics)
      2. data/covered_cities.json (sorted list of cities)
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        dentists = await fetch_all_dentists_from_api_async(client=client)
        if not dentists:
            print("[Async Sync Error] Could not fetch dentists from API. Aborting sync.")
            return {"status": "error", "message": "API unreachable"}

        # 1. Load existing authorized_dentists.json to preserve Hindi/Gujarati phonetics
        existing_auth: Dict[str, List[str]] = {}
        if os.path.exists(AUTH_DENTISTS_FILE):
            try:
                with open(AUTH_DENTISTS_FILE, "r", encoding="utf-8") as f:
                    existing_auth = json.load(f)
            except Exception:
                pass

        # Map existing phonetic variations per English doctor name
        phonetic_map: Dict[str, List[str]] = {}
        for city, names in existing_auth.items():
            current_eng = None
            for name in names:
                if re.match(r"^[A-Za-z\s\.\-]+$", name):
                    current_eng = name.strip()
                    if current_eng not in phonetic_map:
                        phonetic_map[current_eng] = []
                else:
                    if current_eng:
                        if name.strip() not in phonetic_map[current_eng]:
                            phonetic_map[current_eng].append(name.strip())

        # Build updated authorized dentists dictionary grouped by city
        new_auth: Dict[str, List[str]] = {}
        covered_cities_set = set(KNOWN_CITIES)

        for d in dentists:
            raw_name = (d.get("name") or "").strip()
            addr = d.get("address") or ""
            city = extract_city_from_address(addr)

            # If address didn't match known cities, check postal API via 6-digit PIN in address
            if not city:
                pin_match = re.search(r"\b(\d{6})\b", addr)
                if pin_match:
                    postal_district = await resolve_city_from_postal_api_async(pin_match.group(1), client)
                    if postal_district:
                        city = postal_district

            if not city:
                city = "Surat"  # fallback default
                
            covered_cities_set.add(city)

            if city not in new_auth:
                new_auth[city] = []

            if raw_name:
                if raw_name not in new_auth[city]:
                    new_auth[city].append(raw_name)
                # Add preserved phonetics if available
                if raw_name in phonetic_map:
                    for ph in phonetic_map[raw_name]:
                        if ph not in new_auth[city]:
                            new_auth[city].append(ph)

        # Re-apply city aliases (Delhi, Gurgaon, Bengaluru, Surendranagar) to ensure check_dentist matches aliases
        alias_mappings = {
            "Delhi": "New Delhi",
            "Gurgaon": "Gurugram",
            "Bengaluru": "Bangalore",
            "Surendranagar": "Dhrangadhra"
        }
        for alias_city, canon_city in alias_mappings.items():
            if canon_city in new_auth:
                new_auth[alias_city] = new_auth[canon_city]

        # Save updated authorized_dentists.json
        with open(AUTH_DENTISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(new_auth, f, indent=2, ensure_ascii=False)
        print(f"[Async Sync] Saved {len(new_auth)} cities to {AUTH_DENTISTS_FILE}")

        # 3. Save sorted covered_cities.json (including recognized aliases)
        all_covered = sorted(list((set(KNOWN_CITIES) & covered_cities_set) | set(alias_mappings.keys())))
        with open(COVERED_CITIES_FILE, "w", encoding="utf-8") as f:
            json.dump(all_covered, f, indent=4, ensure_ascii=False)
        print(f"[Async Sync] Saved {len(all_covered)} covered cities to {COVERED_CITIES_FILE}")

        return {
            "status": "success",
            "dentists_synced": len(dentists),
            "cities_count": len(all_covered)
        }


def sync_dentist_data() -> Dict[str, Any]:
    """Synchronous wrapper for sync_dentist_data_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run_coroutine_threadsafe(sync_dentist_data_async(), loop).result()
    except RuntimeError:
        pass
    return asyncio.run(sync_dentist_data_async())


if __name__ == "__main__":
    result = asyncio.run(sync_dentist_data_async())
    print("Sync Result:", json.dumps(result, indent=2))

