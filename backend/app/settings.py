from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_LIVE_MODEL: str = "gemini-3.1-flash-live-preview"
    GEMINI_LIVE_VOICE: str = "Aoede"

    INTERNAL_KEY: str = "default_internal_secret_key"
    ALLOWED_NUMBERS: str = ""

    # Tata Smartflo Configuration
    SMARTFLO_API_KEY: str = ""
    SMARTFLO_BEARER_TOKEN: str = ""
    SMARTFLO_BASE_URL: str = "https://api-smartflo.tatateleservices.com"
    SMARTFLO_CALLER_ID: str = ""
    SMARTFLO_AGENT_NUMBER: str = ""

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()