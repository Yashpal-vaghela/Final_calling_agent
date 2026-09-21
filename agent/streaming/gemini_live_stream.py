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
    "TRANSCRIPTION POLICY — TRANSCRIPTION ONLY. "
    "When Hindi speech is clearly recognized, transcribe it in Devanagari where practical. "
    "When Gujarati speech is clearly recognized, transcribe it in Gujarati script where practical. "
    "Do not use transcript script alone to determine the spoken response language. "
    "Gujarati audio may sometimes be transcribed in Devanagari or imperfect English text (e.g. 'तमे जणावो ने पेला' is Gujarati in meaning despite Devanagari script). "
    "However, genuine complete Hindi or English turns are real language switches. "
    "Response-language selection is controlled only by the TURN LANGUAGE ROUTER."
)

TURN_LANGUAGE_ROUTER = """
### TURN LANGUAGE ROUTER — HIGHEST PRIORITY CONVERSATIONAL RULE

Before generating EVERY response, silently determine TURN_LANGUAGE
from the caller's CURRENT raw spoken audio.

- Clear English speech -> ENGLISH
- Clear Hindi speech -> HINDI
- Clear Gujarati speech -> GUJARATI
- ONLY if the current utterance is genuinely language-neutral or acoustically unclear
  -> preserve the language of the most recent CLEAR caller utterance.

Recompute TURN_LANGUAGE independently on EVERY caller turn.

The previous assistant language NEVER locks the current response.
The previous caller language NEVER overrides a clear current utterance.
The opening greeting language NEVER becomes a conversational default.
The number of previous turns in another language is irrelevant.
There is NO language loyalty after 2 turns, 5 turns, or 20 turns. A clear current-turn language ALWAYS wins.

English dental vocabulary inside Hindi or Gujarati is language-neutral.
Words such as appointment, doctor, dentist, treatment, braces, aligners,
veneers, crown, implant, bridge, scan, cost, price, consultation,
and smile design do NOT make a Hindi or Gujarati sentence ambiguous.

Determine TURN_LANGUAGE from the COMPLETE spoken utterance:
meaning, grammar, sentence structure, function words, pronunciation, and cadence together.
Do NOT determine language from transcript script alone.
Do NOT switch languages based on isolated acknowledgement words alone (e.g. "yes", "haan", "okay", "good", "great").
However, if an acknowledgement word is followed by any words, phrase, or question in another language (e.g. "Okay, what is the cost of veneers?" -> ENGLISH; "अच्छा, मुझे बताइए कि इसमें कितना खर्चा होगा?" -> HINDI; "હા, તો મને કહો કે કન્સલ્ટેશન ક્યારે થશે?" -> GUJARATI), you MUST switch to that new language IMMEDIATELY.

CRITICAL DISTINCTIONS:
- Phonetic Gujarati transcribed in Devanagari (e.g. "तमे जणावो ने पेला") has Gujarati linguistic meaning -> GUJARATI.
- Genuine Hindi complete utterance (e.g. "तो आज treatment हमारा करेगा कौन?", "मुझे लगता है कि मेरे लिए विनियर्स सही रहेंगे...") has clear Hindi grammar and meaning -> HINDI immediately.
- Sustained English complete utterance (e.g. "Can you explain that again?", "Can you tell me the medicine name for pain relief?") -> ENGLISH immediately.
- Clear English questions of any length (e.g. "What is the cost?", "How long does it take?", "Is it painful?", "Who is the doctor?") -> ENGLISH immediately.
- Explicit language requests ("Hindi mein bolo", "English please", "Gujarati ma vaat karo") OVERRIDE immediately.

### MANDATORY MULTIDIRECTIONAL LANGUAGE SWITCHING (ZERO RESIDUAL LOCK):
You MUST switch languages immediately on the turn the caller switches:
1. SWITCHING AWAY FROM GUJARATI:
   - When previous turns were in Gujarati, and the caller asks a question or speaks in Hindi:
     YOU MUST SWITCH TO HINDI ON THIS EXACT TURN. Continuing in Gujarati is FORBIDDEN.
   - When previous turns were in Gujarati, and the caller asks a question or speaks in English:
     YOU MUST SWITCH TO ENGLISH ON THIS EXACT TURN. Continuing in Gujarati is FORBIDDEN.
2. SWITCHING AWAY FROM HINDI:
   - When previous turns were in Hindi, and the caller asks a question or speaks in Gujarati:
     YOU MUST SWITCH TO GUJARATI ON THIS EXACT TURN. Continuing in Hindi is FORBIDDEN.
   - When previous turns were in Hindi, and the caller asks a question or speaks in English:
     YOU MUST SWITCH TO ENGLISH ON THIS EXACT TURN. Continuing in Hindi is FORBIDDEN.
3. SWITCHING AWAY FROM ENGLISH:
   - When previous turns were in English, and the caller asks a question or speaks in Gujarati:
     YOU MUST SWITCH TO GUJARATI ON THIS EXACT TURN. Continuing in English is FORBIDDEN.
   - When previous turns were in English, and the caller asks a question or speaks in Hindi:
     YOU MUST SWITCH TO HINDI ON THIS EXACT TURN. Continuing in English is FORBIDDEN.

A single turn in a new language is 100% sufficient to trigger an instant language switch.
Never maintain conversational language inertia across caller turns.

This language routing operates on EVERY turn, regardless of whether zero tools or multiple tools are called. Tool calls never determine, anchor, or lock the response language.

Examples:
- "तो आज treatment हमारा करेगा कौन?" -> HINDI
- "મારે braces કરાવવાના છે." -> GUJARATI
- "Can you explain that again?" -> ENGLISH

After deciding the language for THIS caller turn:

RESPOND IN TURN_LANGUAGE.
YOU MUST RESPOND UNMISTAKABLY IN TURN_LANGUAGE FOR THE ENTIRE RESPONSE.

Do not mention TURN_LANGUAGE.
Do not explain that a language switch occurred.
Do not continue in the previous response language after a clear switch.

This language lock applies ONLY to the CURRENT response.

On the NEXT caller turn, discard the previous TURN_LANGUAGE decision
and determine the language again from the new raw spoken audio.

Tool results, JSON, CRM data, caller name, city, form metadata,
opening greeting language, prompt examples, scripts, analogies,
suggested phrasing, retrieved knowledge, and prior assistant wording
NEVER determine TURN_LANGUAGE.

### PROMPT-CONTENT LANGUAGE SAFETY

Examples, scripts, quoted sample replies, analogies, persona examples,
knowledge snippets, tool results, guidance text, and previous assistant wording
are CONTENT REFERENCES ONLY.

They NEVER determine TURN_LANGUAGE.

If any example, analogy, script, retrieved fact, or suggested phrasing is written
in a language different from TURN_LANGUAGE, preserve its meaning but translate
and naturally express the ENTIRE spoken response in TURN_LANGUAGE.

Proper names and brand names such as Rolex, Bentley, Cartier,
Ultimate Smile Design, and Haresh Savani may remain unchanged.

If any lower-priority instruction or example conflicts with TURN_LANGUAGE,
TURN_LANGUAGE ALWAYS WINS.
"""

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
        resumption_handle: Optional[str] = None,
        turn_complete_on_start: Optional[bool] = None,
    ):
        self.call_id = call_id or "live-session"
        self.session = session
        self.preferred_language = preferred_language
        self.input_sample_rate = input_sample_rate
        self.caller_context = caller_context or {}
        self.opening_intent = opening_intent or self.caller_context.get("opening_intent")
        self.initial_greeting = initial_greeting
        self.initial_prompt = initial_prompt
        self.turn_complete_on_start = turn_complete_on_start
        self._resumption_handle: Optional[str] = resumption_handle
        self._latest_valid_resumption_handle: Optional[str] = resumption_handle
        
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
            self.system_instruction = (
                f"{MANDATORY_TRANSCRIPTION_INSTRUCTION}\n\n"
                f"{TURN_LANGUAGE_ROUTER}\n\n"
                f"{intent_directive}"
                f"{system_instruction}"
            )
        else:
            base_prompt = build_system_prompt(self.opening_intent or "inbound")

            self.system_instruction = (
                f"{MANDATORY_TRANSCRIPTION_INSTRUCTION}\n\n"
                f"{TURN_LANGUAGE_ROUTER}\n\n"
                f"{intent_directive}"
                f"{base_prompt}\n\n"
                "---\n\n"
                "## CURRENT SESSION (LIVE VOICE CALL)\n"
                "Speak warmly, naturally, and concisely. "
                "Follow the style, business, tool, and safety rules from the loaded prompt files.\n\n"
                "### FINAL PER-TURN SPOKEN LANGUAGE REMINDER\n"
                "Before every response, determine the caller's spoken language from the complete latest caller turn. "
                "Respond in that language. A clear current-turn language overrides the previous conversation language. "
                "CRITICAL: If the caller changes language (e.g. Gujarati to Hindi/English, Hindi to Gujarati/English, or English to Hindi/Gujarati), YOU MUST SWITCH IMMEDIATELY. Never remain stuck in the previous conversational language. "
                "Do not switch languages based only on script or isolated acknowledgement words.\n\n"
                "### STRICT NO MEDICAL DISCLAIMERS REMINDER\n"
                "CRITICAL: NEVER append automatic medical, legal, or informational disclaimers (e.g., 'not a medical diagnosis', 'advice is for informational purposes only', 'અમારી સલાહ મેડિકલ સલાહ કે નિદાન નથી', 'हमारी सलाह मेडिकल सलाह नहीं है') at the end of answers. Keep all responses natural, clean, and conversational."
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
        self._ready_event: asyncio.Event = asyncio.Event()
        self._diagnostic_model_started: bool = False

    @property
    def latest_valid_resumption_handle(self) -> Optional[str]:
        """Returns the latest valid session resumption handle received from Gemini Live."""
        return self._latest_valid_resumption_handle

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
            description=(
                "Retrieve authoritative answers and approved conversational guidance from the local knowledge base. "
                "YOU MUST CALL THIS TOOL whenever the caller asks about ANY of the following topics:\n"
                "DENTAL CONCERNS: gaps between teeth, yellow or stained teeth, crooked or uneven teeth, chipped teeth, gummy smile, missing teeth, tooth sensitivity, discoloration\n"
                "TREATMENTS & PROCEDURES: veneers, crowns, clear aligners, AD-Aligners, implants, whitening, gum contouring, smile makeover, smile design, Ultimate Smile Design\n"
                "MATERIALS & COMPARISONS: E.max, E.max vs zirconia, zirconia, lithium disilicate, veneer materials, which material is better, natural-looking materials\n"
                "PROCESS & TIMELINE: digital smile design, DSD, 3D scanning, how the process works, how long it takes, steps in the treatment, planning process, digital preview\n"
                "PRICING & VALUE: cost, price, investment, worth, starting price, consultation fee, payment\n"
                "COMPANY & AUTHORITY: Ultimate Smile Design, Advance Dental Export, Haresh Savani, master ceramist, how many cases, 120000 cases, cities covered, authorized dentists\n"
                "COMPARISONS & OBJECTIONS: USD vs regular dentist, why choose USD, what makes USD different, local dentist comparison\n"
                "COMFORT & SAFETY: pain, discomfort, scared, safe, anaesthesia, procedure comfort, aftercare\n"
                "AI SMILE PREVIEW: virtual smile try-on, digital simulation, preview, before and after, how results look\n"
                "PROFESSION GUIDANCE: profession-based smile personalization (pass topic as 'profession_<profession>', e.g. 'profession_teacher', 'profession_lawyer')\n"
                "ANALOGIES: when an approved analogy is needed to clarify craftsmanship, materials, or a comparison (pass topic as 'analogy' or 'emax_vs_zirconia' or 'digital_smile_design_planning' etc.)\n"
                "OTHER TOPICS: warranty, authentication, dentist partnership, training courses, privacy, booking guidance\n"
                "Always call this tool before answering factual dental or company questions. Never answer from memory alone."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "topic": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description=(
                            "Topic or keyword query. Examples: 'veneers', 'cost_value', 'emax_vs_zirconia', "
                            "'digital_smile_design_planning', 'whitening', 'implants', 'about_ade_haresh_savani', "
                            "'local_dentist_vs_usd', 'process_timeline', 'cities_coverage', 'warranty', "
                            "'profession_teacher', 'profession_lawyer', 'analogy', 'ai_smile_preview', "
                            "'aftercare_comfort', 'safety_quality_materials', 'privacy_busy_schedule', "
                            "'dentist_partner_benefits', 'course_price'"
                        ),
                    ),
                    "language": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description=(
                            "Language of the caller's CURRENT clear spoken turn. "
                            "Must match TURN_LANGUAGE exactly: en for English, "
                            "hi for Hindi, gu for Gujarati. "
                            "Never use the previous conversational language "
                            "when the caller clearly switches languages."
                        ),
                    ),
                },
                required=["topic", "language"],
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
                "Submit the booking to the backend database. Speech alone does NOT save the booking.\n\n"
                "WHEN TO CALL THIS TOOL (MANDATORY — one of these conditions must be true):\n"
                "1. EXPLICIT BOOKING COMMAND: The caller gives a clear, unambiguous instruction to book, such as:\n"
                "   'Book my appointment', 'Schedule a consultation', 'Book kar do', 'Appointment book karo', "
                "   'મારી એપોઇન્ટમેન્ટ બુક કરો', 'Book karo', 'Consultation book kar do', 'হ্যাঁ বুক করুন'\n"
                "2. FINAL CONFIRMATION after Kiara has summarized full details (Doctor, City, Message) and asked "
                "   'Shall I go ahead and submit your consultation?': Caller says 'Yes', 'Go ahead', 'Haan', 'Ha', "
                "   'हां', 'हाँ कीजिए', 'હા', 'હા કરો', 'Confirm', 'Submit it', 'Yes go ahead'\n\n"
                "⚠ WHEN NOT TO CALL THIS TOOL:\n"
                "- CASUAL CONVERSATIONAL AFFIRMATIONS: Words like 'yes', 'okay', 'haan', 'good', 'great', 'perfect', "
                "'sure', 'fine' said in response to a FACTUAL QUESTION or GENERAL RESPONSE do NOT trigger booking.\n"
                "- INFORMATION QUESTIONS: Do not call if caller only says 'yes I understand', 'okay tell me more', "
                "'haan that sounds good' in context of learning about treatments, prices, or processes.\n"
                "- BOOKING NEVER ALREADY CONFIRMED: Do not call again if booking is already confirmed in this session.\n\n"
                "CONTEXT TEST: Before calling, verify the caller's response is to Kiara's explicit booking summary "
                "question ('Shall I go ahead and submit your consultation?'), not to a general conversational exchange.\n\n"
                "NEVER say 'I have submitted your request' or 'Your appointment is booked' without calling this tool!\n\n"
                "STRICT NEGATIVE CONSTRAINTS:\n"
                "- NEVER ask the caller for preferred appointment date, day, or time! Consultations are scheduled directly by our clinical coordinator who contacts the patient. You ONLY collect City, Doctor preference (optional), and Message (optional).\n"
                "- NEVER tell the caller to visit ultimatesmiledesign.com to book or schedule an appointment — you book it directly on this call!"
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

        update_caller_profile_tool = genai_types.FunctionDeclaration(
            name="update_caller_profile",
            description=(
                "Update or correct the caller's profile details (such as name or phone number). "
                "ONLY use this tool when the caller explicitly provides or corrects their own identity or contact details. "
                "NEVER invent a caller name. Never infer a caller name from unrelated words. "
                "Never autocomplete names. Never translate names. "
                "Never change a trusted form name unless the caller explicitly corrects it (e.g. 'My name isn't Kunal, it is Keval'). "
                "If a spoken name is uncertain, ask the caller to repeat it rather than guessing. "
                "It is better to omit the caller's name than use an uncertain or wrong name. "
                "For phone number changes, pass the complete 10-digit number. "
                "If the caller only provided a partial number, do NOT call this tool; ask them for the full 10-digit number. "
                "If the caller has already confirmed the phone number change, pass confirm_phone=True."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "name": genai_types.Schema(type=genai_types.Type.STRING, description="Caller's explicit full name or corrected name."),
                    "phone": genai_types.Schema(type=genai_types.Type.STRING, description="Caller's explicit 10-digit phone number."),
                    "confirm_phone": genai_types.Schema(type=genai_types.Type.BOOLEAN, description="Set to True ONLY if caller has explicitly confirmed the phone update."),
                },
                required=[],
            ),
        )

        return genai_types.Tool(
            function_declarations=[
                check_city_tool,
                get_faq_tool,
                book_consultation_tool,
                check_dentist_tool,
                cancel_consultation_tool,
                update_caller_profile_tool
            ]
        )

    def _build_default_tool_mapping(self) -> Dict[str, Callable]:
        """Maps schema names to callable execution wrappers supplying session parameters."""
        from agent.tools.check_dentist import check_dentist
        from agent.tools.caller_profile import update_caller_profile

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

        return {
            "capture_lead": wrap_capture_lead,
            "check_city_coverage": check_city_coverage,
            "get_faq": wrap_get_faq,
            "human_handoff": wrap_handoff,
            "book_consultation": book_consultation,
            "cancel_consultation": cancel_consultation,
            "check_dentist": check_dentist,
            "update_caller_profile": update_caller_profile,
            "update_contact_details": update_caller_profile,
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

        # Session Resumption Config
        if self._resumption_handle:
            session_resumption_cfg = genai_types.SessionResumptionConfig(handle=self._resumption_handle)
        else:
            session_resumption_cfg = genai_types.SessionResumptionConfig()

        # Context Window Compression Config (official SlidingWindow)
        context_compression_cfg = genai_types.ContextWindowCompressionConfig(
            sliding_window=genai_types.SlidingWindow()
        )

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
                turn_coverage=genai_types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    disabled=False,
                    prefix_padding_ms=500,
                    silence_duration_ms=650,
                ),
            ),
            session_resumption=session_resumption_cfg,
            context_window_compression=context_compression_cfg,
            tools=self.tools if self.tools else None,
        )

        print(f"[Gemini Live Stream] Connecting to Live API with model={self.model_name}, voice={self.voice_name}, resumption_handle_available={bool(self._resumption_handle)}")

        try:
            async with self.client.aio.live.connect(model=self.model_name, config=config) as session:
                self._is_connected = True
                self._ready_event.set()
                print("[Gemini Live Stream] Session established successfully.")

                # Only inject anchor turns and caller context on FIRST connection.
                # When resuming an existing session, the model already has conversational state.
                if not self._resumption_handle:
                    # Solution C: Language-neutral anchor turn injected FIRST so Gemini
                    # enters the session in a listen-first state rather than pre-guessing
                    # the caller's language from any context clue.
                    turns: list[genai_types.Content | genai_types.ContentDict] = [
                        genai_types.Content(
                            parts=[genai_types.Part.from_text(
                                text=(
                                    "[SESSION ANCHOR] This is a live voice call. "
                                    "Do not infer the caller's language from name, city, form data or metadata. "
                                    "Listen to the caller's spoken audio. "
                                    "Respond in the language of their current clear spoken sentence. "
                                    "Whenever the caller switches between English, Hindi, or Gujarati, switch your response language IMMEDIATELY to match. "
                                    "For isolated single-word acknowledgments alone (like just 'yes' or 'okay'), preserve the recent context, but any statement or question in a new language switches immediately."
                                )
                            )],
                            role="user",
                        ),
                        genai_types.Content(
                            parts=[genai_types.Part.from_text(
                                text="Understood. I will listen to the caller's spoken audio and match the language of their current sentence. If the caller switches between English, Hindi, or Gujarati, I will switch my response language immediately."
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

                    initial_prompt = getattr(self, "initial_prompt", None)
                    if initial_prompt:
                        turns.append(
                            genai_types.Content(
                                parts=[genai_types.Part.from_text(text=initial_prompt)],
                                role="user",
                            )
                        )

                    if turns:
                        if self.turn_complete_on_start is not None:
                            turn_complete = self.turn_complete_on_start
                        else:
                            turn_complete = True if initial_prompt else (False if self.initial_greeting else True)
                        await session.send_client_content(
                            turns=turns,
                            turn_complete=turn_complete,
                        )
                else:
                    print("[Gemini Resumption] Session resumed with existing handle; skipping anchor, context, and greeting injection.")

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
                                # Capture session resumption update from server
                                resumption_update = getattr(response, "session_resumption_update", None)
                                if resumption_update:
                                    resumable = getattr(resumption_update, "resumable", False)
                                    new_h = getattr(resumption_update, "new_handle", None)
                                    if resumable and new_h:
                                        self._latest_valid_resumption_handle = new_h
                                        print(f"[Gemini Resumption] Saved resumable handle (handle_available=True)")
                                        await event_queue.put({
                                            "type": "session_resumption_update",
                                            "handle": new_h,
                                            "resumable": True
                                        })
                                    else:
                                        print(f"[Gemini Resumption] Non-resumable or empty update received; retaining previous handle: handle_available={bool(self._latest_valid_resumption_handle)}")

                                # Capture go_away notice from server
                                go_away = getattr(response, "go_away", None)
                                if go_away:
                                    time_left = getattr(go_away, "time_left", None)
                                    print(f"[Gemini Live Stream Warning] Received GoAway notice: time_left={time_left}")
                                    await event_queue.put({
                                        "type": "go_away",
                                        "time_left": time_left,
                                        "details": str(go_away)
                                    })
                                
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

                                        if isinstance(result_data, dict):
                                            result_data.setdefault(
                                                "instruction",
                                                "Respond entirely in the language of the caller's CURRENT spoken turn. "
                                                "If the caller switched languages (e.g. from Gujarati to Hindi or English, "
                                                "or from Hindi to English or Gujarati), you MUST respond in their NEW language. "
                                                "Do not let the language of this tool result determine or lock the response language."
                                            )

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
            self._ready_event.clear()
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

    async def wait_until_ready(self, timeout: float = 10.0) -> None:
        """Waits until the Gemini Live session WebSocket connection is established and ready."""
        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)

    async def finish(self) -> None:
        """Gracefully shuts down background tasks and closes GenAI SDK client resources."""
        if self._closed:
            return
        self._closed = True
        self._is_connected = False
        self._ready_event.clear()
        logger.debug("[Gemini Live Stream] Finishing client operations...")
        if self._session_task and not self._session_task.done():
            self._session_task.cancel()
            try:
                await self._session_task
            except (asyncio.CancelledError, Exception):
                pass
            self._session_task = None

        try:
            aio_client = getattr(self.client, "aio", None)
            aio_close = getattr(aio_client, "close", None) if aio_client else None
            if callable(aio_close):
                res = aio_close()
                if inspect.isawaitable(res):
                    await res
            else:
                client_close = getattr(self.client, "close", None)
                if callable(client_close):
                    res = client_close()
                    if inspect.isawaitable(res):
                        await res
        except Exception as e:
            print(f"[Gemini Live Stream Error] Exception during GenAI client closure: {e}")
        print("[Gemini Live Stream] Client finished and resources cleanly released.")

    async def close(self) -> None:
        """Alias for finish to maintain uniform lifecycle interface."""
        await self.finish()
