from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from backend.app.routes import smartflo_voice, contact_form, internal_callback

app = FastAPI(title="USD Calling Agent")

app.include_router(smartflo_voice.router)
app.include_router(contact_form.router)
app.include_router(internal_callback.router)

@app.get("/")
def root():
    return RedirectResponse(url="/contact-form")

@app.get("/health")
def health_check():
    return {"status": "ok"}
