import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from agent.pipeline import VoicePipelineOrchestrator
from agent.session.call_session import CallSession

def test_phase_a_language_detection_does_not_steer_gemini():
    async def run_test():
        pipeline = VoicePipelineOrchestrator(websocket=AsyncMock(), call_id="test", stream_sid="test")
        pipeline.session = CallSession("test_id", preferred_language="en")
        pipeline.lid_observer = CallSession("test_id:lid", preferred_language="en")
        pipeline.gemini_live_client = AsyncMock()
        pipeline._waiting_for_user_since = None
        pipeline._silence_state = "active"
        
        await pipeline._on_live_event({"type": "gemini", "text": "Sure, I can help with that."})
        assert pipeline.session.preferred_language == "en"
        assert pipeline.lid_observer.preferred_language == "en"
        
        await pipeline._on_live_event({"type": "user", "text": "haan appointment book kardo"})
        # In Phase A, session is untouched while observer tracks detected language
        assert pipeline.session.preferred_language == "en"
        assert pipeline.lid_observer.preferred_language == "hi"
        
        # In Phase A, no text directive is sent to Gemini Live
        pipeline.gemini_live_client.send_text.assert_not_called()
    
    asyncio.run(run_test())

def test_hybrid_mode_steering_sends_directive():
    async def run_test():
        pipeline = VoicePipelineOrchestrator(websocket=AsyncMock(), call_id="test", stream_sid="test")
        pipeline.session = CallSession("test_id", preferred_language="en")
        pipeline.lid_observer = CallSession("test_id:lid", preferred_language="en")
        pipeline.gemini_live_client = AsyncMock()
        pipeline._waiting_for_user_since = None
        pipeline._silence_state = "active"
        
        with patch("agent.pipeline.ENABLE_PYTHON_LANGUAGE_STEERING", True):
            await pipeline._on_live_event({"type": "user", "text": "haan appointment book kardo"})
            assert pipeline.session.preferred_language == "hi"
            pipeline.gemini_live_client.send_text.assert_called_once()
            call_arg = pipeline.gemini_live_client.send_text.call_args[0][0]
            assert "The caller's current spoken language is Hindi" in call_arg
    
    asyncio.run(run_test())

def test_direct_evaluation_no_duplication():
    async def run_test():
        pipeline = VoicePipelineOrchestrator(websocket=AsyncMock(), call_id="test", stream_sid="test")
        pipeline.session = CallSession("test_id", preferred_language="en")
        pipeline.gemini_live_client = AsyncMock()
        pipeline._waiting_for_user_since = None
        pipeline._silence_state = "active"
        
        await pipeline._on_live_event({"type": "user", "text": "I need"})
        assert pipeline.session.preferred_language == "en"
        
        await pipeline._on_live_event({"type": "user", "text": "veneers"})
        assert pipeline.session.preferred_language == "en"
    
    asyncio.run(run_test())
