"""
Per-call conversation session state machine and sentence chunker buffer.
"""

import os
import asyncio
import re
from typing import Any, Optional

class CallSession:
    """
    Manages state transitions (listening, thinking, speaking), conversation history,
    and transcript buffers for real-time Gemini Live interactions.
    Also owns Layer 3 conversational memory, mutable preferred language, and session state.
    """
    def __init__(self, call_id: str, opening_intent: str | None = None, lead_id: str | None = None, preferred_language: str = "en"):
        self.call_id = call_id
        self.opening_intent = opening_intent
        self.lead_id = lead_id
        
        # State machine: "listening", "thinking", "speaking"
        self.state: str = "listening"
        
        # Layer 3: Python-managed conversation memory & mutable session state
        self.preferred_language: str = preferred_language  # mutable preferred language (en, hi, gu, multi)
        self.last_discussed_topic: Optional[str] = None
        self.booking_stage: str = "greeting"  # e.g., greeting, discovery, consultation_proposed, lead_captured, handoff
        self.collected_user_info: dict[str, str] = {
            "name": "",
            "phone": "",
            "email": "",
            "city": "",
            "subject": "",
            "intent": "",
            "notes": ""
        }
        

        # Chronological record of confirmed conversational turns for logging
        self.conversation_history: list[dict[str, Any]] = []
        
        # Turn tracking for future barge-in cancellation guards
        self.current_turn_id: int = 0

    def set_preferred_language(self, lang: str) -> None:
        """Mutates preferred language immediately without restarting session."""
        clean_lang = lang.strip().lower()
        mapping = {"english": "en", "hindi": "hi", "gujarati": "gu", "en": "en", "hi": "hi", "gu": "gu", "multi": "multi"}
        if clean_lang in mapping:
            new_lang = mapping[clean_lang]
            if self.preferred_language != new_lang:
                print(f"[CallSession {self.call_id[:8]}] Preferred Language Updated: {self.preferred_language} -> {new_lang}")
                self.preferred_language = new_lang

    def update_language_if_requested(self, text: str) -> bool:
        """
        Inspects user utterance in Python for language selection or switch requests.
        Updates preferred language immediately on the fly.
        """
        text_lower = text.lower().strip()
        if any(token in text_lower for token in ["gujarati", "gujlish", "gujarati ma", "kem cho"]):
            self.set_preferred_language("gu")
            return True
        elif any(token in text_lower for token in ["hindi", "hinglish", "hindi mein", "hindi me", "hindi mein baat", "kaise ho", "namaste"]):
            self.set_preferred_language("hi")
            return True
        elif any(token in text_lower for token in ["english", "in english", "continue in english", "speak in english"]):
            self.set_preferred_language("en")
            return True
        return False

    def update_topic(self, topic: str) -> None:
        """Updates the active conversational topic in Python memory."""
        if topic and topic != self.last_discussed_topic:
            print(f"[CallSession {self.call_id[:8]}] Active Topic: {self.last_discussed_topic} -> {topic}")
            self.last_discussed_topic = topic
            if self.booking_stage == "greeting":
                self.booking_stage = "discovery"

    def update_user_info(self, name: Optional[str] = None, phone: Optional[str] = None, email: Optional[str] = None, city: Optional[str] = None, subject: Optional[str] = None, intent: Optional[str] = None, notes: Optional[str] = None) -> None:
        """Updates collected user entities across conversational turns."""
        if name: self.collected_user_info["name"] = name
        if phone: self.collected_user_info["phone"] = phone
        if email: self.collected_user_info["email"] = email
        if city: self.collected_user_info["city"] = city
        if subject: self.collected_user_info["subject"] = subject
        if intent: self.collected_user_info["intent"] = intent
        if notes: self.collected_user_info["notes"] = notes
        
        if self.collected_user_info["name"] and self.collected_user_info["phone"]:
            self.booking_stage = "lead_captured"

    def get_session_context_prompt(self) -> str:
        """
        Returns a formatted memory context block representing Python-managed session state.
        Gemini consumes this state dynamically without owning it.
        """
        info_str = ", ".join([f"{k}: {v}" for k, v in self.collected_user_info.items() if v]) or "None"
        lang_label = {"en": "English", "hi": "Hindi", "gu": "Gujarati", "multi": "Multilingual/Code-mixed"}.get(self.preferred_language, "English")
        return (
            f"[PYTHON SESSION MEMORY & STATE]\n"
            f"- Preferred Language: {lang_label} ({self.preferred_language}) - respond in this language immediately.\n"
            f"- Active Topic in Discussion: {self.last_discussed_topic or 'General Inquiry'}\n"
            f"- Current Booking Stage: {self.booking_stage}\n"
            f"- Collected User Info: {info_str}\n"
            f"- Turn History Count: {len(self.conversation_history)}"
        )

    def transition_state(self, new_state: str) -> None:
        """Transitions the call state machine and logs the change."""
        if self.state != new_state:
            print(f"[CallSession {self.call_id[:8]}] State: {self.state} -> {new_state}")
            self.state = new_state

    def add_transcript(self, text: str, role: str = "user") -> None:
        """Appends a completed utterance to the permanent conversation history."""
        self.conversation_history.append({
            "role": role,
            "content": text.strip()
        })

