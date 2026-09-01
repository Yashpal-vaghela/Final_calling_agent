from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import os

from backend.app.routes import smartflo_voice, contact_form, internal_callback, booking_form, cities, smile_preview
from backend.app.services.caller_context import ACTIVE_CALLER_CONTEXTS

app = FastAPI(title="USD Calling Agent")

# Mount static directory
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(smartflo_voice.router)
app.include_router(contact_form.router)
app.include_router(internal_callback.router)
app.include_router(booking_form.router)
app.include_router(cities.router)
app.include_router(smile_preview.router)

@app.get("/")
def root():
    return RedirectResponse(url="/contact-form")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/api/debug/environment")
def debug_environment():
    import os
    import glob
    env = "CLOUD_RUN" if os.environ.get("K_SERVICE") or os.environ.get("PORT") else "LOCAL"
    cwd = os.getcwd()
    sys_prompt_path = os.path.join(cwd, "agent", "prompts", "system_prompt.md")
    kb_path = os.path.join(cwd, "data", "knowledge")
    
    kb_files = []
    if os.path.exists(kb_path):
        kb_files = glob.glob(os.path.join(kb_path, "*.json"))
        kb_files = [os.path.basename(f) for f in kb_files]
        
    return {
        "environment": env,
        "current_working_directory": cwd,
        "system_prompt_path_exists": os.path.exists(sys_prompt_path),
        "knowledge_base_path_exists": os.path.exists(kb_path),
        "knowledge_files_found": kb_files,
        "active_contexts_count": len(ACTIVE_CALLER_CONTEXTS)
    }
