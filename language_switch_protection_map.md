# LANGUAGE SWITCH PROTECTION MAP
### Complete Exhaustive Reference — Every File, Every Line, Every Rule

> This document is the single source of truth for protecting the working multilingual behavior.
> Before modifying **any** file in this project, check whether it appears below.

---

## FILE 1: `agent/streaming/gemini_live_stream.py`
**This is the highest-priority engine file. The entire language switching system originates here.**

---

### ITEM 1.1 — `MANDATORY_TRANSCRIPTION_INSTRUCTION` (Lines 22–29)
```python
MANDATORY_TRANSCRIPTION_INSTRUCTION = (
    "TRANSCRIPTION POLICY — TRANSCRIPTION ONLY. "
    "When Hindi speech is clearly recognized, transcribe it in Devanagari where practical. "
    "When Gujarati speech is clearly recognized, transcribe it in Gujarati script where practical. "
    "Do not use transcript script alone to determine the spoken response language. "
    "Gujarati audio may sometimes be transcribed in Devanagari or imperfect English text. "
    "Response-language selection is controlled only by the TURN LANGUAGE ROUTER."
)
```
| Property | Value |
|---|---|
| **What it does** | Forces Gemini to transcribe Hindi/Gujarati audio into native scripts (Devanagari/Gujarati), NOT into English. Tells the model that the transcript is an unreliable signal for language. |
| **Why required** | Without this, Gujarati speech often transcribes as broken English. The model then reads "English" tokens and responds in English. |
| **What breaks if removed** | Gujarati and Hindi callers may receive English responses immediately. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |
| **Dependent on** | Fed directly into `TURN_LANGUAGE_ROUTER` and `start_session()` guard (Line 485–486). |
| **One word change risk** | Yes. Removing "TRANSCRIPTION ONLY" could cause Gemini to treat it as a response rule rather than a transcription rule. |

---

### ITEM 1.2 — `TURN_LANGUAGE_ROUTER` Full Block (Lines 31–100)
```python
TURN_LANGUAGE_ROUTER = """
### TURN LANGUAGE ROUTER — HIGHEST PRIORITY CONVERSATIONAL RULE

Before generating EVERY response, silently determine TURN_LANGUAGE
from the caller's CURRENT raw spoken audio.
...
Recompute TURN_LANGUAGE independently on EVERY caller turn.
The previous assistant language NEVER locks the current response.
The previous caller language NEVER overrides a clear current utterance.
The opening greeting language NEVER becomes a conversational default.
The number of previous turns in another language is irrelevant.
...
If any lower-priority instruction or example conflicts with TURN_LANGUAGE,
TURN_LANGUAGE ALWAYS WINS.
"""
```
| Property | Value |
|---|---|
| **What it does** | The absolute master rule. Forces Gemini to re-evaluate language per turn from raw audio alone, and explicitly forbids every other signal (history, tool data, CRM data, greeting language) from winning. |
| **Why required** | LLMs naturally lock into a language. This prompt aggressively fights that inertia on every single turn. |
| **What breaks if removed** | The model will get stuck in the opening language (usually English). Language switching stops. |
| **What breaks if reworded** | Weakening "ALWAYS WINS" or "NEVER locks" to softer language could cause language drift. |
| **What breaks if reordered** | This must come BEFORE the persona prompt so it has highest priority in the attention window. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |
| **Subsections that are especially fragile** | |
| • `Recompute TURN_LANGUAGE independently on EVERY caller turn.` | Core rule — never soften this. |
| • `The previous assistant language NEVER locks the current response.` | Prevents sticky-language bug. |
| • `The opening greeting language NEVER becomes a conversational default.` | Prevents English greeting locking the call. |
| • `English dental vocabulary inside Hindi/Gujarati is language-neutral.` | Without this, words like "appointment" cause the router to call Hindi utterances "English". |
| • Examples block (lines 60–62): `"तो आज treatment हमारा करेगा कौन?" → HINDI` | These examples calibrate the router. Removing or changing them shifts the boundary between languages. |
| • `PROMPT-CONTENT LANGUAGE SAFETY` section (lines 83–99) | Explicitly prevents any prompt example, script, analogy, or tool result from leaking into the spoken language. Critical. |
| **One word change risk** | Yes. High. |

---

### ITEM 1.3 — `get_faq` Tool Schema — `language` Parameter (Lines 306–314)
```python
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
```
| Property | Value |
|---|---|
| **What it does** | Forces Gemini to evaluate the current `TURN_LANGUAGE` *before* calling the FAQ tool, and pass it as a parameter. |
| **Why required** | The FAQ database returns content in the language specified. If Gemini passes `"en"` while the caller is speaking Gujarati, the returned content is English and the model drifts to English in its response. |
| **What breaks if removed** | Gemini defaults to `"en"` and all FAQ results come back in English, causing language leakage mid-conversation. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |
| **Depends on** | `agent/tools/get_faq.py` `language` parameter. |

---

### ITEM 1.4 — `input_audio_transcription` with `language_auto` (Lines 511–523)
```python
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
```
| Property | Value |
|---|---|
| **What it does** | `language_auto=genai_types.LanguageAuto()` tells the Gemini Live API-level transcription layer to auto-detect language rather than being hard-coded to English. The `custom_vocabulary` helps the transcription engine correctly recognize brand-specific English terms even inside Hindi/Gujarati audio. |
| **Why required** | If `language_auto` is replaced with a hard-coded `language_code="en"`, the transcription will force English, breaking the TURN_LANGUAGE_ROUTER before it even runs. |
| **What breaks if removed** | `language_auto` removed → transcription is English-only → entire language switch breaks at the API level. |
| **What breaks if vocabulary modified** | Removing brand terms from `custom_vocabulary` causes them to be mis-transcribed in Hindi/Gujarati audio, potentially changing perceived meaning. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH (`language_auto` line). `custom_vocabulary` is SAFE TO EDIT CAREFULLY (can add terms; do not remove existing ones). |

---

### ITEM 1.5 — `[SESSION ANCHOR]` Turn at Call Start (Lines 553–571)
```python
turns = [
    genai_types.Content(
        parts=[genai_types.Part.from_text(
            text=(
                "[SESSION ANCHOR] This is a live voice call. "
                "Do not infer the caller's language from name, city, form data or metadata. "
                "Listen to the caller's spoken audio. "
                "Respond in the language of their current clear spoken sentence. "
                "For short ambiguous turns, preserve the language from the recent conversation context."
            )
        )],
        role="user",
    ),
    genai_types.Content(
        parts=[genai_types.Part.from_text(
            text="Understood. I will listen to the caller's spoken audio and match the language of their current clear sentence, while preserving context on ambiguous turns."
        )],
        role="model",
    ),
]
```
| Property | Value |
|---|---|
| **What it does** | Injects a fake "user→model" conversation at session start to establish a listen-first posture *before* the real call begins. The model confirms it will not guess language from metadata. |
| **Why required** | When a Gujarati-named caller submits a form with an English subject line, Gemini could pre-warm itself to English. This anchor explicitly voids that. |
| **What breaks if removed** | The model guesses the language from CRM data (name, city, subject) and replies in the wrong language on the very first turn. |
| **What breaks if reordered** | This must be injected FIRST before the caller context block. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 1.6 — City Intentionally Omitted from CRM Context Injection (Lines 574–577)
```python
# Solution B: City is intentionally OMITTED from this context message.
# The city is still available server-side in self.caller_context for tool
# calls (book_consultation, check_city_coverage, check_dentist), but it
# is NOT sent to Gemini here so it cannot bias Gemini's language choice.
```
| Property | Value |
|---|---|
| **What it does** | The caller's **city** field is deliberately NOT included in the `<caller_context>` text sent to Gemini, even though it is stored in `self.caller_context` for tool calls. |
| **Why required** | A Gujarati city name (like "Surat", "Vadodara") in a text block would bias the model towards Gujarati before hearing audio. An English city name ("New Delhi") would bias it towards English. Omitting it prevents this. |
| **What breaks if you add city back** | Language bias reintroduced at call start. First-turn language errors return. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH. Never add `city` to the `context_msg` block. |

---

### ITEM 1.7 — `start_session()` Transcription Safety Guard (Lines 484–486)
```python
setup_instruction = self.system_instruction
if MANDATORY_TRANSCRIPTION_INSTRUCTION not in setup_instruction:
    setup_instruction = f"{MANDATORY_TRANSCRIPTION_INSTRUCTION}\n\n{setup_instruction}"
```
| Property | Value |
|---|---|
| **What it does** | A defensive guard that ensures `MANDATORY_TRANSCRIPTION_INSTRUCTION` is present in the final WebSocket setup payload, even if someone accidentally calls `start_session()` with a custom `system_instruction` that doesn't include it. |
| **Why required** | Prevents silent failure: if code is refactored and the router gets dropped from the prompt, this catches it. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 1.8 — `preferred_language="multi"` in `GeminiLiveStreamClient` call (Pipeline Line 875)
```python
self.gemini_live_client = GeminiLiveStreamClient(
    ...
    preferred_language="multi",
    ...
)
```
| Property | Value |
|---|---|
| **What it does** | Signals to the live stream client that no single language is preferred. Tools that use `self.preferred_language` as a fallback default (like `wrap_get_faq`) will use "multi" instead of "en". |
| **Why required** | If changed to `"en"`, `"hi"`, or `"gu"`, it would bias tool calls to always fetch in that language, breaking language switching for FAQ data. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH. Always keep as `"multi"`. |

---

## FILE 2: `agent/prompts/core/persona.md`

---

### ITEM 2.1 — Language Router Deference Declaration (Lines 1–4)
```markdown
# 1. LANGUAGE STYLE & DELIVERY — STYLE ONLY

**Language selection is owned exclusively by the system-level `TURN LANGUAGE ROUTER`.**
This file must never select, preserve, or lock the conversational language.
```
| Property | Value |
|---|---|
| **What it does** | Explicitly tells Gemini that this persona file has no authority over language selection. It defers to the router. |
| **Why required** | Without this, when Gemini reads "Use warm, polished, natural Indian-English," it might interpret that as a global language instruction rather than a style instruction. |
| **What breaks if removed** | Persona style rules begin to compete with the TURN_LANGUAGE_ROUTER. Indian-English style leaks into Gujarati/Hindi turns. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH (lines 3–4). Lines 1–2 are SAFE TO EDIT CAREFULLY (can rename the heading). |

---

### ITEM 2.2 — `ABSOLUTE LANGUAGE RULE` for Analogies (Lines 39–43)
```markdown
- **ABSOLUTE LANGUAGE RULE:**
  The entire comparison, objection response, explanation, analogy, CTA and follow-up question MUST be spoken in TURN_LANGUAGE.
  The analogy itself must also be translated/adapted into the current language.
  English wording inside this prompt is semantic reference material only.
  Never copy its language into the spoken answer unless TURN_LANGUAGE is ENGLISH.
```
| Property | Value |
|---|---|
| **What it does** | Prevents the Rolex, Bentley, Cartier analogies (written in English in the prompt) from making the model switch to English when explaining them. Forces everything to TURN_LANGUAGE. |
| **Why required** | Analogy phrasing is in English inside the prompt. Without this rule, Gemini naturally explains analogies in English regardless of caller language. |
| **What breaks if removed** | Agent answers in Gujarati, then suddenly switches to English to explain the Rolex analogy. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |
| **One word change risk** | Yes. Softening "MUST be spoken" or "NEVER" would cause drift. |

---

### ITEM 2.3 — Mandatory Female Grammar Rules with Native Script Examples (Lines 20–31)
```markdown
## 2.1 MANDATORY FEMALE GRAMMATICAL IDENTITY (STRICT GRAMMAR RULE)
...
- **Hindi Rules:**
  - ✅ Mandatory Feminine: Always use `-ती हूँ`, `-रही हूँ`, ...
  - ❌ Forbidden Masculine: NEVER say `बता सकता हूँ`, `करूँगा`, ...
- **Gujarati Rules:**
  - ✅ Mandatory Feminine (-ઈ): Always say `હું તમારી એલીટ કન્સલ્ટન્ટ કિયારા છું`, ...
  - ❌ Forbidden Masculine (-યો): NEVER say `હું સમજી ગયો`, `ગયો`, ...
```
| Property | Value |
|---|---|
| **What it does** | Provides exact Hindi and Gujarati verb forms. Without these examples, the model has no concrete training signal for correct feminine grammar in these scripts. |
| **Why required** | Language switching alone is not enough — the *form* of Hindi/Gujarati used must also be correct. If the model speaks Gujarati with masculine verbs, it sounds wrong to native speakers. |
| **What breaks if removed** | Hindi/Gujarati responses use masculine verbs (e.g., `मैं बता सकता हूँ` instead of `बता सकती हूँ`). |
| **Status** | ⚠️ SAFE TO EDIT CAREFULLY. You can add more examples or adjust existing ones. But do NOT remove the Devanagari/Gujarati-script examples entirely — they are the model's grammar anchor. |

---

### ITEM 2.4 — `FINAL RESPONSE CHECKLIST` (Lines 70–75)
```markdown
# 4. FINAL RESPONSE CHECKLIST
Before every response, ensure you:
1. Match the caller's current language accurately:
   - English → English
   - Gujarati → Conversational Gujarati (MUST be in Gujarati script...)
   - Hindi → Conversational Hindi (MUST be in Devanagari script...)
```
| Property | Value |
|---|---|
| **What it does** | A last-line-of-defense reminder before every response is generated. |
| **Why required** | Reinforces script enforcement (Gujarati in Gujarati script, Hindi in Devanagari). Without this, mixed-script responses appear (e.g., Romanized Hindi). |
| **Status** | ⚠️ SAFE TO EDIT CAREFULLY. Keep the principle and the native-script examples. |

---

## FILE 3: `agent/tools/get_faq.py`

---

### ITEM 3.1 — Post-Tool Language Safety Instructions (4 locations)
```python
# Location 1: Line 111–113 (Guidance found path)
"instruction": (
    "Use this guidance/reframing to personalize your response to the caller. "
    "Respond naturally in the language of the caller's CURRENT spoken turn. "
    "Do not read out instructions or metadata; speak conversationally as Kiara."
)

# Location 2: Line 123–125 (Guidance not found path)
"instruction": (
    "Use this guidance to respond naturally to the caller. "
    "Respond entirely in the language of the caller's CURRENT spoken turn."
)

# Location 3: Line 142–145 (FAQ not found path)
"instruction": (
    "Use these facts to answer the caller. "
    "Respond entirely in the language of the caller's CURRENT spoken turn. "
    "Do not let the language of this tool result determine the response language."
)

# Location 4: Line 167–170 (FAQ found path — primary return)
"instruction": (
    "Use these facts to answer the caller. "
    "Respond entirely in the language of the caller's CURRENT spoken turn. "
    "Do not let the language of this tool result determine the response language."
)
```
| Property | Value |
|---|---|
| **What it does** | Every code path that returns data from this tool appends an "instruction" key reminding Gemini to respond in the caller's current language, not the language of the returned content. |
| **Why required** | Tool results are typically English JSON. Gemini's attention bias toward the most recently read text (the tool result) causes it to reply in English. This instruction counters that on every tool call. |
| **What breaks if removed** | Any caller question that triggers `get_faq` will cause the agent to reply in English regardless of what language the caller is speaking. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 3.2 — Language Fallback Normalization (Line 81)
```python
lang = language.strip().lower() if language.strip().lower() in ("en", "hi", "gu") else "en"
```
| Property | Value |
|---|---|
| **What it does** | Safely normalizes the `language` parameter. Anything outside of `en/hi/gu` defaults to `en`. |
| **Why required** | Prevents crashes or weird behavior if `TURN_LANGUAGE` somehow passes a string like "ENGLISH" or "gujarati". |
| **Status** | ⚠️ SAFE TO EDIT CAREFULLY. If you add more languages in the future, update this whitelist. |

---

## FILE 4: `agent/prompts/intents/*.md` — All Four Intent Files

**Each file has a "Language ownership" disclaimer. These are all CRITICAL.**

---

### ITEM 4.1 — `inbound.md` Language Ownership Disclaimer (Line 11)
```markdown
**Language ownership:** This intent file controls call flow only. Spoken language is selected exclusively by the system-level `TURN LANGUAGE ROUTER` on every caller turn.
```
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 4.2 — `outbound_contact.md` Language Ownership Disclaimer (Line 12)
```markdown
**Language ownership:** This intent file controls content/flow only. It must never establish or preserve a spoken language; the system-level `TURN LANGUAGE ROUTER` decides language from each current caller turn.
```
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 4.3 — `outbound_booking.md` Opening Language Scope Warning (Line 15)
```markdown
**OPENING LANGUAGE SCOPE (CRITICAL):** The exact greeting below is a one-time opening script only. Its English language MUST NOT become the conversation default. After the caller starts speaking, every clear caller turn is independently language-routed by the system-level `TURN LANGUAGE ROUTER`.
```
| Property | Value |
|---|---|
| **What it does** | Explicitly warns the model that the English booking greeting does not establish English as the permanent language. |
| **Why required** | Outbound booking calls always open in English ("Hi [Name], this is Kiara from Ultimate Smile Design..."). Without this disclaimer, the model locks into English for the entire call. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 4.4 — `outbound_smile_preview.md` TURN_LANGUAGE Reference and Language Ownership (Lines 15 and 34)
```markdown
# Line 15:
render it naturally in the current `TURN_LANGUAGE`; do not use duplicated Hindi/Gujarati/English fixed scripts in this intent file.

# Line 34:
**Language ownership:** This intent file controls preview-specific content only. The opening/content examples must never establish a persistent language preference; every clear caller turn is independently routed by the system-level `TURN LANGUAGE ROUTER`.
```
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

## FILE 5: `agent/pipeline.py`

---

### ITEM 5.1 — Silence Monitor Language Instruction (Lines 262–270)
```python
silence_text = (
    "The caller has been silent for 15 seconds. "
    "Politely ask whether they are still on the call. "
    "Use the same language the caller was most recently speaking in this conversation. "
    "If the caller was speaking English, use polished Indian-English. "
    "If Hindi, use natural conversational Hindi with feminine grammar. "
    "If Gujarati, use natural conversational Gujarati with feminine grammar. "
    "Do not change the conversational language merely because this instruction itself is written in English. "
    "Then wait for the caller's reply."
)
```
| Property | Value |
|---|---|
| **What it does** | When a caller is silent for 15 seconds, the pipeline injects a text instruction into Gemini asking it to check on the caller. This instruction explicitly tells Gemini not to switch to English just because the injected text is English. |
| **Why required** | This is a system-generated text injection. Without the last sentence ("Do not change the conversational language..."), Gemini would read an English instruction and reply with "Are you still there?" in English, breaking the language of a Hindi/Gujarati call. |
| **What breaks if removed** | 15-second silence prompts always fire in English regardless of the caller's language. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 5.2 — Reconnection Fallback Prompt Language Rule (Lines 859–864)
```python
fallback_prompt = (
    "[CALL CONTINUATION FALLBACK CONTEXT]\n\n"
    ...
    "LANGUAGE RULE:\n"
    "Do not assume English, Hindi or Gujarati from metadata.\n"
    "Do not speak yet.\n"
    "Wait silently for the caller's next actual spoken audio.\n"
    "On their next clear spoken turn, respond in the language of that CURRENT spoken turn.\n"
    "If the next turn is only a short or ambiguous acknowledgment, infer the conversational language from the recent dialogue context.\n\n"
    ...
)
```
| Property | Value |
|---|---|
| **What it does** | When the Gemini Live connection rotates (reconnects) and there is no session resumption handle, this fallback context is injected. It contains an explicit `LANGUAGE RULE` block. |
| **Why required** | Without this rule, a reconnection resets the language state and the model defaults to English on the next turn, breaking a live Hindi/Gujarati call after a seamless reconnect. |
| **What breaks if removed** | Language resets to English on every Gemini session rotation (every ~9 minutes or on GoAway events). |
| **Status** | ✅ CRITICAL — DO NOT TOUCH |

---

### ITEM 5.3 — `preferred_language="multi"` in Pipeline (Line 875)
```python
self.gemini_live_client = GeminiLiveStreamClient(
    preferred_language="multi",
    ...
)
```
| Property | Value |
|---|---|
| **What it does** | Tells all internal tools that no single language is preferred. The `wrap_get_faq` wrapper in `gemini_live_stream.py` uses `self.preferred_language` as fallback default. |
| **Why required** | If this is "en", every FAQ call defaults to English. |
| **Status** | ✅ CRITICAL — DO NOT TOUCH. Must always be `"multi"`. |

---

### ITEM 5.4 — Initial Prompt Multilingual Statement for Unknown-Intent Calls (Line 790)
```python
initial_prompt = (
    f"The call has just connected. "
    f"Say exactly: '{self.greeting}' Then wait for their reply. "
    "You are fluent in Gujarati, Hindi, and English; after greeting, seamlessly converse in whichever language the caller uses."
)
```
| Property | Value |
|---|---|
| **What it does** | For calls with no subject/message/intent, this is the fallback initial prompt. It explicitly tells Gemini to match the caller's language. |
| **Why required** | Even on plain inbound calls with no context, the model needs to be told to match the caller rather than stay in English. |
| **Status** | ⚠️ SAFE TO EDIT CAREFULLY. Keep the multilingual instruction at the end. |

---

## FILE 6: `agent/session/call_session.py`

---

### ITEM 6.1 — `preferred_language` Declared as Deprecated (Lines 25–26)
```python
# Deprecated compatibility field (Gemini Live now natively handles conversational language)
self.preferred_language: str = preferred_language
```
| Property | Value |
|---|---|
| **What it does** | Marks `preferred_language` as a deprecated field. Gemini Live now handles language natively. |
| **Why required for awareness** | If a future developer sees this field and tries to "fix" it by wiring it back into the prompt, they will break the native language routing. |
| **Status** | ⚠️ SAFE TO EDIT CAREFULLY. Do NOT reconnect this field to the system prompt or use it to force a language in Gemini. |

---

## GOLDEN RULES (READ BEFORE ANY EDIT)

1. **Never rename `TURN_LANGUAGE` or `TURN LANGUAGE ROUTER`.** These exact strings are referenced across `gemini_live_stream.py`, `persona.md`, and all four intent files. They act as a binding contract.

2. **Never add city to the CRM context block sent to Gemini** (`gemini_live_stream.py` lines 588–597). City is intentionally withheld to prevent language bias.

3. **Never hard-code `language_code="en"` in `input_audio_transcription`.** Always keep `language_auto=genai_types.LanguageAuto()`.

4. **Never change `preferred_language="multi"` to a specific language** in the `GeminiLiveStreamClient()` call in `pipeline.py`.

5. **Never remove the `"instruction"` key** from any `get_faq` return path. It is what keeps FAQ results from causing English drift.

6. **Never remove the "Language ownership" disclaimers** from any intent file.

7. **Any text injected into Gemini via `send_text()` that is written in English must include an explicit rule** saying "do not change language because this instruction is in English." (See the silence monitor pattern.)

---

## WHAT IS SAFE TO EDIT

| File | Safe to Change |
|---|---|
| `persona.md` | Tone descriptions (warm/polished/natural), brand facts, analogy list content, all of section 2, 3 (except ABSOLUTE LANGUAGE RULE), section 4 formatting |
| `guardrails.md` | Any guardrail content. Just ensure multi-language examples are included for new rules |
| Intent files (`inbound.md`, etc.) | Flow rules, call scripts, greeting text — but NEVER the Language ownership disclaimer |
| Knowledge base JSONs (`data/`) | Add/edit facts freely. Tool's post-instruction handles language safely |
| `call_session.py` | State machine, topic tracking, booking stages — none touch language |
| `pipeline.py` | Silence timer values, audio buffer sizes, reconnection logic — **except Items 5.1–5.4 above** |
| `gemini_live_stream.py` | `custom_vocabulary` list (add terms), tool schemas for non-FAQ tools, audio config values — **except Items 1.1–1.8 above** |

---

## DO NOT TOUCH — MASTER CHECKLIST

Copy this checklist and check off before every major edit:

- [ ] `gemini_live_stream.py` Lines 22–29 — `MANDATORY_TRANSCRIPTION_INSTRUCTION` intact
- [ ] `gemini_live_stream.py` Lines 31–100 — `TURN_LANGUAGE_ROUTER` intact
- [ ] `gemini_live_stream.py` Lines 306–314 — `get_faq` `language` schema intact
- [ ] `gemini_live_stream.py` Line 512 — `language_auto=genai_types.LanguageAuto()` present
- [ ] `gemini_live_stream.py` Lines 553–571 — `[SESSION ANCHOR]` turn intact
- [ ] `gemini_live_stream.py` Lines 574–577 — City NOT in context block
- [ ] `gemini_live_stream.py` Lines 484–486 — `start_session()` guard intact
- [ ] `persona.md` Lines 3–4 — Router deference intact
- [ ] `persona.md` Lines 39–43 — `ABSOLUTE LANGUAGE RULE` intact
- [ ] `persona.md` Lines 20–31 — Hindi/Gujarati feminine grammar examples intact
- [ ] `get_faq.py` Lines 111–113, 123–125, 142–145, 167–170 — All 4 `instruction` keys intact
- [ ] `inbound.md` Line 11 — Language ownership disclaimer intact
- [ ] `outbound_contact.md` Line 12 — Language ownership disclaimer intact
- [ ] `outbound_booking.md` Line 15 — Opening language scope warning intact
- [ ] `outbound_smile_preview.md` Lines 15 & 34 — TURN_LANGUAGE reference and disclaimer intact
- [ ] `pipeline.py` Lines 262–270 — Silence monitor language instruction intact
- [ ] `pipeline.py` Lines 859–864 — Reconnect fallback `LANGUAGE RULE` block intact
- [ ] `pipeline.py` Line 875 — `preferred_language="multi"` not changed
