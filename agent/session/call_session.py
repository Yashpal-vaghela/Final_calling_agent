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
    Owns conversational memory, user information, topics, and session booking state.
    Language determination is handled natively by Gemini Multimodal Live API.
    """
    def __init__(self, call_id: str, opening_intent: str | None = None, lead_id: str | None = None, preferred_language: str = "en"):
        self.call_id = call_id
        self.opening_intent = opening_intent
        self.lead_id = lead_id
        
        # State machine: "listening", "thinking", "speaking"
        self.state: str = "listening"
        
        # Deprecated compatibility field (Gemini Live now natively handles conversational language)
        self.preferred_language: str = preferred_language
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
        """Deprecated compatibility method: mutates preferred_language attribute."""
        clean_lang = lang.strip().lower()
        mapping = {"english": "en", "hindi": "hi", "gujarati": "gu", "en": "en", "hi": "hi", "gu": "gu", "multi": "multi"}
        if clean_lang in mapping:
            self.preferred_language = mapping[clean_lang]

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
        
        outbound_note = ""
        if self.opening_intent == "outbound_booking_form":
            outbound_note = "\n- Completed Action: The caller ALREADY booked their consultation. NEVER tell them to book or fill out the booking form again."
        elif self.opening_intent == "outbound_smile_preview":
            outbound_note = "\n- Completed Action: The caller ALREADY completed the AI Smile Preview. NEVER tell them to try the preview or fill out that form again."
        elif self.opening_intent == "outbound_contact_form":
            outbound_note = "\n- Completed Action: The caller ALREADY submitted their enquiry via contact form. NEVER tell them to fill out the contact form again."

        return (
            f"[PYTHON SESSION MEMORY & STATE]\n"
            f"- Active Topic in Discussion: {self.last_discussed_topic or 'General Inquiry'}\n"
            f"- Current Booking Stage: {self.booking_stage}\n"
            f"- Collected User Info: {info_str}\n"
            f"- Turn History Count: {len(self.conversation_history)}"
            f"{outbound_note}"
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


