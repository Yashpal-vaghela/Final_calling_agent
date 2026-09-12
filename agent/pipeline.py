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

import numpy as np
try:
    from agent.audio.silero import SileroVAD
except Exception as e:
    SileroVAD = None
    print(f"[Orchestrator] SileroVAD not available: {e}")

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
        initial_lang = self.caller_context.get("preferred_language", "en") if self.caller_context else "en"
        self.session = CallSession(call_id=call_id, opening_intent=opening_intent, lead_id=lead_id, preferred_language=initial_lang)
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
        self.prebuffer_threshold = 480  # 60ms jitter buffer (optimized for immediate greeting)
        self.output_buffer = bytearray()
        self._initial_greeting_complete = False
        self._timings = {}
        self._send_audio_task: Optional[asyncio.Task] = None
        self._outbound_chunk_counter: int = 1
        self._turn_start_time: Optional[float] = None

        # Persistent resampler states per stream stage
        self._inbound_resample_state: Optional[tuple] = None
        self._outbound_resample_state: Optional[tuple] = None
        
        # Track consultation booking in this session to prevent duplicate submissions
        self._consultation_booked: bool = False
        self._consultation_doctor: Optional[str] = None
        self._cancellation_save_attempted: bool = False
        
        self._is_running: bool = False
        self._stopped: bool = False
        self._assistant_turn_complete: bool = False
        self._waiting_for_user_since: Optional[float] = None
        self._silence_state: str = "active"
        self._silence_monitor_task: Optional[asyncio.Task] = None
        self._turns_since_last_directive: int = 0
        
        self.vad_buffer = bytearray()
        if SileroVAD:
            try:
                self.vad_iterator = SileroVAD(threshold=0.5, min_silence_duration_ms=500, sample_rate=16000)
            except Exception as e:
                print(f"[Orchestrator] SileroVAD initialization failed: {e}")
                self.vad_iterator = None
        else:
            self.vad_iterator = None


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
                                if 'first_telephony_audio_tx' not in getattr(self, '_timings', {}):
                                    self._timings['first_telephony_audio_tx'] = time.monotonic()
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
                                    if 'first_telephony_audio_tx' not in getattr(self, '_timings', {}):
                                        self._timings['first_telephony_audio_tx'] = time.monotonic()
                                    await self.websocket.send_text(json.dumps(media_message))
                                except Exception:
                                    pass
                        break
                    
                    # Sleep slightly and check again
                    await asyncio.sleep(0.01)
            
            # When buffer is fully drained and assistant is done speaking
            if len(self.output_buffer) == 0 and self._is_running and not self._stopped:
                if not getattr(self, "_initial_greeting_complete", True) and getattr(self, "_assistant_turn_complete", False):
                    self._initial_greeting_complete = True
                    self._timings['greeting_complete'] = time.monotonic()
                    
                    t = self._timings
                    start = t.get('call_start', 0)
                    print("\n" + "="*50)
                    print("🚀 ZERO-LATENCY GREETING TIMING REPORT")
                    print("="*50)
                    if 'call_start' in t: print(f"Call Start: 0.000s")
                    if 'gemini_connecting' in t: print(f"Gemini Connecting: {t['gemini_connecting'] - start:.3f}s")
                    if 'gemini_connected' in t: print(f"Gemini Connected: {t['gemini_connected'] - start:.3f}s")
                    if 'greeting_triggered' in t: print(f"Greeting Triggered: {t['greeting_triggered'] - start:.3f}s")
                    if 'first_gemini_audio_rx' in t: print(f"First Gemini Audio Rx: {t['first_gemini_audio_rx'] - start:.3f}s")
                    if 'first_telephony_audio_tx' in t: print(f"First Telephony Audio Tx: {t['first_telephony_audio_tx'] - start:.3f}s")
                    if 'greeting_complete' in t: print(f"Greeting Complete & Input Gate Opened: {t['greeting_complete'] - start:.3f}s")
                    print("="*50 + "\n")

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
                self._turn_start_time = None
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
            if 'first_gemini_audio_rx' not in getattr(self, '_timings', {}):
                self._timings['first_gemini_audio_rx'] = time.monotonic()

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
        if not getattr(self, "_initial_greeting_complete", True):
            print("[Orchestrator] Ignoring interruption: Initial greeting is still playing.")
            return

        # Early-turn echo guard: Ignore interruptions within first 1.8s of assistant speaking
        if self._turn_start_time and (time.time() - self._turn_start_time < 1.8):
            print(f"[Orchestrator] Ignoring early-turn echo interruption ({time.time() - self._turn_start_time:.2f}s < 1.8s)")
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
                
                # Directly evaluate the finalized transcript string
                prev_lang = self.session.preferred_language
                language_switched = self.session.update_language_if_requested(text)
                
                print(f"  [DIAGNOSTIC] Detected Language Switch: {language_switched}. Previous: {prev_lang}, New: {self.session.preferred_language}")
                
                if language_switched:
                    active_lang = self.session.preferred_language
                    lang_name = {"en": "English", "hi": "Hindi", "gu": "Gujarati"}.get(active_lang, active_lang)
                    print(f"[Orchestrator] Active caller language updated: {lang_name} ({active_lang})")
                    if self.gemini_live_client:
                        directive = (
                            f"[CRITICAL SYSTEM DIRECTIVE: The caller just spoke in {lang_name}. "
                            f"Respond in the language the caller is currently speaking. Support Gujarati, Hindi, and English. "
                            f"Follow a genuine language switch immediately; do not remain locked to the previous language.]"
                        )
                        await self.gemini_live_client.send_text(directive)
        elif event_type == "gemini":
            text = event.get("text", "").strip()
            if text:
                print(f"[Live Output Transcript] Assistant: '{text}'")
                self.session.add_transcript(text, role="assistant")
        elif event_type == "turn_complete":
            print("[Orchestrator] Live turn complete; output buffer draining to caller.")
            self._assistant_turn_complete = True
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
        from agent.tools.book_consultation import book_consultation
        from agent.tools.check_dentist import check_dentist
        from agent.tools.cancel_consultation import cancel_consultation

        def live_check_dentist(**kwargs):
            city = kwargs.get("city") or self.caller_context.get("city") or self.lead_city or ""
            return check_dentist(doctor_name=kwargs.get("doctor_name", ""), city=city)

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
            kwargs["language"] = self.session.preferred_language
            res = get_faq(**kwargs)
            if kwargs.get("topic"):
                self.session.update_topic(kwargs.get("topic"))
            return res

        def live_handoff(**kwargs):
            kwargs.setdefault("call_id", self.call_id)
            res = human_handoff(**kwargs)
            self.session.booking_stage = "handoff"
            return res

        def live_set_caller_language(**kwargs):
            lang = kwargs.get("language")
            if lang:
                self.session.set_preferred_language(lang)
                return {"status": "success", "language": lang, "message": f"Language set to {lang}"}
            return {"status": "error", "message": "No language provided"}

        def live_cancel_consultation(**kwargs):
            if not getattr(self, "_cancellation_save_attempted", False):
                # The agent tried to cancel immediately. Block it and force the prompt.
                self._cancellation_save_attempted = True
                return {
                    "status": "error",
                    "message": "SYSTEM GUARDRAIL: Do not cancel yet. First politely ask the caller why they want to cancel and if you can help resolve their issue."
                }
            
            lead_id_to_cancel = kwargs.get("lead_id") or getattr(self, "lead_id", None) or ""
            full_name = self.caller_context.get("name") or self.lead_name or ""
            name_parts = full_name.strip().split(maxsplit=1) if full_name.strip() else []
            
            first_name = self.caller_context.get("first_name") or (name_parts[0] if name_parts else "")
            last_name  = self.caller_context.get("last_name") or (name_parts[1] if len(name_parts) > 1 else ".")
            phone      = self.caller_context.get("phone", "")
            email      = self.caller_context.get("email", "")
            city       = self.caller_context.get("city", "")
            doctor     = getattr(self, "_consultation_doctor", None) or ""

            res = cancel_consultation(
                lead_id=lead_id_to_cancel, 
                reason=kwargs.get("reason", ""),
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                email=email,
                city=city,
                doctor_name=doctor
            )
            return res

        def live_book_consultation(**kwargs):
            # REMOVED the "_consultation_booked" guard here so the agent can send updates.
            
            # Pass lead_id if an earlier booking in this session already registered one.
            # Do NOT fall back to caller_context['id'] which is an internal UUID.
            kwargs["lead_id"] = getattr(self, "lead_id", None) or ""
            # CRITICAL: Always use form-submitted data for identity fields.
            full_name = self.caller_context.get("name") or self.lead_name or ""
            name_parts = full_name.strip().split(maxsplit=1) if full_name.strip() else []
            
            first_name = self.caller_context.get("first_name") or (name_parts[0] if name_parts else "")
            last_name  = self.caller_context.get("last_name") or (name_parts[1] if len(name_parts) > 1 else "")
            
            if not last_name:
                last_name = kwargs.get("last_name", "").strip() or "."
            kwargs["first_name"] = first_name
            kwargs["last_name"]  = last_name
            kwargs["phone"]      = self.caller_context.get("phone", "")
            kwargs["email"]      = self.caller_context.get("email", "")
            
            requested_city = kwargs.get("city", "").strip()
            if not requested_city:
                kwargs["city"] = self.caller_context.get("city", "")
            
            res = book_consultation(**kwargs)
            if res.get("status") == "success":
                self._consultation_booked = True
                self._consultation_doctor = (kwargs.get("doctor_name") or "").strip()
                # If the backend returned a newly generated lead_id, save it for future updates in this call
                if res.get("lead_id"):
                    self.lead_id = res.get("lead_id")
                    
            return res

        return {
            "capture_lead": live_capture_lead,
            "check_city_coverage": live_check_city,
            "get_faq": live_get_faq,
            "human_handoff": live_handoff,
            "set_caller_language": live_set_caller_language,
            "book_consultation": live_book_consultation,
            "cancel_consultation": live_cancel_consultation,
            "check_dentist": live_check_dentist,
        }

    def _build_initial_prompt(self) -> str:
        self._timings['greeting_triggered'] = time.monotonic()
        msg = (self.lead_message or "").strip()
        subj = (self.lead_subject or "").strip()
        known_name = self.lead_name if (self.lead_name and self.lead_name.lower() != "there") else ""

        city_display = self.lead_city or "their city"
        location_rule = (
            f"CRITICAL LOCATION & PARTNER RULE: The caller's consultation will be arranged with our Authorized USD Smile Designer in {city_display}. "
            f"If they ask where the meeting or consultation will take place, confidently confirm it will be with our Authorized Smile Designer in {city_display}. "
            f"NEVER invent, guess, or suggest other cities (like Hyderabad, Delhi, or Mumbai) if the user explicitly selected {city_display}."
        )
        crucial_instruction = (
            f"CRITICAL: You already know the caller's Name and City ({city_display}) from the form they just submitted. "
            "DO NOT ask them for their name or city. Acknowledge the details they provided naturally, "
            f"and move directly to the consultation guidance. {location_rule}"
        )
        if self.opening_intent == "outbound_booking_form":
            doctor = self.caller_context.get("doctor") if self.caller_context else None
            doc_info = f" with {doctor}" if doctor else ""
            crucial_instruction = (
                f"CRITICAL: You already know the caller's First Name, Last Name, City ({city_display}){doc_info} from the booking form they just submitted. "
                f"DO NOT ask them for their name, city, or doctor. "
                "STRICT RULE: The appointment is ALREADY booked. NEVER tell them to book an appointment, schedule a consultation, or fill out any booking form again! "
                f"You MUST open by confirming the appointment{doc_info} in {city_display} and asking if they have any other questions. {location_rule}"
            )
        elif self.opening_intent == "outbound_smile_preview":
            crucial_instruction = (
                f"CRITICAL: You already know the caller's Name and City ({city_display}) because they just completed the AI Smile Preview online. "
                f"DO NOT ask for their name or city again. "
                "STRICT RULE: The user ALREADY completed the AI Smile Preview. NEVER tell them to try the AI Smile Preview, upload a photo, or fill out the preview form again! "
                f"Open the call by acknowledging they saw their AI smile and ask if they want to book a consultation with our authorized designer in {city_display}. {location_rule}"
            )
        elif self.opening_intent == "outbound_contact_form":
            crucial_instruction = (
                f"CRITICAL: You already know the caller's Name and City ({city_display}) from the contact form they just submitted. "
                f"DO NOT ask them for their name or city again. "
                "STRICT RULE: The user ALREADY submitted their enquiry via the contact form. NEVER tell them to fill out the contact form or submit an enquiry again! "
                f"Directly address their message and provide expert consultation. {location_rule}"
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
                    f"Say exactly: '{greet_phrase}' Then answer the question from their message directly in 2 to 3 elegant sentences without filler (or 3-4 sentences if comparing options) using our knowledge base, and then ask: 'Do you have any other questions or any additional details you’d like to know?'. "
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
                    f"Say exactly: '{greet_phrase}' Then answer the question from their message directly in 2 to 3 elegant sentences without filler (or 3-4 sentences if comparing options with an intuitive analogy) using our knowledge base, and then ask: 'Do you have any other questions or any additional details you’d like to know?'. "
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
                f"Say exactly: '{greet_phrase}' Then address their topic directly in 2 to 3 elegant sentences without filler (or 3-4 sentences if comparing options with an intuitive analogy) based on our knowledge base, and then ask: 'Do you have any other questions or any additional details you’d like to know?'. "
                f"{crucial_instruction}"
            )
            return initial_prompt
        else:
            if known_name:
                initial_prompt = (
                    f"The outbound call has connected to {known_name}. "
                    f"Say exactly: '{self.greeting}' Then wait for their reply. "
                    f"{crucial_instruction}"
                )
            else:
                initial_prompt = (
                    f"The call has just connected. "
                    f"Say exactly: '{self.greeting}' Then wait for their reply. "
                    "You are fluent in Gujarati, Hindi, and English; after greeting, seamlessly converse in whichever language the caller uses."
                )
            return initial_prompt

    async def _manage_gemini_connection(self) -> None:
        is_reconnect = False
        while self._is_running and not self._stopped:
            try:
                if not is_reconnect:
                    self._timings['gemini_connecting'] = time.monotonic()

                self.gemini_live_client = GeminiLiveStreamClient(
                    call_id=self.call_id,
                    preferred_language="multi",
                    initial_greeting=None if is_reconnect else self.greeting,
                    initial_prompt=None if is_reconnect else self._build_initial_prompt(),
                    tool_mapping=self._build_live_tool_mapping(),
                    caller_context=self.caller_context,
                    opening_intent=self.opening_intent,
                    session=self.session
                )
                
                self._gemini_reconnect_event.clear()
                
                await self.gemini_live_client.connect(
                    audio_output_callback=self._on_live_audio_output,
                    audio_interrupt_callback=self._on_live_interruption,
                    event_callback=self._on_live_event,
                )

                if not is_reconnect:
                    self._timings['gemini_connected'] = time.monotonic()

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
                    lang_label = {"en": "English", "hi": "Hindi", "gu": "Gujarati"}.get(self.session.preferred_language, "English")
                    reconnect_prompt = (
                        f"You are continuing an ongoing phone call as Kiara with {caller_name}. "
                        "Maintain your exact calm, warm, refined, and confident tone and natural speaking pitch at all times. "
                        f"ACTIVE LANGUAGE: The caller was speaking {lang_label}. Respond in the language the caller is currently speaking. "
                        "Support Gujarati, Hindi, and English. Follow a genuine language switch immediately; do not remain locked to the previous language. "
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
        if 'call_start' not in getattr(self, '_timings', {}):
            self._timings['call_start'] = time.monotonic()

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
            
        if not getattr(self, "_initial_greeting_complete", True):
            # Hard-drop user audio while the initial greeting is playing
            return

        audio_bytes = base64.b64decode(ulaw_base64)
        
        if self.gemini_live_client and not getattr(self.gemini_live_client, "_closed", False):
            try:
                pcm8k = audioop.ulaw2lin(audio_bytes, 2)
                
                # Dynamic RMS Digital Noise Gate:
                # When assistant is speaking, filter faint carrier echo/leakage (350 RMS).
                # When listening to the caller, keep the natural acoustic spectrum intact (only filter dead silence < 80 RMS)
                # so that soft consonants, vowels, and phonemes are not mutilated before reaching Gemini STT.
                is_speaking = (
                    self.session.state == "speaking"
                    or len(self.output_buffer) > 0
                    or (self._send_audio_task is not None and not self._send_audio_task.done())
                )
                gate_threshold = 350 if is_speaking else 80
                rms = audioop.rms(pcm8k, 2)
                if rms < gate_threshold:
                    pcm8k = b"\x00" * len(pcm8k)

                pcm16k, self._inbound_resample_state = resample_pcm16(
                    pcm8k, 8000, 16000, self._inbound_resample_state
                )
                
                if getattr(self, 'vad_iterator', None):
                    self.vad_buffer.extend(pcm16k)
                    # Bounded buffer ceiling to prevent latency spiral (max 1 second)
                    if len(self.vad_buffer) > 32000:
                        print("[Orchestrator Warning] VAD buffer exceeded 32000 bytes. Truncating to newest 16000 bytes.")
                        self.vad_buffer = self.vad_buffer[-16000:]
                        
                    while len(self.vad_buffer) >= 1024:
                        chunk = self.vad_buffer[:1024]
                        del self.vad_buffer[:1024]
                        
                        audio_float32 = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                        try:
                            speech_dict = self.vad_iterator(audio_float32, return_seconds=False)
                            if speech_dict:
                                if 'start' in speech_dict:
                                    print("🗣️ [VAD] Local speech start detected.")
                                    if is_speaking:
                                        print("⚠️ [VAD] Genuine candidate barge-in detected during AI playback! Triggering local interruption.")
                                        asyncio.create_task(self._on_live_interruption())
                                if 'end' in speech_dict:
                                    print("🛑 [VAD] Local speech end detected.")
                        except Exception as e:
                            print(f"[Orchestrator Error] VAD inference failed: {e}")
                
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