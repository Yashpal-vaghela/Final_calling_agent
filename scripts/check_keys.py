import os
import sys
import httpx
import asyncio

# Add backend to path to import settings
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.settings import settings

async def check_smartflo():
    missing = []
    if not settings.SMARTFLO_API_KEY: missing.append("SMARTFLO_API_KEY")
    if not settings.SMARTFLO_BEARER_TOKEN: missing.append("SMARTFLO_BEARER_TOKEN")
    
    if missing:
        print(f"[-] Tata Smartflo: Missing credentials ({', '.join(missing)})")
        return

    print(f"[+] Tata Smartflo: Configured (Caller ID: {settings.SMARTFLO_CALLER_ID})")

async def check_gemini():
    if not settings.GEMINI_API_KEY:
        print("[-] Gemini: Missing GEMINI_API_KEY")
        return
    model = getattr(settings, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={settings.GEMINI_API_KEY}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                print(f"[+] Gemini: OK (Model: {model})")
            else:
                print(f"[-] Gemini: Failed ({resp.status_code}) - {resp.text.strip()}")
        except Exception as e:
            print(f"[-] Gemini: Error - {e}")

async def main():
    print("==================================================")
    print(" Ultimate Smile Design Agent - Service Key Diagnostic")
    print("==================================================" + "\n")
    
    await asyncio.gather(
        check_smartflo(),
        check_gemini()
    )
    print("\nDiagnostic complete.")

if __name__ == "__main__":
    asyncio.run(main())
