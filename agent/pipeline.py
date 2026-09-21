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
import logging

logger = logging.getLogger(__name__)

import numpy as np
try:
    from agent.audio.silero import SileroVAD
except Exception as e:
    SileroVAD = None
    print(f"[Orchestrator] SileroVAD not available: {e}")

from agent.streaming.gemini_live_stream import GeminiLiveStreamClient
from agent.session.call_session import CallSession
from agent.audio.codecs import resample_pcm16

ENABLE_LOCAL_VAD = False
ENABLE_NOISE_GATE = False


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
        
        # Seed known caller information from trusted context into session immediately
        self.session.update_user_info(
            name=self.lead_name or self.caller_context.get("name"),
            phone=self.lead_phone or self.caller_context.get("phone"),
            email=self.lead_email or self.caller_context.get("email"),
            city=self.lead_city or self.caller_context.get("city"),
            subject=self.lead_subject or self.caller_context.get("subject"),
            intent=self.lead_subject or self.caller_context.get("subject") or opening_intent,
            notes=self.lead_message or self.caller_context.get("message") or self.caller_context.get("notes")
        )
        self._pending_phone: Optional[str] = None
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
        
        # Session resumption and safe rotation state
        self._current_resumption_handle: Optional[str] = None
        self._gemini_rotation_pending: bool = False
        self._gemini_rotation_reason: Optional[str] = None
        self._gemini_reconnect_event: asyncio.Event = asyncio.Event()
        self._gemini_reconnecting: bool = False
        
        self.vad_buffer = bytearray()
        if SileroVAD:
            try:
                self.vad_iterator = SileroVAD(threshold=0.5, min_silence_duration_ms=500, sample_rate=16000)
            except Exception as e:
                print(f"[Orchestrator] SileroVAD initialization failed: {e}")
                self.vad_iterator = None
        else:
            self.vad_iterator = None

    def update_canonical_identity(
        self,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        city: Optional[str] = None,
        email: Optional[str] = None
    ) -> None:
        """Maintains strict synchronization of canonical identity across all layers."""
        if name:
            clean_name = name.strip()
            self.lead_name = clean_name
            self.caller_context["name"] = clean_name
            parts = clean_name.split(maxsplit=1)
            self.caller_context["first_name"] = parts[0]
            self.caller_context["last_name"] = parts[1] if len(parts) > 1 else "."
            if hasattr(self, "session") and self.session:
                self.session.update_user_info(name=clean_name)
        if phone:
            clean_phone = phone.strip()
            self.lead_phone = clean_phone
            self.caller_context["phone"] = clean_phone
            if hasattr(self, "session") and self.session:
                self.session.update_user_info(phone=clean_phone)
        if city:
            clean_city = city.strip()
            self.lead_city = clean_city
            self.caller_context["city"] = clean_city
            if hasattr(self, "session") and self.session:
                self.session.update_user_info(city=clean_city)
        if email:
            clean_email = email.strip()
            self.lead_email = clean_email
            self.caller_context["email"] = clean_email
            if hasattr(self, "session") and self.session:
                self.session.update_user_info(email=clean_email)

    async def _silence_monitor(self) -> None:
        """Hybrid background task to monitor for user silence. Acts as a safety net if no VAD/events trigger for 15s."""
        while self._is_running and not self._stopped:
            if (
                getattr(self, "_gemini_reconnecting", False)
                or self.gemini_live_client is None
                or getattr(self.gemini_live_client, "_closed", False)
                or not getattr(self.gemini_live_client, "_is_connected", False)
            ):
                await asyncio.sleep(0.25)
                continue

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
                    
                    print(f"[Silence Monitor] 15s silence; asking Gemini to continue in caller's current conversational language")
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

        print("[Gemini Interruption] Server interruption confirmed; clearing queued Smartflo audio")
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
        elif event_type == "gemini":
            text = event.get("text", "").strip()
            if text:
                print(f"[Live Output Transcript] Assistant: '{text}'")
                self.session.add_transcript(text, role="assistant")
        elif event_type == "turn_complete":
            print("[Orchestrator] Live turn complete; output buffer draining to caller.")
            self._assistant_turn_complete = True
        elif event_type == "session_resumption_update":
            handle = event.get("handle")
            resumable = event.get("resumable", False)
            if resumable and handle:
                self._current_resumption_handle = handle
                logger.debug("[Gemini Resumption] Saved resumable handle: handle_available=True")
        elif event_type == "go_away":
            time_left = event.get("time_left")
            print(f"[Gemini Rotation] Received go_away event: time_left={time_left}")
            self._gemini_rotation_pending = True
            self._gemini_rotation_reason = f"go_away(time_left={time_left})"
            if hasattr(self, "_gemini_reconnect_event"):
                self._gemini_reconnect_event.set()
        elif event_type == "error":
            err_str = str(event.get('error', ''))
            print(f"[Orchestrator Error] Gemini Live event error: {err_str}")
            if hasattr(self, "_gemini_reconnect_event") and ("1008" in err_str or "GoAway" in err_str or "Connection aborted" in err_str or "closed" in err_str.lower()):
                self._gemini_rotation_pending = True
                self._gemini_rotation_reason = f"error({err_str[:40]})"
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
            # Phase A: don't inject preferred_language — let get_faq use default
            # and instruction is now language-neutral (Gemini decides from caller voice)
            res = get_faq(**kwargs)
            if kwargs.get("topic"):
                self.session.update_topic(kwargs.get("topic"))
            return res

        def live_handoff(**kwargs):
            kwargs.setdefault("call_id", self.call_id)
            res = human_handoff(**kwargs)
            self.session.booking_stage = "handoff"
            return res

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

            # --- SAME-CALL RE-BOOKING & UN-CANCEL ---
            # After cancellation, keep self.lead_id so that if the caller decides to
            # re-book in the same call, book_consultation updates and un-cancels (is_cancel: False)
            # that existing appointment instead of creating an orphaned duplicate record.
            if res.get("status") == "success":
                self._consultation_booked = False
                self._cancellation_save_attempted = False  # allow cancel guard to reset for safety
            return res

        def live_update_caller_profile(**kwargs):
            from agent.tools.caller_profile import update_caller_profile
            lead_id_val = getattr(self, "lead_id", None) or ""
            kwargs.setdefault("lead_id", lead_id_val)
            if kwargs.get("confirm_phone") and not kwargs.get("phone") and getattr(self, "_pending_phone", None):
                kwargs["phone"] = self._pending_phone

            if not kwargs.get("name"):
                name_val = self.caller_context.get("name") or self.lead_name or ""
                if name_val:
                    kwargs["name"] = name_val
            if not kwargs.get("city"):
                city_val = self.caller_context.get("city") or ""
                if city_val:
                    kwargs["city"] = city_val
            if not kwargs.get("email"):
                email_val = self.caller_context.get("email") or ""
                if email_val:
                    kwargs["email"] = email_val

            res = update_caller_profile(**kwargs)
            if res.get("status") == "confirmation_required":
                self._pending_phone = res.get("phone")
            elif res.get("status") == "success":
                self._pending_phone = None
                new_phone = res.get("phone")
                new_name = res.get("name")
                old_phone = self.lead_phone or self.caller_context.get("phone")

                if new_name:
                    self.update_canonical_identity(name=new_name)
                if new_phone:
                    self.update_canonical_identity(phone=new_phone)
                    from backend.app.services.caller_context import reindex_caller_phone
                    reindex_caller_phone(old_phone=old_phone, new_phone=new_phone, context_data=self.caller_context)
            return res

        def live_book_consultation(**kwargs):
            # REMOVED the "_consultation_booked" guard here so the agent can send updates.
            
            # Pass lead_id if an earlier booking in this session already registered one.
            # Do NOT fall back to caller_context['id'] which is an internal UUID.
            kwargs["lead_id"] = getattr(self, "lead_id", None) or ""
            # CRITICAL: Always use canonical form-submitted or confirmed data for identity fields.
            full_name = self.caller_context.get("name") or self.lead_name or (self.session.collected_user_info.get("name") if hasattr(self, "session") else "") or ""
            name_parts = full_name.strip().split(maxsplit=1) if full_name.strip() else []
            
            first_name = self.caller_context.get("first_name") or (name_parts[0] if name_parts else (kwargs.get("first_name", "").strip() or ""))
            last_name  = self.caller_context.get("last_name") or (name_parts[1] if len(name_parts) > 1 else (kwargs.get("last_name", "").strip() or ""))
            
            if not last_name:
                last_name = kwargs.get("last_name", "").strip() or "."
            kwargs["first_name"] = first_name
            kwargs["last_name"]  = last_name

            canonical_phone = self.caller_context.get("phone") or self.lead_phone or (self.session.collected_user_info.get("phone") if hasattr(self, "session") else "") or kwargs.get("phone", "")
            kwargs["phone"]      = canonical_phone
            kwargs["email"]      = self.caller_context.get("email") or self.lead_email or (self.session.collected_user_info.get("email") if hasattr(self, "session") else "") or kwargs.get("email", "")
            
            requested_city = kwargs.get("city", "").strip()
            if not requested_city:
                kwargs["city"] = self.caller_context.get("city", "")
            
            res = book_consultation(**kwargs)
            if res.get("status") == "success":
                self._consultation_booked = True
                self._consultation_doctor = (kwargs.get("doctor_name") or "").strip()
                if requested_city:
                    self.update_canonical_identity(city=requested_city)
                # If the backend returned a newly generated lead_id, save it for future updates in this call
                if res.get("lead_id"):
                    self.lead_id = res.get("lead_id")
                    
            return res

        return {
            "capture_lead": live_capture_lead,
            "check_city_coverage": live_check_city,
            "get_faq": live_get_faq,
            "human_handoff": live_handoff,
            "book_consultation": live_book_consultation,
            "cancel_consultation": live_cancel_consultation,
            "check_dentist": live_check_dentist,
            "update_caller_profile": live_update_caller_profile,
            "update_contact_details": live_update_caller_profile,
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
                f"You also have their initial enquiry message: '{msg}' regarding '{subj}'. "
                f"DO NOT ask them for their name or city again. "
                "STRICT RULE: The user ALREADY submitted their enquiry via the contact form. NEVER tell them to fill out the contact form or submit an enquiry again! "
                f"Directly address their message and provide expert consultation. If they want to book an appointment, use their initial enquiry message as the reason/message for the booking and verify it with them. {location_rule}"
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
                    f"If the caller decides to book an appointment later in the call, use their initial message '{msg}' as the booking reason and verify it with them. "
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

    def _is_safe_to_rotate_gemini(self) -> bool:
        """
        Determines whether it is safe to rotate the Gemini Live WebSocket.
        A safe boundary means:
        - Call is still running
        - Session is NOT currently speaking
        - Telephony output_buffer is empty
        - _send_audio_task is None or done
        - Assistant turn is complete / no audio actively being generated
        """
        if not self._is_running or self._stopped:
            return True
        is_speaking = (self.session.state == "speaking")
        buffer_has_audio = (len(self.output_buffer) > 0)
        send_task_active = (self._send_audio_task is not None and not self._send_audio_task.done())
        model_generating = not getattr(self, "_assistant_turn_complete", True) and (is_speaking or buffer_has_audio)
        
        return not (is_speaking or buffer_has_audio or send_task_active or model_generating)

    async def _wait_for_safe_rotation(self, max_wait_seconds: float = 12.0) -> None:
        """
        Waits for in-progress assistant speech and outbound audio buffer to drain before rotating Gemini.
        Prevents cutting Kiara mid-sentence while honoring lifetime limits.
        """
        print("[Gemini Rotation] Waiting for safe turn boundary")
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < max_wait_seconds:
            if not self._is_running or self._stopped:
                break
            if self._is_safe_to_rotate_gemini():
                break
            await asyncio.sleep(0.1)
        print("[Gemini Rotation] Output drained; rotating Gemini connection")

    async def _manage_gemini_connection(self) -> None:
        is_reconnect = False
        while self._is_running and not self._stopped:
            try:
                if not is_reconnect:
                    self._timings['gemini_connecting'] = time.monotonic()

                # Determine resumption handle and initial prompt for this connection
                resumption_handle = self._current_resumption_handle if is_reconnect else None
                fallback_prompt = None
                fallback_turn_complete = None

                if is_reconnect:
                    if resumption_handle:
                        print(f"[Gemini Resumption] Reconnecting with saved session handle: handle_available=True")
                    else:
                        print("[Gemini Resumption Warning] No resumption handle available; using silent conversation prefill fallback.")
                        # Build silent context prefill for fallback (turn_complete=False)
                        full_history = self.session.conversation_history if self.session.conversation_history else []
                        history_lines = []
                        for t in full_history:
                            role = "USER" if t.get('role') == "user" else "KIARA (YOU)"
                            text = (t.get('content') or t.get('text') or '').strip()
                            if text:
                                history_lines.append(f"{role}: {text}")
                        history_text = "\n".join(history_lines) if history_lines else "No previous turns recorded."
                        caller_name = self.lead_name or "the caller"
                        fallback_prompt = (
                            f"[CALL CONTINUATION FALLBACK CONTEXT]\n\n"
                            f"This is a continuation of the same phone call.\n\n"
                            f"Retain the caller identity, booking state and recent conversation context below.\n\n"
                            f"LANGUAGE RULE:\n"
                            f"Do not assume English, Hindi or Gujarati from metadata.\n"
                            f"Do not speak yet.\n"
                            f"Wait silently for the caller's next actual spoken audio.\n"
                            f"On their next clear spoken turn, respond in the language of that CURRENT spoken turn.\n"
                            f"If the next turn is only a short or ambiguous acknowledgment, infer the conversational language from the recent dialogue context.\n\n"
                            f"Caller Name: {caller_name}\n"
                            f"Current Booking Stage: {self.session.booking_stage}\n"
                            f"Session State: {self.session.get_session_context_prompt()}\n"
                            f"Recent conversation context:\n{history_text}\n\n"
                            f"[INSTRUCTION] Retain this background context silently. Do NOT speak. Do NOT generate any output. Wait silently for the caller's next spoken audio turn."
                        )
                        fallback_turn_complete = False

                self.gemini_live_client = GeminiLiveStreamClient(
                    call_id=self.call_id,
                    preferred_language="multi",
                    initial_greeting=None if is_reconnect else self.greeting,
                    initial_prompt=fallback_prompt if (is_reconnect and not resumption_handle) else (None if is_reconnect else self._build_initial_prompt()),
                    turn_complete_on_start=fallback_turn_complete,
                    tool_mapping=self._build_live_tool_mapping(),
                    caller_context=self.caller_context,
                    opening_intent=self.opening_intent,
                    session=self.session,
                    resumption_handle=resumption_handle
                )
                
                self._gemini_reconnect_event.clear()
                self._gemini_rotation_pending = False
                self._gemini_rotation_reason = None
                
                await self.gemini_live_client.connect(
                    audio_output_callback=self._on_live_audio_output,
                    audio_interrupt_callback=self._on_live_interruption,
                    event_callback=self._on_live_event,
                )

                wait_fn = getattr(self.gemini_live_client, "wait_until_ready", None)
                if callable(wait_fn):
                    try:
                        res = wait_fn(timeout=10.0)
                        if inspect.isawaitable(res):
                            await res
                    except Exception as e:
                        print(f"[Gemini Live Stream Warning] wait_until_ready timed out or error: {e}")

                if not is_reconnect:
                    self._timings['gemini_connected'] = time.monotonic()
                else:
                    if resumption_handle:
                        print("[Gemini Resumption] Session resumed successfully")
                    self._gemini_reconnecting = False
                    self._silence_state = "stage_1_waiting"
                    self._waiting_for_user_since = time.time()
                
                try:
                    await asyncio.wait_for(self._gemini_reconnect_event.wait(), timeout=540.0)
                    reason = self._gemini_rotation_reason or "server event / go_away"
                    print(f"[Gemini Rotation] Rotation requested (reason: {reason}).")
                except asyncio.TimeoutError:
                    print("[Gemini Rotation] Rotation requested (reason: 9-minute proactive reconnect timer expired).")
                
                if self._is_running and not self._stopped:
                    self._gemini_reconnecting = True
                    # Kill the old silence countdown.
                    self._waiting_for_user_since = None
                    self._silence_state = "active"

                    # Safe turn boundary wait: ensure assistant is not speaking and buffer is drained
                    await self._wait_for_safe_rotation(max_wait_seconds=12.0)

                    old_client = self.gemini_live_client
                    if old_client and old_client.latest_valid_resumption_handle:
                        self._current_resumption_handle = old_client.latest_valid_resumption_handle
                        print(f"[Gemini Resumption] Saved resumable handle from active client: handle_available=True")

                    self.gemini_live_client = None
                    if old_client:
                        await old_client.finish()
                    is_reconnect = True
                    print("[Orchestrator] Spawning new Gemini Live session for seamless continuation...")
                    
            except Exception as e:
                self._gemini_reconnecting = False
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
        self._gemini_reconnect_event.clear()
        
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
                
                # NOISE GATE — only active when ENABLE_NOISE_GATE is True
                if ENABLE_NOISE_GATE:
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
                
                # SILERO VAD — only active when ENABLE_LOCAL_VAD is True
                if ENABLE_LOCAL_VAD and getattr(self, 'vad_iterator', None):
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
                                    if self.session.state == "speaking" or len(self.output_buffer) > 0 or (self._send_audio_task is not None and not self._send_audio_task.done()):
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