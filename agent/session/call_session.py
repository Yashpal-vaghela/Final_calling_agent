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
        self.booking_stage: str = "booking_confirmed" if opening_intent == "outbound_booking_form" else "greeting"  # e.g., greeting, discovery, consultation_proposed, lead_captured, handoff, booking_confirmed
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
        Inspects user utterance in Python for language selection, script detection,
        or dynamic code-switch triggers (including short 1-3 word phrases).
        Updates preferred language immediately on the fly.
        """
        if not text:
            return False
        text_lower = text.lower().strip()
        
        # 1. Script-based Unicode detection
        if re.search(r"[\u0A80-\u0AFF]", text):  # Gujarati script
            self.set_preferred_language("gu")
            return True
        if re.search(r"[\u0900-\u097F]", text):  # Devanagari (Hindi) script
            self.set_preferred_language("hi")
            return True

        # 2. Conversational Gujarati triggers (including 1-3 word micro-utterances)
        gu_tokens = [
            "gujarati", "gujlish", "gujarati ma", "kem cho", "su chhe", "shu chhe",
            "ketla", "thashe", "nathi", "tamare", "tame", "karo", "bolo ne", "saru",
            "kaho", "barabar", "aavse", "chhe", "maare", "aapo ne", "saheb", "tamari"
        ]
        if any(token in text_lower for token in gu_tokens):
            self.set_preferred_language("gu")
            return True

        # 3. Conversational Hindi triggers (including 1-3 word micro-utterances)
        hi_tokens = [
            "hindi", "hinglish", "hindi mein", "hindi me", "hindi mein baat",
            "kaise ho", "namaste", "kaise", "kya", "kitna", "batao", "bataiye",
            "suno", "haan", "haanji", "theek", "acha", "boliye", "kariye",
            "hoga", "chahiye", "kya hai", "sahi hai", "mujhe", "aapko"
        ]
        if any(token in text_lower for token in hi_tokens):
            self.set_preferred_language("hi")
            return True

        # 4. Explicit English selection triggers
        en_tokens = ["english", "in english", "continue in english", "speak in english"]
        if any(token in text_lower for token in en_tokens):
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
            f"- Preferred Language: {lang_label} ({self.preferred_language}). (NOTE: Always prioritize ZERO LAG SWITCHING: if the user speaks a different language, match their language instantly.)\n"
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
        """Appends or merges streaming utterances into clean conversation turns."""
        clean_text = text.strip()
        if not clean_text:
            return

        # If previous utterance was from the same role, merge them seamlessly
        if self.conversation_history and self.conversation_history[-1].get("role") == role:
            prev = self.conversation_history[-1]
            prev_content = prev.get("content") or prev.get("text") or ""
            if prev_content and not prev_content.endswith(" "):
                merged_content = f"{prev_content} {clean_text}"
            else:
                merged_content = f"{prev_content}{clean_text}"
            prev["content"] = merged_content
            prev["text"] = merged_content
        else:
            self.conversation_history.append({
                "role": role,
                "content": clean_text,
                "text": clean_text
            })


