"""
Framework-agnostic voice pipeline orchestrator.
Coordinates WebSocket Media Streams and Gemini Live real-time bidirectional audio streaming and tool calling.
"""

import os
import asyncio
import json
import base64
import inspect
import audioop
import time
import re
from typing import Optional

from agent.streaming.gemini_live_stream import GeminiLiveStreamClient
from agent.session.call_session import CallSession
from agent.audio.codecs import resample_pcm16


class VoicePipelineOrchestrator:
    """
    Orchestrates real-time bi-directional conversational voice turns for a single phone call using Gemini Live API.
    """
    def __init__(
        self,
        websocket,
        call_id: str,
        stream_sid: str,
        opening_intent: Optional[str] = None,
        lead_id: Optional[str] = None,
        lead_name: Optional[str] = None,
        lead_city: Optional[str] = None,
        lead_phone: Optional[str] = None,
        lead_email: Optional[str] = None,
        lead_subject: Optional[str] = None,
        lead_message: Optional[str] = None,
        caller_context: Optional[dict] = None
    ):
        self.websocket = websocket
        self.call_id = call_id
        self.stream_sid = stream_sid
        self.opening_intent = opening_intent
        self.lead_id = lead_id
        self.lead_name = lead_name
        self.lead_city = lead_city
        self.lead_phone = lead_phone
        self.lead_email = lead_email
        self.lead_subject = lead_subject
        self.lead_message = lead_message

        # Assemble full caller context dictionary
        self.caller_context = caller_context or {}
        if self.lead_name: self.caller_context.setdefault("name", self.lead_name)
        if self.lead_city: self.caller_context.setdefault("city", self.lead_city)
        if self.lead_phone: self.caller_context.setdefault("phone", self.lead_phone)
        if self.lead_email: self.caller_context.setdefault("email", self.lead_email)
        if self.lead_subject: self.caller_context.setdefault("subject", self.lead_subject)
        if self.lead_message: self.caller_context.setdefault("message", self.lead_message)

        self.lead_name = self.lead_name or self.caller_context.get("name")
        self.lead_city = self.lead_city or self.caller_context.get("city")
        self.lead_phone = self.lead_phone or self.caller_context.get("phone")
        self.lead_email = self.lead_email or self.caller_context.get("email")
        self.lead_subject = self.lead_subject or self.caller_context.get("subject")
        self.lead_message = self.lead_message or self.caller_context.get("message")

        # Determine greeting based on call intent and whether caller name is already known
        if self.lead_name and self.lead_name.lower() not in ("there", "none", ""):
            city_str = f" in {self.lead_city}" if self.lead_city else ""
            if self.opening_intent == "outbound_booking_form":
                first_name = self.caller_context.get("first_name") or self.lead_name.split()[0]
                clean_first_name = re.sub(r"[\x00-\x1F<>\"\\{}]", "", first_name).strip()[:50]
                if not clean_first_name:
                    clean_first_name = "the customer"
                doctor = self.caller_context.get("doctor", "your selected doctor")
                self.greeting = (
                    f"Hi {clean_first_name}, this is Kiara from Ultimate Smile Design. I'm calling to confirm that we've received your appointment booking request with {doctor}{city_str}. "
                    "Your consultation has been scheduled, and our team will contact you shortly to confirm the details. Do you have any other questions I can help you with?"
                )
            elif self.opening_intent == "outbound_smile_preview":
                first_name = self.caller_context.get("first_name") or self.lead_name.split()[0]
                clean_first_name = re.sub(r"[\x00-\x1F<>\"\\{}]", "", first_name).strip()[:50]
                if not clean_first_name:
                    clean_first_name = "the customer"
                self.greeting = (
                    f"Hi {clean_first_name}, this is Kiara from Ultimate Smile Design. I see you just tried out your AI Smile Preview! "
                    f"How did you like your new smile, and would you like to book an appointment with our authorized smile designer{city_str}?"
                )
            elif self.opening_intent in ("follow-up", "outbound_contact_form"):
                self.greeting = (
                    f"Hi {self.lead_name}, this is Kiara from Ultimate Smile Design, "
                    f"following up on your{city_str} smile consultation enquiry. How can I help you today?"
                )
            else:
                self.greeting = (
                    f"Hi {self.lead_name}, this is Kiara from Ultimate Smile Design. "
                    f"How can I help you today?"
                )
        else:
            if self.opening_intent == "outbound_booking_form":
                city_str = f" {self.lead_city}" if self.lead_city else ""
                doctor = self.caller_context.get("doctor", "your selected doctor") if self.caller_context else "your selected doctor"
                self.greeting = (
                    f"Hello! This is Kiara from Ultimate Smile Design. I'm calling to confirm that we've received your appointment booking request with {doctor}{city_str}. "
                    "Your consultation has been scheduled, and our team will contact you shortly to confirm the details. Do you have any other questions I can help you with?"
                )
            elif self.opening_intent == "outbound_smile_preview":
                city_str = f" in {self.lead_city}" if self.lead_city else ""
                self.greeting = (
                    f"Hello! This is Kiara from Ultimate Smile Design. I see you just tried out your AI Smile Preview online! "
                    f"How did you like your new smile, and would you like to book an appointment with our authorized smile designer{city_str}?"
                )
            elif self.opening_intent in ("follow-up", "outbound_contact_form"):
                city_str = f" {self.lead_city}" if self.lead_city else ""
                self.greeting = (
                    f"Hello! This is Kiara from Ultimate Smile Design, "
                    f"following up on your{city_str} smile consultation enquiry. May I know your name, please?"
                )
            else:
                self.greeting = (
                    "Hello! Thank you for calling Ultimate Smile Design. "
                    "My name is Kiara. May I know your name, please?"
                )

        # Initialize session and Gemini Live client
        self.session = CallSession(call_id=call_id, opening_intent=opening_intent, lead_id=lead_id)
        if self.caller_context:
            self.session.update_user_info(
                name=self.caller_context.get("name"),
                phone=self.caller_context.get("phone"),
                email=self.caller_context.get("email"),
                city=self.caller_context.get("city"),
                subject=self.caller_context.get("subject"),
                intent=self.caller_context.get("subject") or opening_intent,
                notes=self.caller_context.get("message") or self.caller_context.get("notes")
            )
        print(f"[Orchestrator] Pipeline Mode: LIVE (using GeminiLiveStreamClient for CallSid: {call_id})")
        
        self.gemini_live_client = None
        self.tts_provider_name = "Gemini Live"
        self.mulaw_frame_size = 160
        self.prebuffer_threshold = 800  # 100ms jitter buffer (absorbs internet streaming jitter)
        self.output_buffer = bytearray()
        self._send_audio_task: Optional[asyncio.Task] = None
        self._outbound_chunk_counter: int = 1
        self._turn_start_time: Optional[float] = None

        # Persistent resampler states per stream stage
        self._inbound_resample_state: Optional[tuple] = None
        self._outbound_resample_state: Optional[tuple] = None
        
        self._is_running: bool = False
        self._stopped: bool = False
        self._assistant_turn_complete: bool = False
        self._waiting_for_user_since: Optional[float] = None
        self._silence_state: str = "active"
        self._silence_monitor_task: Optional[asyncio.Task] = None


    async def _silence_monitor(self) -> None:
        """Hybrid background task to monitor for user silence. Acts as a safety net if no VAD/events trigger for 15s."""
        while self._is_running and not self._stopped:
            # If audio is currently playing or actively streaming through buffer, do not count user silence
            if len(self.output_buffer) > 0 or (self._send_audio_task and not self._send_audio_task.done()):
                await asyncio.sleep(1)
                continue

            if self._waiting_for_user_since is not None:
                elapsed = time.time() - self._waiting_for_user_since
                state = getattr(self, "_silence_state", "active")
                
                # Stage 1: User silent for 15 seconds after assistant finishes speaking
                if state == "stage_1_waiting" and elapsed >= 15:
                    self._silence_state = "stage_1_prompting"
                    self._waiting_for_user_since = None
                    
                    # Dynamically determine the caller's active spoken language
                    active_lang = "en"
                    if getattr(self, "session", None):
                        # 1. First prioritize recent user turns in conversation history
                        history = getattr(self.session, "conversation_history", [])
                        user_turns = [t for t in history if t.get("role") == "user"]
                        detected_from_history = None
                        if user_turns:
                            last_text = (user_turns[-1].get("content") or user_turns[-1].get("text") or "").strip()
                            if re.search(r"[\u0A80-\u0AFF]", last_text) or any(w in last_text.lower() for w in [
                                "kem cho", "su chhe", "shu chhe", "ketla", "thashe", "nathi", "tamare", "tame",
                                "karo", "bolo ne", "saru", "kaho", "barabar", "aavse", "chhe", "gujarati", "gujlish"
                            ]):
                                detected_from_history = "gu"
                            elif re.search(r"[\u0900-\u097F]", last_text) or any(w in last_text.lower() for w in [
                                "namaste", "kaise", "kya", "kitna", "batao", "bataiye", "hindi", "suno", "haan",
                                "haanji", "theek", "acha", "boliye", "kariye", "hoga", "chahiye"
                            ]):
                                detected_from_history = "hi"

                        if detected_from_history:
                            active_lang = detected_from_history
                        else:
                            pref = getattr(self.session, "preferred_language", "en")
                            if pref in ("hi", "gu", "en"):
                                active_lang = pref

                    if active_lang == "hi":
                        silence_text = "The user has been silent for 15 seconds. In your refined female voice, politely ask in natural Hindi: 'नमस्ते, क्या आप अभी भी कॉल पर हैं?' and wait for their reply."
                    elif active_lang == "gu":
                        silence_text = "The user has been silent for 15 seconds. In your refined female voice, politely ask in natural Gujarati: 'નમસ્તે, શું તમે હજુ લાઈન પર છો?' and wait for their reply."
                    else:
                        silence_text = "The user has been silent for 15 seconds. In your refined female voice, politely ask in English: 'Hello, are you still there?' and wait for their reply."
                    
                    print(f"[Orchestrator] User silent for {elapsed:.1f}s. Sending language-aware ({active_lang}) silence check.")
                    if getattr(self, "gemini_live_client", None):
                        await self.gemini_live_client.send_text(silence_text)

                # Stage 2: User silent for 25 seconds AFTER assistant finishes asking 'Hello, are you still there?'
                elif state == "stage_2_waiting" and elapsed >= 25:
                    print(f"[Orchestrator] User silent for {elapsed:.1f}s after check prompt. Disconnecting call automatically.")
                    self._silence_state = "disconnecting"
                    if getattr(self, "websocket", None):
                        try:
                            await self.websocket.send_text(json.dumps({
                                "event": "stop",
                                "streamSid": self.stream_sid
                            }))
                        except Exception:
                            pass
                        try:
                            await self.websocket.close(code=1000)
                        except Exception:
                            pass
                    await self.stop()
                    break
            await asyncio.sleep(1)

    async def _send_buffered_live_audio(self) -> None:
        """Send buffered audio to WebSocket in Smartflo-compliant chunk sizes (multiples of 160 bytes)."""
        CHUNK_MULTIPLIER = 2  # 320 bytes (40ms) - reduces WS frame overhead and matches carrier buffers
        target_chunk_size = getattr(self, "mulaw_frame_size", 160) * CHUNK_MULTIPLIER
        FRAME_DURATION = 0.02 * CHUNK_MULTIPLIER
        
        try:
            next_send_at = time.monotonic()
            starved_time = None

            while self._is_running and not self._stopped:
                now = time.monotonic()
                
                if len(self.output_buffer) >= target_chunk_size:
                    if starved_time is not None:
                        # Buffer was starved, reset timing to prevent bursting chunks
                        next_send_at = now
                        starved_time = None

                    if now >= next_send_at:
                        chunk_data = bytes(self.output_buffer[:target_chunk_size])
                        del self.output_buffer[:target_chunk_size]
                        
                        payload = base64.b64encode(chunk_data).decode("utf-8")
                        media_message = {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {
                                "payload": payload,
                                "chunk": self._outbound_chunk_counter
                            },
                        }
                        self._outbound_chunk_counter += 1
                        if self.websocket and not getattr(self.websocket, "client_state", None) == "DISCONNECTED":
                            try:
                                await self.websocket.send_text(json.dumps(media_message))
                            except Exception as e:
                                print(f"[Orchestrator Warning] Failed to send buffered live audio: {e}")
                                break
                        else:
                            break
                        
                        next_send_at += FRAME_DURATION
                        sleep_for = next_send_at - time.monotonic()
                        if sleep_for > 0:
                            await asyncio.sleep(sleep_for)
                        else:
                            next_send_at = time.monotonic()
                            await asyncio.sleep(0.001)
                    else:
                        sleep_for = max(0.001, next_send_at - now)
                        await asyncio.sleep(sleep_for)
                else:
                    # Buffer has less than target_chunk_size
                    if starved_time is None:
                        starved_time = now
                    
                    # Check if turn is complete based on Gemini's explicit signal or a long timeout (fallback)
                    is_turn_complete = getattr(self, "_assistant_turn_complete", False)
                    is_timeout = (now - starved_time) > 2.0

                    if is_turn_complete or is_timeout:
                        if len(self.output_buffer) > 0:
                            trailing = bytes(self.output_buffer)
                            self.output_buffer.clear()
                            
                            remainder = len(trailing) % getattr(self, "mulaw_frame_size", 160)
                            if remainder > 0:
                                pad_amount = getattr(self, "mulaw_frame_size", 160) - remainder
                                trailing += b"\xff" * pad_amount
                                
                            payload = base64.b64encode(trailing).decode("utf-8")
                            media_message = {
                                "event": "media",
                                "streamSid": self.stream_sid,
                                "media": {
                                    "payload": payload,
                                    "chunk": self._outbound_chunk_counter
                                },
                            }
                            self._outbound_chunk_counter += 1
                            if self.websocket and getattr(self.websocket, "client_state", None) != "DISCONNECTED":
                                try:
                                    await self.websocket.send_text(json.dumps(media_message))
                                except Exception:
                                    pass
                        break
                    
                    # Sleep slightly and check again
                    await asyncio.sleep(0.01)
            
            # When buffer is fully drained and assistant is done speaking
            if len(self.output_buffer) == 0 and self._is_running and not self._stopped:
                # Send Mark Event to indicate end of speech turn
                if self.websocket and not getattr(self.websocket, "client_state", None) == "DISCONNECTED":
                    try:
                        await self.websocket.send_text(json.dumps({
                            "event": "mark",
                            "streamSid": self.stream_sid,
                            "mark": {"name": "assistant_turn_complete"}
                        }))
                        print(f"[Orchestrator] Sent 'mark' event for StreamSid: {self.stream_sid}")
                    except Exception:
                        pass
                        
                self.session.transition_state("listening")
                if getattr(self, "_silence_state", "") == "stage_1_prompting":
                    print("[Orchestrator] Finished asking if user is there. Now waiting 25s for user response before disconnect.")
                    self._silence_state = "stage_2_waiting"
                    self._waiting_for_user_since = time.time()
                elif getattr(self, "_silence_state", "") != "stage_2_waiting":
                    self._silence_state = "stage_1_waiting"
                    self._waiting_for_user_since = time.time()
        except asyncio.CancelledError:
            pass

    async def _on_live_audio_output(self, data: bytes) -> None:
        """Callback for Gemini Live audio output chunks (24kHz 16-bit PCM)."""
        if not self._is_running or self._stopped or not self.stream_sid:
            return
        try:
            # Direct single-stage 24kHz -> 8kHz stateful resampling (3:1 integer decimation)
            resampled_8k, self._outbound_resample_state = resample_pcm16(
                data, 24000, 8000, self._outbound_resample_state
            )
            mulaw_data = audioop.lin2ulaw(resampled_8k, 2)
            self.output_buffer.extend(mulaw_data)
            if self.session.state != "speaking":
                self._turn_start_time = time.time()
            self.session.transition_state("speaking")
            self._assistant_turn_complete = False
            if getattr(self, "_silence_state", "") != "stage_1_prompting":
                self._waiting_for_user_since = None
            
            # Kick off playback task once jitter buffer threshold is satisfied or if sender task is active
            if self._send_audio_task is None or self._send_audio_task.done():
                if len(self.output_buffer) >= getattr(self, "prebuffer_threshold", 800):
                    self._send_audio_task = asyncio.create_task(self._send_buffered_live_audio())
        except Exception as e:
            print(f"[Orchestrator Error] Error sending live audio to WebSocket: {e}")

    async def _on_live_interruption(self) -> None:
        """Callback invoked when Gemini Live detects caller interruption (barge-in)."""
        if self._stopped:
            return
        # Early-turn echo guard: Ignore interruptions within first 1.2s of assistant speaking
        if self._turn_start_time and (time.time() - self._turn_start_time < 1.2):
            print(f"[Orchestrator] Ignoring early-turn echo interruption ({time.time() - self._turn_start_time:.2f}s < 1.2s)")
            return

        print("[Orchestrator] Gemini Live server interruption received! Clearing output buffer and media stream.")
        if getattr(self, "_send_audio_task", None) and not self._send_audio_task.done():
            self._send_audio_task.cancel()
        self.output_buffer.clear()
        self._outbound_resample_state = None
        self._assistant_turn_complete = False
        self._turn_start_time = None
        self.session.transition_state("listening")
        self._waiting_for_user_since = None
        self._silence_state = "active"
        if self.websocket and not getattr(self.websocket, "client_state", None) == "DISCONNECTED":
            try:
                await self.websocket.send_text(json.dumps({
                    "event": "clear",
                    "streamSid": self.stream_sid
                }))
                print(f"[Orchestrator] Sent 'clear' event for StreamSid: {self.stream_sid}")
            except Exception as e:
                print(f"[Orchestrator Warning] Failed to send clear event during live interruption: {e}")

    async def _on_live_event(self, event: dict) -> None:
        """Callback for general Gemini Live events (transcripts, turn completions, tool calls)."""
        if not event or not isinstance(event, dict):
            return
        event_type = event.get("type")
        if event_type == "user":
            # User spoke! Reset silence timer back to active conversation
            self._waiting_for_user_since = None
            self._silence_state = "active"
            text = event.get("text", "").strip()
            if text:
                print(f"[Live STT Transcript] User: '{text}'")
                self.session.add_transcript(text, role="user")
                switched = self.session.update_language_if_requested(text)
                if switched and getattr(self, "gemini_live_client", None):
                    lang_name = {"hi": "Hindi / Hinglish", "gu": "Gujarati / Gujlish", "en": "English"}.get(self.session.preferred_language, "English")
                    print(f"[Orchestrator] Language switch detected -> Steering live session to {lang_name}")
                    try:
                        await self.gemini_live_client.send_text(f"[SYSTEM INSTRUCTION: Caller switched language to {lang_name}. Respond 100% in {lang_name} using feminine grammatical inflections starting on this turn.]")
                    except Exception as e:
                        print(f"[Orchestrator Warning] Failed to send language switch directive: {e}")
        elif event_type == "gemini":
            text = event.get("text", "").strip()
            if text:
                print(f"[Live Output Transcript] Assistant: '{text}'")
                self.session.add_transcript(text, role="assistant")
        elif event_type == "turn_complete":
            print("[Orchestrator] Live turn complete; output buffer draining to caller.")
            self._assistant_turn_complete = True
            self._turn_start_time = None
        elif event_type == "error":
            print(f"[Orchestrator Error] Gemini Live event error: {event.get('error')}")
            if hasattr(self, "_gemini_reconnect_event") and ("1008" in str(event.get('error')) or "GoAway" in str(event.get('error')) or "Connection aborted" in str(event.get('error'))):
                self._gemini_reconnect_event.set()
        elif event_type == "go_away":
            print(f"[Orchestrator] Received go_away event. Triggering seamless reconnect.")
            if hasattr(self, "_gemini_reconnect_event"):
                self._gemini_reconnect_event.set()
    def _build_live_tool_mapping(self):
        from agent.tools.check_city_coverage import check_city_coverage
        from agent.tools.capture_lead import capture_lead
        from agent.tools.get_faq import get_faq
        from agent.tools.handoff import human_handoff

        def live_capture_lead(**kwargs):
            kwargs.setdefault("call_id", self.call_id)
            kwargs.setdefault("preferred_language", self.session.preferred_language)
            res = capture_lead(**kwargs)
            self.session.update_user_info(
                name=kwargs.get("name"),
                phone=kwargs.get("phone"),
                city=kwargs.get("city"),
                intent=kwargs.get("intent"),
                notes=kwargs.get("notes")
            )
            return res

        def live_check_city(**kwargs):
            res = check_city_coverage(**kwargs)
            if kwargs.get("city"):
                self.session.update_user_info(city=kwargs.get("city"))
            return res

        def live_get_faq(**kwargs):
            kwargs.setdefault("language", self.session.preferred_language)
            res = get_faq(**kwargs)
            if kwargs.get("topic"):
                self.session.update_topic(kwargs.get("topic"))
            return res

        def live_handoff(**kwargs):
            kwargs.setdefault("call_id", self.call_id)
            res = human_handoff(**kwargs)
            self.session.booking_stage = "handoff"
            return res

        return {
            "capture_lead": live_capture_lead,
            "check_city_coverage": live_check_city,
            "get_faq": live_get_faq,
            "human_handoff": live_handoff,
        }



    def _build_initial_prompt(self) -> str:
        msg = (self.lead_message or "").strip()
        subj = (self.lead_subject or "").strip()
        known_name = self.lead_name if (self.lead_name and self.lead_name.lower() != "there") else ""

        city_display = self.lead_city or "their city"
        location_rule = (
            f"CRITICAL LOCATION & PARTNER RULE: The caller's consultation will be arranged with our Authorized USD Smile Designer in {city_display}. "
            f"If they ask where the meeting or consultation will take place, confidently confirm it will be with our local Authorized Smile Designer in {city_display}. "
            f"NEVER invent, guess, or suggest other cities (like Hyderabad, Delhi, or Mumbai) if the user explicitly selected {city_display}."
        )
        crucial_instruction = (
            f"CRITICAL: You already know the caller's Name and City ({city_display}) from the form they just submitted. "
            "DO NOT ask them for their name or city. Acknowledge the details they provided naturally, "
            f"and move directly to the consultation guidance. {location_rule}"
        )
        if self.opening_intent == "outbound_booking_form":
            doctor = self.caller_context.get("doctor") if self.caller_context else None
            if doctor:
                crucial_instruction = (
                    f"CRITICAL: You already know the caller's First Name, Last Name, City ({city_display}), and selected Doctor ({doctor}) from the form they just submitted. "
                    f"DO NOT ask them for their name, city, or doctor. You MUST open by confirming the appointment with {doctor} in {city_display} and asking if they have any other questions. {location_rule}"
                )
        elif self.opening_intent == "outbound_smile_preview":
            crucial_instruction = (
                f"CRITICAL: You already know the caller's Name and City ({city_display}) because they just completed the AI Smile Preview online. "
                f"DO NOT ask for their name or city again. Open the call by acknowledging they saw their AI smile and ask if they want to book a consultation. {location_rule}"
            )

        if msg:
            subj_str = f" regarding {subj}" if subj else ""
            if self.opening_intent == "outbound_booking_form":
                first_name = self.caller_context.get("first_name") or known_name.split()[0] if known_name else "the customer"
                clean_first_name = re.sub(r"[\x00-\x1F<>\"\\{}]", "", first_name).strip()[:50]
                if not clean_first_name:
                    clean_first_name = "the customer"
                doctor = self.caller_context.get("doctor", "your selected doctor")
                greet_phrase = f"Hi {clean_first_name}, this is Kiara from Ultimate Smile Design. I'm calling to confirm that we've received your appointment booking request with {doctor} in {city_display}. Your consultation has been scheduled, and our team will contact you shortly to confirm the details."
                
                initial_prompt = (
                    f"The outbound call has connected to {clean_first_name}. "
                    f"Speak now: State exactly '{greet_phrase}', then answer the question from their message in about 2-3 natural sentences using our knowledge base, and then ask: 'Do you have any other questions or any additional details you’d like to know?'. "
                    f"{crucial_instruction}"
                )
            else:
                enquiry_type = "consultation enquiry"
                if known_name:
                    greet_phrase = f"Hi {known_name}, this is Kiara from Ultimate Smile Design following up on your {enquiry_type}{subj_str}."
                else:
                    greet_phrase = f"Hello! This is Kiara from Ultimate Smile Design following up on your {enquiry_type}{subj_str}."
                
                initial_prompt = (
                    f"The outbound call has connected to {known_name or 'the customer'}. "
                    f"Speak now: Greet the customer ('{greet_phrase}'), then answer the question from their message in about 2-3 natural sentences using our knowledge base and an intuitive real-world analogy, and then ask: 'Do you have any other questions or any additional details you’d like to know?'. "
                    f"{crucial_instruction}"
                )
            return initial_prompt
        elif subj:
            if self.opening_intent == "outbound_booking_form":
                enquiry_type = "appointment booking request"
            else:
                enquiry_type = "consultation enquiry"
            if known_name:
                greet_phrase = f"Hi {known_name}, this is Kiara from Ultimate Smile Design following up on your enquiry regarding {subj}."
            else:
                greet_phrase = f"Hello! This is Kiara from Ultimate Smile Design following up on your enquiry regarding {subj}."
            
            initial_prompt = (
                f"The outbound call has connected to {known_name or 'the customer'}. "
                f"Speak now: Greet the customer ('{greet_phrase}'), then address their topic from the caller context clearly in 2-3 sentences based on our knowledge base and an intuitive analogy, and then ask: 'Do you have any other questions or any additional details you’d like to know?'. "
                f"{crucial_instruction}"
            )
            return initial_prompt
        else:
            if known_name:
                initial_prompt = (
                    f"The outbound call has connected to {known_name}. "
                    f"Greet the caller by saying exactly: '{self.greeting}' and wait for their reply. "
                    f"{crucial_instruction}"
                )
            else:
                initial_prompt = (
                    f"The call has just connected. "
                    f"Greet the caller by saying exactly: '{self.greeting}' and wait for their reply."
                )
            return initial_prompt

    async def _manage_gemini_connection(self) -> None:
        is_reconnect = False
        while self._is_running and not self._stopped:
            try:
                self.gemini_live_client = GeminiLiveStreamClient(
                    call_id=self.call_id,
                    preferred_language="multi",
                    initial_greeting=None if is_reconnect else self.greeting,
                    initial_prompt=None if is_reconnect else self._build_initial_prompt(),
                    tool_mapping=self._build_live_tool_mapping(),
                    caller_context=self.caller_context,
                    opening_intent=self.opening_intent
                )
                
                self._gemini_reconnect_event.clear()
                
                await self.gemini_live_client.connect(
                    audio_output_callback=self._on_live_audio_output,
                    audio_interrupt_callback=self._on_live_interruption,
                    event_callback=self._on_live_event,
                )

                client = self.gemini_live_client
                if is_reconnect:
                    full_history = self.session.conversation_history if self.session.conversation_history else []
                    
                    history_lines = []
                    for t in full_history:
                        role = "USER" if t.get('role') == "user" else "KIARA (YOU)"
                        text = (t.get('content') or t.get('text') or '').strip()
                        if text:
                            history_lines.append(f"{role}: {text}")
                    
                    history_text = "\n".join(history_lines) if history_lines else "No previous turns recorded."
                    session_memory = self.session.get_session_context_prompt()
                    caller_name = self.lead_name or "the caller"
                    reconnect_prompt = (
                        f"You are continuing an ongoing phone call as Kiara with {caller_name}. "
                        "Maintain your exact calm, warm, refined, and confident tone and natural speaking pitch at all times. "
                        "Do NOT say hello again or greet the caller. Retain full context of the ongoing discussion.\n\n"
                        f"{session_memory}\n\n"
                        f"--- RECENT CONVERSATION CONTEXT ---\n"
                        f"{history_text}\n"
                        f"--- END CONTEXT ---\n\n"
                        "Stay in character and wait for the caller to speak, or smoothly continue where you left off."
                    )
                    if client:
                        await client.send_text(reconnect_prompt)
                
                try:
                    await asyncio.wait_for(self._gemini_reconnect_event.wait(), timeout=540.0)
                    print("[Orchestrator] Gemini reconnection event triggered (go_away or error).")
                except asyncio.TimeoutError:
                    print("[Orchestrator] 9-minute proactive reconnect triggered.")
                
                if self._is_running and not self._stopped:
                    old_client = self.gemini_live_client
                    self.gemini_live_client = None
                    if old_client:
                        await old_client.finish()
                    is_reconnect = True
                    print("[Orchestrator] Spawning new Gemini Live session for seamless continuation...")
                    
            except Exception as e:
                print(f"[Orchestrator] Failed to manage Gemini Live connection: {e}")
                if not is_reconnect:
                    await self.stop()
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                break

    async def start(self) -> None:
        """Starts the voice pipeline connections and initiates the opening greeting."""
        self._is_running = True
        self._waiting_for_user_since = None
        self._silence_state = "active"
        self._silence_monitor_task = asyncio.create_task(self._silence_monitor())
        self._gemini_reconnect_event = asyncio.Event()
        
        print(f"[Orchestrator] Starting voice pipeline for CallSid: {self.call_id}, StreamSid: {self.stream_sid}")
        
        self.session.add_transcript(self.greeting, role="assistant")
        self.session.transition_state("listening")
        
        self._manage_gemini_connection_task = asyncio.create_task(self._manage_gemini_connection())

    async def handle_media_payload(self, ulaw_base64: str) -> None:
        """
        Processes an inbound chunk of 8kHz ulaw audio from Smartflo Streams and sends to Gemini Live.
        """
        if not self._is_running:
            return

        audio_bytes = base64.b64decode(ulaw_base64)
        
        if self.gemini_live_client and not getattr(self.gemini_live_client, "_closed", False):
            try:
                pcm8k = audioop.ulaw2lin(audio_bytes, 2)
                
                # RMS Digital Noise Gate: Suppress ambient line noise, breathing, and mobile AEC artifacts
                rms = audioop.rms(pcm8k, 2)
                if rms < 400:
                    pcm8k = b"\x00" * len(pcm8k)

                pcm16k, self._inbound_resample_state = resample_pcm16(
                    pcm8k, 8000, 16000, self._inbound_resample_state
                )
                await self.gemini_live_client.send_audio(pcm16k)
            except Exception as e:
                print(f"[Orchestrator Error] Failed to process and send live audio: {e}")

    async def stop(self) -> None:
        """Tears down client connections deterministically."""
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self._is_running = False
        if hasattr(self, "_gemini_reconnect_event"):
            self._gemini_reconnect_event.set()
        self._inbound_resample_state = None
        self._outbound_resample_state = None
        self._outbound_chunk_counter = 1
        if getattr(self, "_silence_monitor_task", None) and not self._silence_monitor_task.done():
            self._silence_monitor_task.cancel()
        if getattr(self, "_send_audio_task", None) and not self._send_audio_task.done():
            self._send_audio_task.cancel()
        print(f"[Orchestrator] Stopping voice pipeline for CallSid: {self.call_id}")
        
        if getattr(self, "gemini_live_client", None):
            try:
                await self.gemini_live_client.finish()
            except Exception as e:
                print(f"[Orchestrator Error] Failed to cleanly finish Gemini Live Client: {e}")
                
        print(f"[Orchestrator] Call Session complete. Total turns: {len(self.session.conversation_history)}")