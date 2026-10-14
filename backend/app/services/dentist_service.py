"""
Centralized Live API Dentist Directory Service.
Source of Truth: https://ultimatesmiledesign.com/api/dentists-list/pincode/
Topology: Pure Live API + L1 Python RAM Cache (0.001 ms) + L2 Redis Cloud Cache (3600s TTL).
Zero Static JSON Fallback.
"""

import os
import re
import json
import time
import logging
import asyncio
from typing import Dict, Any, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

REMOTE_API_URL = "https://ultimatesmiledesign.com/api/dentists-list/pincode/"
LOCAL_API_URL = "http://192.168.0.161:1020/api/dentists-list/pincode/"
DEFAULT_QUERY_PINCODE = "395006"
TTL_DIRECTORY_SECONDS = 3600  # 1 hour cache TTL

CACHE_KEY_DIRECTORY = "dentists:directory:all"
CACHE_KEY_RAW = "dentists:directory:raw"

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

# Indic (Gujarati & Devanagari) script to Latin phonetic transliteration map
INDIC_TO_LATIN_MAP = {
    # Gujarati consonants
    0x0A95: 'k', 0x0A96: 'kh', 0x0A97: 'g', 0x0A98: 'gh', 0x0A99: 'ng',
    0x0A9A: 'ch', 0x0A9B: 'chh', 0x0A9C: 'j', 0x0A9D: 'jh', 0x0A9E: 'ny',
    0x0A9F: 't', 0x0AA0: 'th', 0x0AA1: 'd', 0x0AA2: 'dh', 0x0AA3: 'n',
    0x0AA4: 't', 0x0AA5: 'th', 0x0AA6: 'd', 0x0AA7: 'dh', 0x0AA8: 'n',
    0x0AAA: 'p', 0x0AAB: 'ph', 0x0AAC: 'b', 0x0AAD: 'bh', 0x0AAE: 'm',
    0x0AAF: 'y', 0x0AB0: 'r', 0x0AB2: 'l', 0x0AB3: 'l', 0x0AB5: 'v',
    0x0AB6: 'sh', 0x0AB7: 'sh', 0x0AB8: 's', 0x0AB9: 'h',
    # Gujarati vowels & matras
    0x0A85: 'a', 0x0A86: 'aa', 0x0A87: 'i', 0x0A88: 'ee', 0x0A89: 'u', 0x0A8A: 'oo',
    0x0A8F: 'e', 0x0A90: 'ai', 0x0A93: 'o', 0x0A94: 'au',
    0x0ABE: 'a', 0x0ABF: 'i', 0x0AC0: 'ee', 0x0AC1: 'u', 0x0AC2: 'oo',
    0x0AC7: 'e', 0x0AC8: 'ai', 0x0ACB: 'o', 0x0ACC: 'au',
    # Devanagari (Hindi) consonants
    0x0915: 'k', 0x0916: 'kh', 0x0917: 'g', 0x0918: 'gh', 0x0919: 'ng',
    0x091A: 'ch', 0x091B: 'chh', 0x091C: 'j', 0x091D: 'jh', 0x091E: 'ny',
    0x091F: 't', 0x0920: 'th', 0x0921: 'd', 0x0922: 'dh', 0x0923: 'n',
    0x0924: 't', 0x0925: 'th', 0x0926: 'd', 0x0927: 'dh', 0x0928: 'n',
    0x092A: 'p', 0x092B: 'ph', 0x092C: 'b', 0x092D: 'bh', 0x092E: 'm',
    0x092F: 'y', 0x0930: 'r', 0x0932: 'l', 0x0935: 'v',
    0x0936: 'sh', 0x0937: 'sh', 0x0938: 's', 0x0939: 'h',
    # Devanagari vowels & matras
    0x0905: 'a', 0x0906: 'aa', 0x0907: 'i', 0x0908: 'ee', 0x0909: 'u', 0x090A: 'oo',
    0x090F: 'e', 0x0910: 'ai', 0x0913: 'o', 0x0914: 'au',
    0x093E: 'a', 0x093F: 'i', 0x0940: 'ee', 0x0941: 'u', 0x0942: 'oo',
    0x0947: 'e', 0x0948: 'ai', 0x094B: 'o', 0x094C: 'au',
}


def indic_to_latin(text: str) -> str:
    """Transliterates Gujarati and Hindi Devanagari text into phonetic Latin characters."""
    if not text:
        return ""
    return "".join(INDIC_TO_LATIN_MAP.get(ord(c), c) for c in text)


def extract_city_from_address(address: str) -> Optional[str]:
    """Matches a known canonical city from a dentist address string."""
    if not address:
        return None
    for alias, canonical in CITY_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", address, re.IGNORECASE):
            return canonical

    for city in KNOWN_CITIES:
        if re.search(r"\b" + re.escape(city) + r"\b", address, re.IGNORECASE):
            return city

    return None


class DentistDirectoryService:
    """
    Manages live API fetching and multi-tier caching (RAM L1 + Redis L2)
    for authorized dentists. No static file dependency.
    """

    def __init__(self):
        self._l1_directory: Optional[Dict[str, List[str]]] = None
        self._l1_raw_dentists: Optional[List[Dict[str, Any]]] = None
        self._l1_cached_at: float = 0.0

    def is_l1_valid(self) -> bool:
        """Returns True if L1 RAM cache is populated and unexpired."""
        return (
            self._l1_directory is not None
            and (time.time() - self._l1_cached_at) < TTL_DIRECTORY_SECONDS
        )

    def _normalize_dentists(self, dentists_list: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Groups raw API dentists by canonical city name."""
        by_city: Dict[str, List[str]] = {}

        for d in dentists_list:
            raw_name = (d.get("name") or "").strip()
            if not raw_name:
                continue

            address = d.get("address") or ""
            city = extract_city_from_address(address) or "Surat"

            if city not in by_city:
                by_city[city] = []
            if raw_name not in by_city[city]:
                by_city[city].append(raw_name)

        # Re-apply canonical aliases (e.g. Delhi -> New Delhi, Gurgaon -> Gurugram)
        alias_mappings = {
            "Delhi": "New Delhi",
            "Gurgaon": "Gurugram",
            "Bengaluru": "Bangalore",
            "Surendranagar": "Dhrangadhra"
        }
        for alias_city, canon_city in alias_mappings.items():
            if canon_city in by_city:
                by_city[alias_city] = by_city[canon_city]

        return by_city

    async def fetch_from_api_async(self) -> List[Dict[str, Any]]:
        """Fetches the full dentist directory from the live USD API."""
        async with httpx.AsyncClient(timeout=8.0) as client:
            for base_url in [REMOTE_API_URL, LOCAL_API_URL]:
                try:
                    resp = await client.get(base_url, params={"pincode": DEFAULT_QUERY_PINCODE})
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("status") == "success":
                            dentists = data.get("data", [])
                            if dentists:
                                logger.info(f"[DentistService] Successfully fetched {len(dentists)} dentists from {base_url}")
                                return dentists
                except Exception as e:
                    logger.warning(f"[DentistService] Could not reach {base_url}: {e}")
                    continue
        return []

    def fetch_from_api_sync(self) -> List[Dict[str, Any]]:
        """Synchronously fetches dentist directory from live USD API."""
        for base_url in [REMOTE_API_URL, LOCAL_API_URL]:
            try:
                resp = httpx.get(base_url, params={"pincode": DEFAULT_QUERY_PINCODE}, timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("status") == "success":
                        dentists = data.get("data", [])
                        if dentists:
                            logger.info(f"[DentistService Sync] Successfully fetched {len(dentists)} dentists from {base_url}")
                            return dentists
            except Exception as e:
                logger.warning(f"[DentistService Sync] Could not reach {base_url}: {e}")
                continue
        return []

    async def get_directory_async(self) -> Dict[str, List[str]]:
        """
        Returns { "City": ["Dr. Name", ...] } from RAM L1 -> Redis L2 -> Live API.
        Guarantees sub-millisecond return on warm RAM cache.
        """
        # 1. Check RAM L1
        if self.is_l1_valid() and self._l1_directory:
            logger.debug("[DentistService] [DATA SOURCE: RAM_L1] Directory served from RAM L1")
            return self._l1_directory

        # 2. Check Redis L2
        try:
            from agent.cache import cache_get, cache_set
            redis_data = await cache_get(CACHE_KEY_DIRECTORY)
            if redis_data and isinstance(redis_data, dict):
                self._l1_directory = redis_data
                self._l1_cached_at = time.time()
                logger.info("[DentistService] [DATA SOURCE: REDIS_L2] Directory served from Redis L2 (warmed RAM L1)")
                return self._l1_directory
        except Exception as e:
            logger.debug(f"[DentistService] Redis check failed: {e}")

        # 3. Fetch from Live API
        raw = await self.fetch_from_api_async()
        if raw:
            directory = self._normalize_dentists(raw)
            self._l1_directory = directory
            self._l1_raw_dentists = raw
            self._l1_cached_at = time.time()
            logger.info(f"[DentistService] [DATA SOURCE: DENTIST_LIVE_API] Directory fetched from live API ({len(raw)} dentists across {len(directory)} cities)")

            # Save to Redis L2 in background
            try:
                from agent.cache import cache_set
                asyncio.create_task(cache_set(CACHE_KEY_DIRECTORY, directory, ttl_seconds=TTL_DIRECTORY_SECONDS))
            except Exception:
                pass

            return self._l1_directory

        # If live API failed and no cache, return empty dict honestly (no static JSON fallback)
        logger.error("[DentistService] Live API unreachable and no cache available.")
        return {}

    def get_directory_sync(self) -> Dict[str, List[str]]:
        """Synchronous version of get_directory for synchronous callers."""
        # 1. Check RAM L1
        if self.is_l1_valid() and self._l1_directory:
            logger.debug("[DentistService Sync] [DATA SOURCE: RAM_L1] Directory served from RAM L1")
            return self._l1_directory

        # 2. Check Redis L2
        try:
            from agent.cache import cache_get_sync, cache_set_sync
            redis_data = cache_get_sync(CACHE_KEY_DIRECTORY)
            if redis_data and isinstance(redis_data, dict):
                self._l1_directory = redis_data
                self._l1_cached_at = time.time()
                logger.info("[DentistService Sync] [DATA SOURCE: REDIS_L2] Directory served from Redis L2 (warmed RAM L1)")
                return self._l1_directory
        except Exception:
            pass

        # 3. Fetch from Live API
        raw = self.fetch_from_api_sync()
        if raw:
            directory = self._normalize_dentists(raw)
            self._l1_directory = directory
            self._l1_raw_dentists = raw
            self._l1_cached_at = time.time()
            logger.info(f"[DentistService Sync] [DATA SOURCE: DENTIST_LIVE_API] Directory fetched from live API ({len(raw)} dentists across {len(directory)} cities)")

            try:
                from agent.cache import cache_set_sync
                cache_set_sync(CACHE_KEY_DIRECTORY, directory, ttl_seconds=TTL_DIRECTORY_SECONDS)
            except Exception:
                pass

            return self._l1_directory

        logger.error("[DentistService Sync] Live API unreachable and no cache available.")
        return {}

    async def get_raw_dentists_async(self) -> List[Dict[str, Any]]:
        """Returns the raw list of dentists from RAM L1 -> Live API."""
        if self.is_l1_valid() and self._l1_raw_dentists:
            return self._l1_raw_dentists
        raw = await self.fetch_from_api_async()
        if raw:
            self._l1_raw_dentists = raw
            self._l1_directory = self._normalize_dentists(raw)
            self._l1_cached_at = time.time()
        return self._l1_raw_dentists or []

    def get_raw_dentists_sync(self) -> List[Dict[str, Any]]:
        """Synchronously returns raw list of dentists."""
        if self.is_l1_valid() and self._l1_raw_dentists:
            return self._l1_raw_dentists
        raw = self.fetch_from_api_sync()
        if raw:
            self._l1_raw_dentists = raw
            self._l1_directory = self._normalize_dentists(raw)
            self._l1_cached_at = time.time()
        return self._l1_raw_dentists or []

    def invalidate_cache(self) -> None:
        """Forces cache invalidation to trigger an immediate live API refresh."""
        self._l1_directory = None
        self._l1_raw_dentists = None
        self._l1_cached_at = 0.0
        try:
            from agent.cache import cache_delete_sync
            cache_delete_sync(CACHE_KEY_DIRECTORY)
        except Exception:
            pass


# Module singleton instance
_singleton_service: Optional[DentistDirectoryService] = None


def get_dentist_service() -> DentistDirectoryService:
    """Returns the singleton instance of DentistDirectoryService."""
    global _singleton_service
    if _singleton_service is None:
        _singleton_service = DentistDirectoryService()
    return _singleton_service
