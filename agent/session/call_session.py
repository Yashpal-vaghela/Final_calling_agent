"""
Per-call conversation session state machine and sentence chunker buffer.
"""

import os
import asyncio
import re
from typing import Any, Optional

# Pre-compiled native script regular expressions for microsecond execution
_GUJARATI_SCRIPT_REGEX = re.compile(r"[\u0A80-\u0AFF]")
_DEVANAGARI_SCRIPT_REGEX = re.compile(r"[\u0900-\u097F]")
_LATIN_SCRIPT_REGEX = re.compile(r"[a-zA-Z]")


# High-precision LID Distinguisher Lexicons for Gujarati, Hindi, and English
# High-precision LID Distinguisher Lexicons for Gujarati, Hindi, and English
# IMPORTANT: Words shared between Gujarati and Hindi (or common to dental inquiries) are treated as neutral loanwords.
_DENTAL_LOANWORDS = {
    "appointment", "veneers", "veneer", "clinic", "treatment", "dentist", 
    "crown", "crowns", "teeth", "aligners", "aligner", "smile", "braces",
    "dant", "daant", "दांत", "दांतों", "દાંત",
    "kharch", "kharcha", "kharcho", "खर्च", "खर्चा", "ખર્ચ", "ખર્ચો",
    "ilaaj", "इलाज", "ઇલાજ"
}

_GUJARATI_DEV_TRIGGERS = {
    "केम", "छे", "છે", "छो", "છો", "छुं", "છું", "छीए", "છીએ", "नथी", "નથી",
    "शु", "शुं", "શું", "शाने", "कयारे", "क्यारे", "ક્યારે", "कयां", "क्यां", "ક્યાં",
    "थशे", "थाશે", "થશે", "थाशे", "थाय", "થાય", "थयुं", "थई", "थयो",
    "करशे", "કરશે", "करवु", "કરવું", "करवी", "કરવી", "करवो", "करो", "કરો", "करी", "કરી", "कयुँ",
    "आवशे", "આવશે", "आवुं", "आवो", "જશે", "जशे", "जवु", "जवुं", "मलशे", "मळशे", "મળશે",
    "केटला", "કેટલા", "केटलु", "કેટલું", "केटली", "કેટલી", "केटलो", "કેટલો",
    "केवु", "કેવું", "केवी", "કેવી", "केवो", "केवा", "કેવા",
    "तमने", "તમને", "तमारु", "તમારું", "तमारो", "તમારો", "तमारी", "તમારી", "तमारे", "તમારે", "તમારા", "तमे", "તમે",
    "मने", "મને", "मारु", "મારું", "मारो", "મારો", "मारी", "મારી", "मारा", "हुं", "હું", "अमे", "અમે", "अमने", "अमारु",
    "माटे", "માટે", "सारु", "સારું", "वधारे", "વધારે", "ओछु", "हवे", "હવે", "जोडे", "साथे", "સાથે",
    "बोलने", "બોલોને", "बोलोने", "વાંધો", "वांधो", "ચોક્કસ", "चोक्कस",
    "સમજી", "समजी", "समझी", "સમજાય", "સમજાવો", "समजाय", "समजावु", "સમજાવું", "समजवु",
    "जोइए", "જોઈએ", "जोईए", "आपनुं", "આપનું", "आपो", "આપો", "आपशो", "આપશો",
    "हतो", "हती", "हता", "हतुं", "અને", "अने", "पण", "પણ", "वात", "વાત"
}

_GUJARATI_LATIN_TRIGGERS = {
    "kem", "chhe", "che", "chho", "cho", "chhu", "chun", "chhiye", "chiye", "nathi",
    "shu", "shun", "kyare", "kyaare", "thase", "thashe", "thay", "thaay", "thayu", "thai", "thayo",
    "karse", "karshe", "karvu", "karvi", "karvo", "karo", "kari", "karyu",
    "aavse", "aavshe", "aavvu", "aavo", "jase", "jashe", "javu", "malse", "malshe",
    "ketlu", "ketla", "ketli", "ketlo", "kevu", "kevi", "keva", "kevo",
    "tamne", "tamaru", "tamaro", "tamari", "tamara", "tamare", "tame",
    "mane", "maru", "maro", "mari", "mara", "hun", "hu", "ame", "amne", "amaru",
    "mate", "maate", "saru", "saaru", "vadhare", "ochhu", "hve",
    "sathe", "jode", "bolone", "vandho", "chokkas",
    "samji", "samjay", "samjavo", "samjavu", "joie", "joiye",
    "hato", "hati", "hata", "hatu", "ane", "pan", "vaat", "vat",
    "aapo", "aapsho", "shako", "shakish", "shakay"
}

# Strict Hindi Triggers: ONLY unambiguous, exclusive Hindi words.
# Overlapping words (हा, हाँ, नमस्ते, दांत, खर्च, ठीक, अच्छा, बहुत, ज़्यादा, बात) are removed.
_HINDI_DEV_TRIGGERS = {
    "क्या", "क्यों", "कहाँ", "कब", "कितना", "कितने", "कितनी",
    "कैसा", "कैसी", "कैसे", "बताओ", "बताइए", "बोलिए", "सुनो", "सुनिए",
    "चाहिए", "होगा", "होगी", "होंगे", "था", "थी", "थे", "रहा", "रही", "रहे",
    "सकता", "सकती", "सकते", "मेरा", "मेरी", "मेरे", "तुम्हारा", "तुम्हारी",
    "तुम्हारे", "आपका", "आपकी", "आपके", "इसका", "उसका", "हमारा", "मुझे", "हमें",
    "करना", "करिए", "करदो", "करते", "है", "हैं", "हूँ", "और", "दोनों", "नहीं"
}

_HINDI_LATIN_TRIGGERS = {
    "kya", "kyun", "kyu", "kahan", "kab", "kitna", "kitne", "kitni",
    "kaisa", "kaisi", "kaise", "batao", "bataiye", "boliye", "suno", "suniye",
    "chahiye", "hoga", "hogi", "honge", "tha", "thi", "thhe",
    "raha", "rahi", "rahe", "sakta", "sakti", "sakte", "mera", "meri", "mere",
    "tumhara", "tumhari", "tumhare", "aapka", "aapki", "aapke", "iska", "uska",
    "hamara", "mujhe", "humein", "nahin", "karna", "kariye", "kardo", "karte",
    "hai", "hain", "hoon", "aur", "dono"
}

_ENGLISH_LATIN_TRIGGERS = {
    "what", "how", "cost", "price", "why", "when", "where", "who", "which",
    "can", "could", "would", "should", "please", "tell", "okay", "ok", 
    "yes", "sure", "thanks", "thank", "hello", "good", "fine", "bye",
    "book", "booking", "consultation", "doctor", "process", "much",
    "right", "yeah", "yep", "details", "help", "information", "charges", "quote",
    "explain", "then", "now", "alright", "got", "it", "cool", "understood",
    "want", "need", "know"
}

_AMBIGUOUS_SHORT_TOKENS = {
    "yes", "no", "okay", "ok", "haan", "ha", "haa", "nahi", "na", 
    "please", "hai", "che", "book", "sure", "yeah", "yep"
}


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
        Inspects user STT transcript using stable evidence and precedence rules.
        Returns True if a language switch occurred, else False.
        """
        if not text or not text.strip():
            return False

        clean_text = text.strip()
        lower_text = clean_text.lower()
        all_tokens = re.findall(r'[a-zA-Z\u0900-\u097F\u0A80-\u0AFF]+', lower_text)

        # Early exit for entirely ambiguous or short acknowledgment turns
        if all_tokens and all(t in _AMBIGUOUS_SHORT_TOKENS for t in all_tokens):
            return False

        # 0. Explicit Language Command Detection
        if lower_text == "english" or re.search(r'\b(?:speak english|in english|english please|english mein|english me|english ma|tell me in english)\b', lower_text):
            target_lang = "en"
            if target_lang != self.preferred_language:
                self.set_preferred_language(target_lang)
                return True
            return False
            
        if lower_text == "hindi" or re.search(r'\b(?:speak hindi|in hindi|hindi please|hindi mein|hindi me)\b', lower_text):
            target_lang = "hi"
            if target_lang != self.preferred_language:
                self.set_preferred_language(target_lang)
                return True
            return False
            
        if lower_text == "gujarati" or re.search(r'\b(?:speak gujarati|in gujarati|gujarati please|gujarati ma|gujarati mein|gujarati me)\b', lower_text):
            target_lang = "gu"
            if target_lang != self.preferred_language:
                self.set_preferred_language(target_lang)
                return True
            return False

        # Extract words for cross-script token matching, stripping strictly neutral dental loanwords
        dev_words = set(re.findall(r'[\u0900-\u097F]+', clean_text)) - _DENTAL_LOANWORDS
        latin_words = set(re.findall(r'\b[a-z]+\b', lower_text)) - _DENTAL_LOANWORDS

        # Precedence Rule: Strong Indic grammatical markers outweigh isolated English vocabulary.
        has_gujarati_grammar = bool(dev_words.intersection(_GUJARATI_DEV_TRIGGERS) or latin_words.intersection(_GUJARATI_LATIN_TRIGGERS))
        has_hindi_grammar = bool(dev_words.intersection(_HINDI_DEV_TRIGGERS) or latin_words.intersection(_HINDI_LATIN_TRIGGERS))
        
        # 1. Native Gujarati Unicode Script Range ([\u0A80-\u0AFF]) -> Always 100% Gujarati
        if _GUJARATI_SCRIPT_REGEX.search(clean_text):
            target_lang = "gu"
        # 2. Strong Gujarati Precedence
        elif has_gujarati_grammar:
            target_lang = "gu"
        # 3. Strong Hindi Precedence
        elif has_hindi_grammar:
            target_lang = "hi"
        # 4. English Inquiries & Keywords (Only if no strong Indic markers)
        elif latin_words.intersection(_ENGLISH_LATIN_TRIGGERS):
            target_lang = "en"
        else:
            target_lang = self.preferred_language

        if target_lang and target_lang != self.preferred_language:
            self.set_preferred_language(target_lang)
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
        
        outbound_note = ""
        if self.opening_intent == "outbound_booking_form":
            outbound_note = "\n- Completed Action: The caller ALREADY booked their consultation. NEVER tell them to book or fill out the booking form again."
        elif self.opening_intent == "outbound_smile_preview":
            outbound_note = "\n- Completed Action: The caller ALREADY completed the AI Smile Preview. NEVER tell them to try the preview or fill out that form again."
        elif self.opening_intent == "outbound_contact_form":
            outbound_note = "\n- Completed Action: The caller ALREADY submitted their enquiry via contact form. NEVER tell them to fill out the contact form again."

        return (
            f"[PYTHON SESSION MEMORY & STATE]\n"
            f"- Preferred Language: {lang_label} ({self.preferred_language}). (NOTE: Always prioritize ZERO LAG SWITCHING: if the user speaks a different language, match their language instantly.)\n"
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


