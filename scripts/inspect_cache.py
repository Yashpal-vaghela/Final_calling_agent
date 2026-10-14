"""
Live Cache & RAM Inspector for Ultimate Smile Design Calling Agent
Run this script with active virtualenv:
    python scripts/inspect_cache.py
Or directly:
    .\\venv\\Scripts\\python.exe scripts/inspect_cache.py
"""

import os
import sys
import json
import asyncio
import io
from typing import Dict, Any, List
from dotenv import load_dotenv

# Ensure stdout handles UTF-8 on Windows terminal
if sys.platform == "win32" and isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.cache.redis_client import get_redis
from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS
from backend.app.services.dentist_service import get_dentist_service


async def inspect_redis():
    print("\n" + "=" * 70)
    print("  [REDIS L2 CLOUD CACHE INSPECTION]")
    print("=" * 70)

    redis_enabled = os.getenv("REDIS_ENABLED", "false").lower() in ("1", "true", "yes")
    redis_url = os.getenv("REDIS_URL", "")
    print(f"REDIS_ENABLED in .env : {redis_enabled}")
    print(f"REDIS_URL configured  : {'Yes (rediss://...)' if redis_url else 'No'}")

    r = await get_redis()
    if not r:
        print("\n[!] Could not connect to Redis (or REDIS_ENABLED=false).")
        return

    print("[+] Successfully connected to Redis singleton client!\n")

    keys = await r.keys("*")
    print(f"Total keys stored in Redis: {len(keys)}")
    if not keys:
        print("   (Redis is currently empty)")
        return

    # Categorize keys
    caller_keys = [k for k in keys if k.startswith("call:")]
    faq_keys = [k for k in keys if k.startswith("faq:")]
    dentist_keys = [k for k in keys if k.startswith("dentists:") or k.startswith("dentist:")]
    city_keys = [k for k in keys if k.startswith("city:") or k.startswith("state:")]
    other_keys = [k for k in keys if k not in caller_keys + faq_keys + dentist_keys + city_keys]

    # 1. Caller Contexts
    print("\n" + "-" * 70)
    print(f" [1] CALLER CONTEXTS ({len(caller_keys)} keys)")
    print("-" * 70)
    for k in sorted(caller_keys):
        ttl = await r.ttl(k)
        val = await r.get(k)
        try:
            parsed = json.loads(val)
            name = parsed.get("name", "N/A")
            phone = parsed.get("phone", "N/A")
            city = parsed.get("city", "N/A")
            print(f"  * Key: {k} (TTL: {ttl}s)")
            print(f"    --> Name: '{name}' | Phone: '{phone}' | City: '{city}'")
        except Exception:
            print(f"  * Key: {k} (TTL: {ttl}s) -> {val[:80]}...")

    # 2. FAQs
    print("\n" + "-" * 70)
    print(f" [2] FAQ KNOWLEDGE CACHE ({len(faq_keys)} keys)")
    print("-" * 70)
    for k in sorted(faq_keys):
        ttl = await r.ttl(k)
        val = await r.get(k)
        try:
            parsed = json.loads(val)
            topic = parsed.get("topic", "N/A")
            found = parsed.get("found", False)
            ans = (parsed.get("answer", "") or "")[:70].replace("\n", " ")
            print(f"  * Key: {k} (TTL: {ttl}s)")
            print(f"    --> Found: {found} | Topic: '{topic}'")
            print(f"    --> Preview: \"{ans}...\"")
        except Exception:
            print(f"  * Key: {k} (TTL: {ttl}s)")

    # 3. Cities & States
    print("\n" + "-" * 70)
    print(f" [3] CITY & STATE COVERAGE ({len(city_keys)} keys)")
    print("-" * 70)
    for k in sorted(city_keys):
        ttl = await r.ttl(k)
        val = await r.get(k)
        try:
            parsed = json.loads(val)
            covered = parsed.get("covered", False)
            dentists = parsed.get("dentists", [])
            print(f"  * Key: {k} (TTL: {ttl}s)")
            print(f"    --> Covered: {covered} | Doctors available: {len(dentists)} {dentists[:2]}")
        except Exception:
            print(f"  * Key: {k} (TTL: {ttl}s)")

    # 4. Dentists
    print("\n" + "-" * 70)
    print(f" [4] DENTIST SEARCH & DIRECTORY ({len(dentist_keys)} keys)")
    print("-" * 70)
    for k in sorted(dentist_keys):
        ttl = await r.ttl(k)
        val = await r.get(k)
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict) and "dentists" in parsed:
                docs = parsed.get("dentists", [])
                print(f"  * Key: {k} (TTL: {ttl}s) -> {len(docs)} doctors: {docs[:3]}")
            elif isinstance(parsed, dict) and "is_authorized" in parsed:
                auth = parsed.get("is_authorized", False)
                doc = parsed.get("doctor_name", "")
                print(f"  * Key: {k} (TTL: {ttl}s) -> Authorized: {auth} ({doc})")
            elif k == "dentists:directory:all":
                print(f"  * Key: {k} (TTL: {ttl}s) -> Full Directory ({len(parsed)} cities covered)")
            else:
                print(f"  * Key: {k} (TTL: {ttl}s)")
        except Exception:
            print(f"  * Key: {k} (TTL: {ttl}s)")

    if other_keys:
        print("\n" + "-" * 70)
        print(f" [5] OTHER CACHE KEYS ({len(other_keys)} keys)")
        print("-" * 70)
        for k in sorted(other_keys):
            ttl = await r.ttl(k)
            print(f"  * Key: {k} (TTL: {ttl}s)")


def inspect_ram():
    print("\n" + "=" * 70)
    print("  [LOCAL RAM L1 IN-MEMORY INSPECTION]")
    print("=" * 70)

    # 1. Caller Contexts in RAM
    print(f"1. ACTIVE_CALLER_CONTEXTS dictionary:")
    print(f"   Total indices in RAM: {len(ACTIVE_CALLER_CONTEXTS)}")
    for idx_key, data in list(ACTIVE_CALLER_CONTEXTS.items())[:10]:
        name = data.get("name", "N/A")
        phone = data.get("phone", "N/A")
        print(f"   * Index: '{idx_key}' -> Name: '{name}', Phone: '{phone}'")
    if len(ACTIVE_CALLER_CONTEXTS) > 10:
        print(f"   ... and {len(ACTIVE_CALLER_CONTEXTS) - 10} more indices.")

    # 2. DentistService Singleton RAM Cache
    service = get_dentist_service()
    l1_valid = service.is_l1_valid()
    l1_docs = service._l1_directory
    print(f"\n2. DentistDirectoryService L1 Cache (Singleton in RAM):")
    print(f"   L1 Cache Valid: {l1_valid}")
    if l1_docs:
        print(f"   Cached Cities: {len(l1_docs)} ({', '.join(list(l1_docs.keys())[:5])}...)")
        total_docs = sum(len(d) for d in l1_docs.values())
        print(f"   Total Doctors in RAM: {total_docs}")
    else:
        print("   (RAM directory is empty; will warm on first API / Redis query)")

    print("\n" + "=" * 70 + "\n")


async def main():
    await inspect_redis()
    inspect_ram()


if __name__ == "__main__":
    asyncio.run(main())
