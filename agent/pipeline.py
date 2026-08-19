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
from typing import Optional

from agent.streaming.gemini_live_stream import GeminiLiveStreamClient
from agent.session.call_session import CallSession


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

        # Determine greeting based on call intent
        if self.opening_intent in ("follow-up", "outbound_contact_form"):
            if self.lead_name and self.lead_name.lower() != "there":
                city_str = f" {self.lead_city}" if self.lead_city else ""
                self.greeting = (
                    f"Hi {self.lead_name}, this is Kiara from Ultimate Smile Design, "
                    f"following up on your{city_str} smile consultation enquiry. How can I help you today?"
                )
            else:
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

        live_tool_mapping = {
            "capture_lead": live_capture_lead,
            "check_city_coverage": live_check_city,
            "get_faq": live_get_faq,
            "human_handoff": live_handoff,
        }

        self.gemini_live_client = GeminiLiveStreamClient(
            call_id=call_id,
            preferred_language="multi",
            initial_greeting=self.greeting,
            tool_mapping=live_tool_mapping,
            caller_context=self.caller_context
        )
        self.tts_provider_name = "Gemini Live"
        self.mulaw_frame_size = 160
        self.output_buffer = bytearray()
        self._send_audio_task: Optional[asyncio.Task] = None

        # Persistent resampler states per stream stage
        self._inbound_resample_state: Optional[tuple] = None
        self._outbound_resample_state_24_to_16: Optional[tuple] = None
        self._outbound_resample_state_16_to_8: Optional[tuple] = None
        
        self._is_running: bool = False
        self._stopped: bool = False
        self._waiting_for_user_since: Optional[float] = None
        self._silence_state: str = "active"
        self._silence_monitor_task: Optional[asyncio.Task] = None


    async def _silence_monitor(self) -> None:
        """Background task to monitor for user silence: prompts after 15s, disconnects after an additional 25s."""
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
                    print(f"[Orchestrator] User silent for {elapsed:.1f}s. Sending silence check: 'Hello, are you still there?'")
                    self._silence_state = "stage_1_prompting"
                    self._waiting_for_user_since = None
                    if getattr(self, "gemini_live_client", None):
                        await self.gemini_live_client.send_text("The user has been silent. Say exactly: 'Hello, are you still there?' and wait for their reply.")

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
        """Send buffered audio to WebSocket in consistent 160-byte (20ms) mulaw frames with wall-clock pacing.

        Uses time.monotonic() to compute the exact remaining sleep after each WebSocket send,
        so accumulated loop and I/O overhead does not stretch speech playback speed.
        """
        FRAME_DURATION = 0.02  # 20 ms per 160-byte 8 kHz μ-law frame
        try:
            next_send_at = time.monotonic()
            while len(self.output_buffer) >= getattr(self, "mulaw_frame_size", 160) and self._is_running and not self._stopped:
                frame = bytes(self.output_buffer[:self.mulaw_frame_size])
                del self.output_buffer[:self.mulaw_frame_size]
                payload = base64.b64encode(frame).decode("utf-8")
                media_message = {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": payload},
                }
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
            
            # When buffer is fully drained and assistant is done speaking
            if len(self.output_buffer) == 0 and self._is_running and not self._stopped:
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
            intermediate, self._outbound_resample_state_24_to_16 = audioop.ratecv(
                data, 2, 1, 24000, 16000, self._outbound_resample_state_24_to_16
            )
            resampled_data, self._outbound_resample_state_16_to_8 = audioop.ratecv(
                intermediate, 2, 1, 16000, 8000, self._outbound_resample_state_16_to_8
            )
            mulaw_data = audioop.lin2ulaw(resampled_data, 2)
            self.output_buffer.extend(mulaw_data)
            self.session.transition_state("speaking")
            if getattr(self, "_silence_state", "") != "stage_1_prompting":
                self._waiting_for_user_since = None
            if self._send_audio_task is None or self._send_audio_task.done():
                self._send_audio_task = asyncio.create_task(self._send_buffered_live_audio())
        except Exception as e:
            print(f"[Orchestrator Error] Error sending live audio to WebSocket: {e}")

    async def _on_live_interruption(self) -> None:
        """Callback invoked when Gemini Live detects caller interruption (barge-in)."""
        if self._stopped:
            return
        print("[Orchestrator] Gemini Live server interruption received! Clearing output buffer and media stream.")
        if getattr(self, "_send_audio_task", None) and not self._send_audio_task.done():
            self._send_audio_task.cancel()
        self.output_buffer.clear()
        self._outbound_resample_state_24_to_16 = None
        self._outbound_resample_state_16_to_8 = None
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
                self.session.update_language_if_requested(text)
        elif event_type == "gemini":
            text = event.get("text", "").strip()
            if text:
                print(f"[Live Output Transcript] Assistant: '{text}'")
                self.session.add_transcript(text, role="assistant")
        elif event_type == "turn_complete":
            print("[Orchestrator] Live turn complete; output buffer draining to caller.")
        elif event_type == "error":
            print(f"[Orchestrator Error] Gemini Live event error: {event.get('error')}")

    async def start(self) -> None:
        """Starts the voice pipeline connections and initiates the opening greeting."""
        self._is_running = True
        self._waiting_for_user_since = None
        self._silence_state = "active"
        self._silence_monitor_task = asyncio.create_task(self._silence_monitor())
        print(f"[Orchestrator] Starting voice pipeline for CallSid: {self.call_id}, StreamSid: {self.stream_sid}")
        
        self.session.add_transcript(self.greeting, role="assistant")
        self.session.transition_state("listening")
        try:
            if self.gemini_live_client:
                await self.gemini_live_client.connect(
                    audio_output_callback=self._on_live_audio_output,
                    audio_interrupt_callback=self._on_live_interruption,
                    event_callback=self._on_live_event,
                )

                # If message and/or subject are provided, answer the question from the message first
                msg = (self.lead_message or "").strip()
                subj = (self.lead_subject or "").strip()
                name_str = f"to {self.lead_name}" if self.lead_name and self.lead_name.lower() != "there" else ""

                if msg:
                    subj_str = f" regarding '{subj}'" if subj else ""
                    initial_prompt = (
                        f"The outbound call has just connected {name_str}. "
                        f"The caller submitted a contact form enquiry{subj_str} with the message / question: '{msg}'. "
                        f"Start the call with your warm introduction as Kiara from Ultimate Smile Design, answer their question/message directly and clearly based on our knowledge base, "
                        f"and then ask: 'Do you have any other questions or any additional details you’d like to know?'"
                    )
                    await self.gemini_live_client.send_text(initial_prompt)
                elif subj:
                    initial_prompt = (
                        f"The outbound call has just connected {name_str}. "
                        f"The caller submitted a contact form enquiry on the topic: '{subj}'. "
                        f"Start the call with your warm introduction as Kiara from Ultimate Smile Design, address their topic clearly based on our knowledge base, "
                        f"and then ask: 'Do you have any other questions or any additional details you’d like to know?'"
                    )
                    await self.gemini_live_client.send_text(initial_prompt)
                else:
                    await self.gemini_live_client.send_text(f"Greet the caller by saying exactly: '{self.greeting}' and wait for their reply.")
        except Exception as e:
            print(f"[Orchestrator] Failed to open Gemini Live connection: {e}")
            await self.stop()
            try:
                await self.websocket.close()
            except Exception:
                pass

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
                pcm16k, self._inbound_resample_state = audioop.ratecv(
                    pcm8k, 2, 1, 8000, 16000, self._inbound_resample_state
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
        self._inbound_resample_state = None
        self._outbound_resample_state_24_to_16 = None
        self._outbound_resample_state_16_to_8 = None
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