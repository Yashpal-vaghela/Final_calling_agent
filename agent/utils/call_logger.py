import json
import os

CALLS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "calls.json")

def load_calls():
    if not os.path.exists(CALLS_FILE):
        return {}
    with open(CALLS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_calls(calls: dict):
    # Ephemeral mode: disk storage disabled
    pass

def log_call_end(call_id: str, transcript: list, duration_seconds: float, status: str):
    # Ephemeral mode: disk storage disabled
    pass

def load_usage():
    return []

def log_usage(call_id: str, stt_seconds: float, tts_chars: int, llm_tokens: int):
    # Ephemeral mode: disk storage disabled
    pass
