"""
Production-ready asynchronous client for Google Gemini Multimodal Live API using google-genai SDK.
Supports real-time bidirectional audio streaming, interruption handling, and inline tool execution.
Independent component designed for voice pipeline integration without video or demo frontend code.
"""

import os
import json
import asyncio
import inspect
import traceback
import logging
from typing import AsyncGenerator, Any, Optional, Callable, Dict, List
from datetime import datetime, timezone

from google import genai
from google.genai import types as genai_types
from backend.app.settings import settings

logger = logging.getLogger(__name__)

MANDATORY_TRANSCRIPTION_INSTRUCTION = (
    "When generating text transcripts of the user's speech, always transcribe Hindi speech into native Devanagari script. "
    "Always transcribe Gujarati speech into native Gujarati script. Never use English letters to spell out Hindi or Gujarati words. "
    "Always reply in the exact language the user is speaking."
)

from agent.tools.check_city_coverage import check_city_coverage
from agent.tools.capture_lead import capture_lead
from agent.tools.get_faq import get_faq
from agent.tools.handoff import human_handoff
from agent.tools.book_consultation import book_consultation
from agent.tools.cancel_consultation import cancel_consultation

def build_system_prompt(opening_intent: str) -> str:
    """Dynamically assembles the system prompt from the modular prompts directory."""
    prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
    
    # 1. Load Core Persona & Guardrails
    persona_path = os.path.join(prompts_dir, "core", "persona.md")
    guardrails_path = os.path.join(prompts_dir, "core", "guardrails.md")
    
    components = []
    for path in [persona_path, guardrails_path]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                components.append(f.read())
                
    # 2. Load Intent-Specific Prompt
    intent_file_map = {
        "outbound_booking_form": "outbound_booking.md",
        "outbound_contact_form": "outbound_contact.md",
        "outbound_smile_preview": "outbound_smile_preview.md",
        "inbound": "inbound.md"
    }
    
    intent_filename = intent_file_map.get(opening_intent, "inbound.md")
    intent_path = os.path.join(prompts_dir, "intents", intent_filename)
    
    if os.path.exists(intent_path):
        with open(intent_path, "r", encoding="utf-8") as f:
            components.append(f.read())
            
    if not components:
        return "You are Kiara, an elite concierge for Ultimate Smile Design."
        
    return "\n\n---\n\n".join(components)


class GeminiLiveStreamClient:
    """
    An asynchronous streaming client for Google Gemini Multimodal Live API using the official google-genai SDK.
    Handles real-time audio PCM streams, server-side activity endpointing, interruption events, and native tool execution.
    """
    def __init__(
        self,
        call_id: Optional[str] = None,
        preferred_language: str = "multi",
        initial_greeting: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        input_sample_rate: int = 16000,
        model_name: Optional[str] = None,
        voice_name: Optional[str] = None,
        tools: Optional[list] = None,
        tool_mapping: Optional[Dict[str, Callable]] = None,
        system_instruction: Optional[str] = None,
        caller_context: Optional[Dict[str, Any]] = None,
        opening_intent: Optional[str] = None,
        session: Optional[Any] = None,
    ):
        self.call_id = call_id or "live-session"
        self.session = session
        self.preferred_language = preferred_language
        self.input_sample_rate = input_sample_rate
        self.caller_context = caller_context or {}
        self.opening_intent = opening_intent or self.caller_context.get("opening_intent")
        self.initial_greeting = initial_greeting
        self.initial_prompt = initial_prompt
        
        # Load credentials and configuration from settings with fallback to environment
        self.api_key = getattr(settings, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set in settings or environment.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = (
            model_name 
            or getattr(settings, "GEMINI_LIVE_MODEL", None) 
            or os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
        )
        self.voice_name = (
            voice_name 
            or getattr(settings, "GEMINI_LIVE_VOICE", None) 
            or os.environ.get("GEMINI_LIVE_VOICE", "Erinome")
        )

        # Build system instruction incorporating conversational context
        intent_directive = ""
        if self.opening_intent == "outbound_booking_form":
            intent_directive = (
                "### STRICT FORM RESTRICTION - OUTBOUND BOOKING (ABSOLUTE PRIORITY ON EVERY TURN):\n"
                "- The caller ALREADY filled out and submitted the Appointment Booking Form on the website.\n"
                "- NEVER, under any circumstances, ask or suggest the caller fill out the booking form, contact form, or search for a dentist on the website.\n"
                "- NEVER tell the caller to visit ultimatesmiledesign.com to book an appointment.\n"
                "- If the caller asks about next steps, appointment schedule, or meeting the doctor: Confirm that their appointment request is ALREADY registered with our authorized designer in their city, and our clinical coordinator will contact them directly with their appointment slot.\n\n"
            )
        elif self.opening_intent == "outbound_smile_preview":
            intent_directive = (
                "### STRICT FORM RESTRICTION - OUTBOUND SMILE PREVIEW (ABSOLUTE PRIORITY ON EVERY TURN):\n"
                "- The caller ALREADY completed the Virtual AI Smile Preview and submitted their details.\n"
                "- NEVER ask or tell the caller to try the AI Smile Preview, upload a photo, or fill out the preview form again.\n\n"
            )
        elif self.opening_intent == "outbound_contact_form":
            intent_directive = (
                "### STRICT FORM RESTRICTION - OUTBOUND CONTACT FORM (ABSOLUTE PRIORITY ON EVERY TURN):\n"
                "- The caller ALREADY submitted their enquiry via the website contact form.\n"
                "- NEVER tell the caller to fill out a contact form, enquiry form, or send a message on the website again.\n\n"
            )

        if system_instruction is not None:
            self.system_instruction = f"{MANDATORY_TRANSCRIPTION_INSTRUCTION}\n\n{intent_directive}{system_instruction}"
        else:
            base_prompt = build_system_prompt(self.opening_intent or "inbound")
            self.system_instruction = (
                f"{MANDATORY_TRANSCRIPTION_INSTRUCTION}\n\n"
                f"{intent_directive}"
                "### ACTIVE LANGUAGE — HIGHEST PRIORITY (SPEECH-TO-SPEECH AUDIO RULES)\n\n"

                "RULE 1 — AUDIO LOYALTY (MOST IMPORTANT):\n"
                "This is a real-time bidirectional voice call. You receive the caller's raw audio directly. "
                "You MUST determine language from the caller's spoken voice acoustics, phonetics, rhythm, "
                "and colloquial expressions — NOT from the transcribed text alone. "
                "If the caller's audio sounds Gujarati, respond in Gujarati. "
                "If the caller's audio sounds Hindi, respond in Hindi. "
                "If the caller's audio sounds English, respond in English. "
                "Trust what you HEAR, not what the transcript text shows.\n\n"

                "RULE 2 — GUJARATI vs HINDI STT CONFUSION (CRITICAL ANTI-DRIFT):\n"
                "Speech-to-text systems frequently misrecognize Gujarati speech and produce output in "
                "Devanagari script (Hindi script) or English-looking words, because Gujarati and Hindi "
                "share similar phonetics. For example, the Gujarati word 'નહિ' (nahi) may appear in the "
                "transcript as 'नहीं' (Hindi), or Gujarati speech may be transcribed as random English words. "
                "ABSOLUTE RULE: If the audio phonetics, cadence, and regional accent sound Gujarati, "
                "YOU MUST RESPOND IN GUJARATI — even if the transcript text looks like Hindi Devanagari "
                "or garbled English. NEVER drift from Gujarati to Hindi based on a transcript alone. "
                "Key Gujarati audio markers to recognize: words ending in -che, -chho, -chhu, -nathi, "
                "expressions like 'kevu che', 'shu joie', 'thase', 'chhe', 'tamne', 'amne'.\n\n"

                "RULE 3 — LANGUAGE SWITCH ONLY ON FULL SENTENCE:\n"
                "ONLY switch language when the caller speaks a COMPLETE, FULL sentence in a genuinely "
                "different language. NEVER switch on:\n"
                "- Single words or very short phrases (ha, haan, okay, yes, no, theek hai, saru)\n"
                "- English dental loanwords (appointment, veneers, smile design, clinic, consultation, "
                "  dentist, cost, doctor, treatment, crown) — these appear in Gujarati and Hindi speech naturally\n"
                "- Brief interruptions or audio fragments\n"
                "- The caller's city name (a caller in Surat may speak Hindi or English)\n"
                "- Tool result content (tools always return English internally — ignore for language detection)\n\n"

                "RULE 4 — ESTABLISHED LANGUAGE LOYALTY:\n"
                "Once you have identified the caller's language from their speech pattern across 2+ turns, "
                "maintain that language firmly. Do not flip back and forth. "
                "Information returned by tools may be in English internally — always translate and deliver "
                "in the caller's current spoken language.\n\n"
                "### FEMALE IDENTITY & GRAMMAR (STRICT)\n"
                "You are Kiara, a female consultant. NEVER use masculine verbs for yourself:\n"
                "- Hindi: Always use feminine endings ('बता सकती हूँ', 'देख रही हूँ', 'करूँगी', 'आपकी कंसल्टेंट'). Never say 'बता सकता हूँ'.\n"
                "- Gujarati: Always use feminine endings (-ઈ): say 'હું તમારી કન્સલ્ટન્ટ છું' (tamari, never tamaro), 'હું સમજી ગઈ' / 'મને સમજાયું' (NEVER 'સમજી ગયો' or 'ગયો'), and 'જોઈ/કરી રહી છું' (NEVER 'રહ્યો છું').\n\n"
                "### MANDATORY KNOWLEDGE BASE RETRIEVAL\n"
                "Call the `get_faq` tool to answer questions about prices, procedures, or doctors. Never guess without calling the tool.\n\n"
                "### STRICT DENTIST PRIVACY & VERIFICATION RULES (ZERO TOLERANCE):\n"
                "- NEVER VOLUNTEER OR OFFER DENTIST NAMES: You are strictly forbidden from offering to tell, suggesting, or listing doctor names. NEVER say 'Should I tell you another doctor's name?' or 'કે પછી કોઈ બીજા ડોક્ટરનું નામ જણાવું?' or 'क्या मैं किसी दूसरे डॉक्टर का नाम बताऊँ?'. If the user asks who our doctors are or asks for doctor names, say: 'I cannot provide dentist names over the phone. You can explore all our authorized smile designers on ultimatesmiledesign.com.'\n"
                "- MANDATORY CHECK ON CALLER-PROVIDED DENTIST NAME: If the caller mentions, asks about, or gives a doctor's name (e.g. 'Is Rajesh Patel your dentist?', 'Dr. Hetal Buch che Surat ma?'): You MUST call the `check_dentist` tool immediately with their name and city! NEVER answer 'Yes he is in [City]' or confirm a dentist without the tool result! If the tool returns is_authorized=False, you MUST tell the caller clearly that Dr. [Name] is NOT an authorized Ultimate Smile Design specialist in [City], and offer to proceed without specifying a doctor.\n\n"
                "### MANDATORY TOOL EXECUTION FOR BOOKING (ZERO SPEECH-ONLY HALLUCINATIONS):\n"
                "- Whenever you ask the caller: 'Shall I submit your consultation request?' (or 'શું તમે ચોક્કસ ડૉક્ટર વગર રિક્વેસ્ટ સબમિટ કરવા માંગો છો?' / 'Shall I proceed without a doctor?') AND the caller replies with 'હા', 'हां', 'yes', 'sure', 'go ahead', 'બુક કરો', 'કરો', 'appointment book karo':\n"
                "  YOU MUST EMIT THE `book_consultation` TOOL CALL ON THAT VERY TURN!\n"
                "- YOU CANNOT SUBMIT A BOOKING BY SPEAKING. If you do not execute the `book_consultation` tool, the booking DOES NOT EXIST in the admin panel!\n"
                "- If `check_dentist` returned is_authorized=True (e.g. Dr. Viren K Savani in Surat), call `book_consultation(doctor_name='Dr. Viren K Savani', city='Surat')`.\n"
                "- If the caller agrees to proceed without a doctor, call `book_consultation(doctor_name='', city='Surat')`.\n"
                "- NEVER say 'તમારી વિગતો સબમિટ કરી દીધી છે' / 'I have submitted your booking' / 'એપોઇન્ટમેન્ટ બુક થઈ ગઈ છે' UNLESS `book_consultation` was actually called and returned status: 'success'!\n"
                "- If the caller says they cannot see it in the admin panel ('admin panel par nathi dikhati') or says 'appointment book karo': If book_consultation was not executed yet, call `book_consultation` immediately!\n\n"
                f"{base_prompt}\n\n"
                "---\n\n"
                "## CURRENT SESSION (LIVE VOICE CALL)\n"
                "DELIVERY: Speak warmly and concisely with a natural Indian cadence. Enunciate crisply. Answer standard questions directly in 2 to 3 elegant sentences without filler or repeating the caller's question. For comparisons and objections, use 3 to 4 sentences to clearly explain the distinction and include the luxury analogy. Never give abrupt 1-sentence answers, and never ask about budget or price range.\n"
            )

        # Input queues and state flags
        if tools is not None:
            self.tools = tools
        else:
            self.tools = [self._build_tool_declarations()]
            
        if tool_mapping is not None:
            self.tool_mapping = tool_mapping
        else:
            self.tool_mapping = self._build_default_tool_mapping()

        # Input queues and state flags
        self._audio_input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._text_input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._session_task: Optional[asyncio.Task] = None
        self._closed: bool = False
        self._is_connected: bool = False

    def _build_tool_declarations(self) -> genai_types.Tool:
        """Assembles native tool schemas matching project enterprise tools."""
        capture_lead_tool = genai_types.FunctionDeclaration(
            name="capture_lead",
            description="DO NOT USE THIS FOR BOOKING APPOINTMENTS. ONLY use this if a caller spontaneously insists on a callback. NEVER ask the caller for their name or phone number proactively.",
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "name": genai_types.Schema(type=genai_types.Type.STRING, description="Caller's full name"),
                    "phone": genai_types.Schema(type=genai_types.Type.STRING, description="Caller's phone number"),
                    "city": genai_types.Schema(type=genai_types.Type.STRING, description="City where caller wants dental services"),
                    "intent": genai_types.Schema(type=genai_types.Type.STRING, description="Intent: consultation, find_dentist, warranty_verification, faq, other"),
                    "notes": genai_types.Schema(type=genai_types.Type.STRING, description="Any extra conversation notes"),
                    "preferred_language": genai_types.Schema(type=genai_types.Type.STRING, description="Caller language: en, hi, gu"),
                },
                required=["name", "phone", "city", "intent"],
            ),
        )

        check_city_tool = genai_types.FunctionDeclaration(
            name="check_city_coverage",
            description="Check if Ultimate Smile Design services are available in a specific Indian city. ONLY call this if the user explicitly asks about a city or asks if you are available in their location.",
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "city": genai_types.Schema(type=genai_types.Type.STRING, description="City name to check coverage for"),
                },
                required=["city"],
            ),
        )

        get_faq_tool = genai_types.FunctionDeclaration(
            name="get_faq",
            description="Retrieve authoritative FAQ answers about Ultimate Smile Design prices, procedures, doctors, course_price, dentist_partner_benefits, process, timeline, cities, cost, before_after, or warranty.",
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "topic": genai_types.Schema(type=genai_types.Type.STRING, description="FAQ topic or question keyword: prices, procedures, doctors, course_price, dentist_partner_benefits, process, timeline, cities, cost, before_after, warranty"),
                },
                required=["topic"],
            ),
        )

        handoff_tool = genai_types.FunctionDeclaration(
            name="human_handoff",
            description="DO NOT USE THIS FOR BOOKING APPOINTMENTS. Do not proactively offer to share details with the team.",
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "reason": genai_types.Schema(type=genai_types.Type.STRING, description="Reason for escalation"),
                    "phone_number": genai_types.Schema(type=genai_types.Type.STRING, description="Caller phone number"),
                },
                required=["reason"],
            ),
        )

        book_consultation_tool = genai_types.FunctionDeclaration(
            name="book_consultation",
            description=(
                "Book an in-call consultation with an authorized dentist in the specified city. "
                "MANDATORY EXECUTION: Whenever the caller confirms or agrees to submit/book an appointment "
                "(e.g., caller says 'Yes', 'Go ahead', 'Haan', 'હા', 'हां', 'બુક કરો', 'હા કરો', 'કન્ફર્મ કરો', 'appointment book karo', or agrees to proceed without a doctor): "
                "YOU MUST EMIT THIS TOOL CALL IMMEDIATELY ON THAT EXACT TURN. "
                "NEVER say 'I have submitted your request' or 'મેં તમારી વિગતો સબમિટ કરી દીધી છે' in voice without calling this tool! "
                "Speech alone DOES NOT submit the booking to the backend database. "
                "If the caller agreed to proceed without specifying a doctor, call this tool with doctor_name=''. "
                "If the caller confirmed with an authorized doctor, pass doctor_name."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "doctor_name": genai_types.Schema(type=genai_types.Type.STRING, description="Optional. The name of the specific dentist the caller wants to see, or empty string '' if no specific doctor requested."),
                    "city": genai_types.Schema(type=genai_types.Type.STRING, description="Optional. The consultation city requested by the caller (e.g. Surat, Ahmedabad)."),
                    "message": genai_types.Schema(type=genai_types.Type.STRING, description="Optional. Any additional notes or message from the caller."),
                    "first_name": genai_types.Schema(type=genai_types.Type.STRING, description="Not required. Filled by system."),
                    "last_name": genai_types.Schema(type=genai_types.Type.STRING, description="Not required. Filled by system."),
                    "phone": genai_types.Schema(type=genai_types.Type.STRING, description="Not required. Filled by system."),
                    "email": genai_types.Schema(type=genai_types.Type.STRING, description="Not required. Filled by system."),
                },
                required=[],
            ),
        )

        check_dentist_tool = genai_types.FunctionDeclaration(
            name="check_dentist",
            description=(
                "Check if a specific doctor or dentist named by the caller is an authorized Ultimate Smile Design partner in a specific city. "
                "You MUST call this whenever a caller asks if a specific doctor is available, asks if Dr. [Name] is our smile designer, or mentions a doctor name before booking. "
                "CRITICAL: Do NOT use this tool to list dentists. NEVER volunteer or give dentist names to the caller."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "doctor_name": genai_types.Schema(type=genai_types.Type.STRING, description="The name of the doctor mentioned by the caller (e.g. 'Rajesh Patel', 'Purvi Patel', 'Dr. Hetal Buch')"),
                    "city": genai_types.Schema(type=genai_types.Type.STRING, description="The city to check for this doctor (e.g. 'Surat', 'Ahmedabad', 'Rajkot')"),
                },
                required=["doctor_name", "city"],
            ),
        )

        cancel_consultation_tool = genai_types.FunctionDeclaration(
            name="cancel_consultation",
            description=(
                "Cancel an existing in-call consultation appointment via the backend API. "
                "MANDATORY: You MUST only call this tool AFTER the user explicitly insists on canceling, "
                "AND only AFTER you have asked them why they want to cancel and attempted to help them. "
                "If the user insists on canceling, you MUST call this tool. Do NOT say 'I have canceled your appointment' "
                "without calling this tool."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "reason": genai_types.Schema(type=genai_types.Type.STRING, description="The reason the caller gave for canceling the appointment."),
                },
                required=[],
            ),
        )

        return genai_types.Tool(
            function_declarations=[check_city_tool, get_faq_tool, book_consultation_tool, check_dentist_tool, cancel_consultation_tool]
        )

    def _build_default_tool_mapping(self) -> Dict[str, Callable]:
        """Maps schema names to callable execution wrappers supplying session parameters."""
        from agent.tools.check_dentist import check_dentist

        def wrap_capture_lead(**kwargs):
            kwargs.setdefault("call_id", self.call_id)
            kwargs.setdefault("preferred_language", self.preferred_language)
            return capture_lead(**kwargs)

        def wrap_get_faq(**kwargs):
            kwargs.setdefault("language", self.preferred_language)
            return get_faq(**kwargs)

        def wrap_handoff(**kwargs):
            kwargs.setdefault("call_id", self.call_id)
            return human_handoff(**kwargs)

        def wrap_set_caller_language(**kwargs):
            # This is a dummy wrapper, pipeline.py will override this with real session state logic
            return {"status": "success", "language": kwargs.get("language")}

        return {
            "capture_lead": wrap_capture_lead,
            "check_city_coverage": check_city_coverage,
            "get_faq": wrap_get_faq,
            "human_handoff": wrap_handoff,
            "set_caller_language": wrap_set_caller_language,
            "book_consultation": book_consultation,
            "cancel_consultation": cancel_consultation,
            "check_dentist": check_dentist,
        }

    async def send_audio(self, audio_chunk: bytes) -> None:
        """Enqueues linear 16-bit PCM (16kHz) audio bytes for transmission to Gemini Live."""
        if self._closed:
            return
        await self._audio_input_queue.put(audio_chunk)

    async def send_text(self, text: str) -> None:
        """Enqueues dynamic text instructions, RAG facts, or conversational updates into the live stream."""
        if self._closed or not text.strip():
            return
        await self._text_input_queue.put(text)

    async def start_session(
        self,
        audio_output_callback: Optional[Callable[[bytes], Any]] = None,
        audio_interrupt_callback: Optional[Callable[[], Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Opens the Gemini Live bidirectional session context and runs sender/receiver loops.
        Yields normalized event dictionaries for transcription, turns, tool calls, and errors.
        """
        # Guarantee WebSocket setup payload includes mandatory transcription and language mirroring instruction
        setup_instruction = self.system_instruction
        if MANDATORY_TRANSCRIPTION_INSTRUCTION not in setup_instruction:
            setup_instruction = f"{MANDATORY_TRANSCRIPTION_INSTRUCTION}\n\n{setup_instruction}"

        config = genai_types.LiveConnectConfig(
            response_modalities=[genai_types.Modality.AUDIO],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=self.voice_name
                    )
                )
            ),
            system_instruction=genai_types.Content(
                parts=[genai_types.Part(text=setup_instruction)]
            ),
            input_audio_transcription=genai_types.AudioTranscriptionConfig(
                language_auto=genai_types.LanguageAuto(),
                custom_vocabulary=[
                    "Ultimate Smile Design",
                    "Advance Dental Export",
                    "Kiara",
                    "Haresh Savani",
                    "smile design",
                    "veneers",
                    "appointment",
                    "consultation",
                    "authorized smile designer",
                ],
            ),
            output_audio_transcription=genai_types.AudioTranscriptionConfig(),
            realtime_input_config=genai_types.RealtimeInputConfig(
                turn_coverage="TURN_INCLUDES_ONLY_ACTIVITY",
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    disabled=False,
                    prefix_padding_ms=500,
                    silence_duration_ms=650,
                ),
            ),
            tools=self.tools if self.tools else None,
        )

        print(f"[Gemini Live Stream] Connecting to Live API with model={self.model_name}, voice={self.voice_name}")
        self._is_connected = True

        try:
            async with self.client.aio.live.connect(model=self.model_name, config=config) as session:
                print("[Gemini Live Stream] Session established successfully.")

                # Solution C: Language-neutral anchor turn injected FIRST so Gemini
                # enters the session in a listen-first state rather than pre-guessing
                # the caller's language from any context clue.
                turns = [
                    genai_types.Content(
                        parts=[genai_types.Part.from_text(
                            text=(
                                "[SESSION ANCHOR] You are starting a live phone call. "
                                "Do NOT speak yet. Do NOT assume any language. "
                                "Listen to the caller's first spoken words and immediately match "
                                "their exact language: Gujarati if they speak Gujarati, "
                                "Hindi if they speak Hindi, English if they speak English. "
                                "The caller's city does NOT determine their language."
                            )
                        )],
                        role="user",
                    ),
                    genai_types.Content(
                        parts=[genai_types.Part.from_text(
                            text="Understood. I will listen to the caller first and match their spoken language exactly."
                        )],
                        role="model",
                    ),
                ]

                # Solution B: City is intentionally OMITTED from this context message.
                # The city is still available server-side in self.caller_context for tool
                # calls (book_consultation, check_city_coverage, check_dentist), but it
                # is NOT sent to Gemini here so it cannot bias Gemini's language choice.
                if self.caller_context:
                    name = self.caller_context.get("name", "")
                    phone = self.caller_context.get("phone", "")
                    subject = self.caller_context.get("subject", "")
                    message = self.caller_context.get("message") or self.caller_context.get("notes") or ""

                    name_note = ""
                    if name:
                        name_note = f"Caller Name: {name} (Strict Rule: Pronounce caller's name exactly as '{name}'. Do not repeat the caller's name in every sentence).\n"

                    context_msg = (
                        "<caller_context>\n"
                        f"Name: {name}\n"
                        f"{name_note}"
                        f"Phone: {phone}\n"
                        f"Inquiry Subject: {subject}\n"
                        "<caller_message>\n"
                        f"{message}\n"
                        "</caller_message>\n"
                        "</caller_context>"
                    )
                    turns.append(
                        genai_types.Content(
                            parts=[genai_types.Part.from_text(text=context_msg)],
                            role="user",
                        )
                    )

                if getattr(self, "initial_prompt", None):
                    turns.append(
                        genai_types.Content(
                            parts=[genai_types.Part.from_text(text=self.initial_prompt)],
                            role="user",
                        )
                    )

                if turns:
                    turn_complete = True if getattr(self, "initial_prompt", None) else (False if self.initial_greeting else True)
                    await session.send_client_content(
                        turns=turns,
                        turn_complete=turn_complete,
                    )

                async def send_audio_loop():
                    try:
                        while True:
                            chunk = await self._audio_input_queue.get()
                            await session.send_realtime_input(
                                audio=genai_types.Blob(
                                    data=chunk,
                                    mime_type=f"audio/pcm;rate={self.input_sample_rate}"
                                )
                            )
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        print(f"[Gemini Live Stream Error] send_audio exception: {e}")

                async def send_text_loop():
                    try:
                        while True:
                            text = await self._text_input_queue.get()
                            print(f"[Gemini Live Stream] Sending text input to model: {text[:80]}...")
                            await session.send_realtime_input(text=text)
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        print(f"[Gemini Live Stream Error] send_text exception: {e}")

                event_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()

                async def receive_loop():
                    try:
                        while True:
                            if self._closed:
                                break
                            async for response in session.receive():
                                if getattr(response, "go_away", None):
                                    print(f"[Gemini Live Stream Warning] Received GoAway notice: {response.go_away}")
                                    await event_queue.put({"type": "go_away", "details": str(response.go_away)})
                                
                                server_content = getattr(response, "server_content", None)
                                tool_call = getattr(response, "tool_call", None)

                                if server_content:
                                    if getattr(server_content, "model_turn", None):
                                        for part in server_content.model_turn.parts:
                                            if getattr(part, "inline_data", None) and part.inline_data.data:
                                                if audio_output_callback:
                                                    if inspect.iscoroutinefunction(audio_output_callback):
                                                        await audio_output_callback(part.inline_data.data)
                                                    else:
                                                        audio_output_callback(part.inline_data.data)

                                    if getattr(server_content, "interim_input_transcription", None) and server_content.interim_input_transcription.text:
                                        print(f"  [DIAGNOSTIC] Interim input_transcription: '{server_content.interim_input_transcription.text}'")

                                    if getattr(server_content, "input_transcription", None) and server_content.input_transcription.text:
                                        user_text = server_content.input_transcription.text
                                        print(f"  [DIAGNOSTIC] Finalized input_transcription received: '{user_text}'")
                                        await event_queue.put({
                                            "type": "user",
                                            "text": user_text
                                        })

                                    if getattr(server_content, "output_transcription", None) and server_content.output_transcription.text:
                                        if not getattr(self, "_diagnostic_model_started", False):
                                            print(f"  [DIAGNOSTIC] Gemini response model_turn / output_transcription started!")
                                            self._diagnostic_model_started = True
                                        await event_queue.put({
                                            "type": "gemini",
                                            "text": server_content.output_transcription.text
                                        })

                                    if getattr(server_content, "turn_complete", False):
                                        self._diagnostic_model_started = False
                                        print(f"  [DIAGNOSTIC] turn_complete event received")
                                        await event_queue.put({"type": "turn_complete"})

                                    if getattr(server_content, "interrupted", False):
                                        print("[Gemini Live Stream] Interruption detected by model.")
                                        if audio_interrupt_callback:
                                            if inspect.iscoroutinefunction(audio_interrupt_callback):
                                                await audio_interrupt_callback()
                                            else:
                                                audio_interrupt_callback()
                                        await event_queue.put({"type": "interrupted"})

                                if tool_call:
                                    original_fcs = list(getattr(tool_call, "function_calls", []))
                                    responses_dict = {}
                                    
                                    for fc in original_fcs:
                                        func_name = fc.name
                                        args = dict(fc.args) if fc.args else {}
                                        if func_name == "get_faq":
                                            args["language"] = "en"
                                        print(f"\n  [Gemini Live Tool Call] -> {func_name}({json.dumps(args, ensure_ascii=False)})")
                                        
                                        result_data = None
                                        if func_name in self.tool_mapping:
                                            try:
                                                tool_func = self.tool_mapping[func_name]
                                                if inspect.iscoroutinefunction(tool_func):
                                                    result_data = await tool_func(**args)
                                                else:
                                                    result_data = await asyncio.to_thread(tool_func, **args)
                                            except Exception as e:
                                                print(f"  [Gemini Live Tool Exception] {e}")
                                                result_data = {"error": f"Execution failed for {func_name}: {str(e)}"}
                                        else:
                                            result_data = {"error": f"Unknown tool: {func_name}"}

                                        print(f"  [Gemini Live Tool Result] <- {json.dumps(result_data, ensure_ascii=False, default=str)}\n")
                                        
                                        responses_dict[id(fc)] = genai_types.FunctionResponse(
                                            name=func_name,
                                            id=getattr(fc, "id", None),
                                            response={"result": json.dumps(result_data, ensure_ascii=False, default=str)}
                                        )

                                    function_responses = [responses_dict[id(fc)] for fc in original_fcs]
                                    await session.send_tool_response(function_responses=function_responses)
                                    await event_queue.put({"type": "tool_call", "function_calls": [fc.name for fc in original_fcs]})
                            
                            logger.debug("session.receive() iterator ended (e.g. after turn_complete); re-entering receive loop.")

                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        print(f"[Gemini Live Stream Error] receive_loop error: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                        await event_queue.put({"type": "error", "error": str(e)})
                    finally:
                        await event_queue.put(None)

                # Launch concurrent IO tasks
                audio_task = asyncio.create_task(send_audio_loop())
                text_task = asyncio.create_task(send_text_loop())
                recv_task = asyncio.create_task(receive_loop())

                try:
                    while True:
                        event = await event_queue.get()
                        if event is None:
                            break
                        yield event
                        if event.get("type") == "error":
                            break
                finally:
                    logger.debug("[Gemini Live Stream] Cleaning up session IO tasks.")
                    audio_task.cancel()
                    text_task.cancel()
                    recv_task.cancel()
                    await asyncio.gather(audio_task, text_task, recv_task, return_exceptions=True)

        except Exception as e:
            print(f"[Gemini Live Stream Error] Connection failure: {type(e).__name__}: {e}")
            raise
        finally:
            self._is_connected = False
            print("[Gemini Live Stream] Session closed.")

    async def connect(
        self,
        audio_output_callback: Optional[Callable[[bytes], Any]] = None,
        audio_interrupt_callback: Optional[Callable[[], Any]] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        """
        Non-blocking lifecycle connection entrypoint.
        Starts session execution in a background task and dispatches event notifications to optional callback.
        """
        if self._session_task and not self._session_task.done():
            return

        async def _runner():
            try:
                async for event in self.start_session(
                    audio_output_callback=audio_output_callback,
                    audio_interrupt_callback=audio_interrupt_callback,
                ):
                    if event and event_callback:
                        if inspect.iscoroutinefunction(event_callback):
                            await event_callback(event)
                        else:
                            event_callback(event)
            except asyncio.CancelledError:
                print("[Gemini Live Stream] Background connect runner cancelled.")
            except Exception as e:
                print(f"[Gemini Live Stream Error] Background runner error: {e}")

        self._session_task = asyncio.create_task(_runner())

    async def finish(self) -> None:
        """Gracefully shuts down background tasks and closes GenAI SDK client resources."""
        if self._closed:
            return
        self._closed = True
        logger.debug("[Gemini Live Stream] Finishing client operations...")
        if self._session_task and not self._session_task.done():
            self._session_task.cancel()
            try:
                await self._session_task
            except (asyncio.CancelledError, Exception):
                pass
            self._session_task = None

        try:
            if hasattr(self.client, "aio") and hasattr(self.client.aio, "close") and callable(self.client.aio.close):
                res = self.client.aio.close()
                if inspect.isawaitable(res):
                    await res
            elif hasattr(self.client, "close") and callable(self.client.close):
                res = self.client.close()
                if inspect.isawaitable(res):
                    await res
        except Exception as e:
            print(f"[Gemini Live Stream Error] Exception during GenAI client closure: {e}")
        print("[Gemini Live Stream] Client finished and resources cleanly released.")

    async def close(self) -> None:
        """Alias for finish to maintain uniform lifecycle interface."""
        await self.finish()
