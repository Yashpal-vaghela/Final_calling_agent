"""
Script to synchronize and verify live dentist data directly from the
Ultimate Smile Design production API and push to Redis L2 cache.
Zero Static Fallback — Does NOT write to authorized_dentists.json.
100% Asynchronous execution with httpx and DentistDirectoryService.
"""

import os
import sys
import json
import asyncio
from typing import Dict, Any, List

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.services.dentist_service import (
    get_dentist_service,
    CACHE_KEY_DIRECTORY,
    TTL_DIRECTORY_SECONDS
)

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
COVERED_CITIES_FILE = os.path.join(DATA_DIR, "covered_cities.json")


async def sync_dentist_data_async() -> Dict[str, Any]:
    """
    Asynchronously queries the live USD dentist API:
      1. Fetches full live directory of dentists.
      2. Normalizes and groups doctors by canonical city.
      3. Synchronizes Redis L2 cache (`dentists:directory:all`).
      4. Updates `data/covered_cities.json` with active covered cities.
      5. Pure API architecture — zero static JSON doctor list written.
    """
    service = get_dentist_service()
    # Invalidate local L1 cache to force a fresh fetch from the live API
    service.invalidate_cache()

    print("[Dentist Sync] Fetching fresh directory from live API...")
    raw_dentists = await service.fetch_from_api_async()
    if not raw_dentists:
        print("[Dentist Sync Error] Live API unreachable or empty response. Aborting.")
        return {"status": "error", "message": "API unreachable"}

    directory = service._normalize_dentists(raw_dentists)
    service._l1_directory = directory
    service._l1_raw_dentists = raw_dentists

    # Synchronize to Redis L2 cache
    redis_synced = False
    try:
        from agent.cache import cache_set
        redis_synced = await cache_set(CACHE_KEY_DIRECTORY, directory, ttl_seconds=TTL_DIRECTORY_SECONDS)
        print(f"[Dentist Sync] Redis L2 cache synchronized ({CACHE_KEY_DIRECTORY}, TTL: {TTL_DIRECTORY_SECONDS}s): {redis_synced}")
    except Exception as e:
        print(f"[Dentist Sync Warning] Redis L2 sync skipped: {e}")

    # Update covered_cities.json
    all_covered = sorted(list(directory.keys()))
    try:
        with open(COVERED_CITIES_FILE, "w", encoding="utf-8") as f:
            json.dump(all_covered, f, indent=4, ensure_ascii=False)
        print(f"[Dentist Sync] Updated {len(all_covered)} covered cities in {COVERED_CITIES_FILE}")
    except Exception as e:
        print(f"[Dentist Sync Warning] Could not update {COVERED_CITIES_FILE}: {e}")

    # Summary statistics
    total_doctors = sum(len(docs) for docs in directory.values())
    city_breakdown = {city: len(docs) for city, docs in sorted(directory.items())}

    print("\n--- Live API Dentist Sync Report ---")
    print(f"Total Doctors: {len(raw_dentists)} (Normalized mappings: {total_doctors})")
    print(f"Active Cities:  {len(directory)}")
    print(f"Redis L2 Sync:  {'SUCCESS' if redis_synced else 'SKIPPED / UNAVAILABLE'}")
    print(f"City Breakdown: {json.dumps(city_breakdown, indent=2)}")
    print("-------------------------------------\n")

    return {
        "status": "success",
        "total_api_dentists": len(raw_dentists),
        "cities_count": len(directory),
        "redis_synced": redis_synced,
        "cities": list(directory.keys()),
    }


def sync_dentist_data() -> Dict[str, Any]:
    """Synchronous wrapper for sync_dentist_data_async."""
    return asyncio.run(sync_dentist_data_async())


async def fetch_all_dentists_from_api_async(client=None) -> List[Dict[str, Any]]:
    """Legacy compatibility alias: fetches raw dentists from DentistDirectoryService."""
    return await get_dentist_service().fetch_from_api_async()


def fetch_all_dentists_from_api() -> List[Dict[str, Any]]:
    """Legacy compatibility sync alias: fetches raw dentists from DentistDirectoryService."""
    return get_dentist_service().fetch_from_api_sync()


if __name__ == "__main__":
    result = asyncio.run(sync_dentist_data_async())
    print("Sync Result:", json.dumps(result, indent=2))
