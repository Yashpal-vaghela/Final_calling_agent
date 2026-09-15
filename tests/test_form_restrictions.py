"""
Unit tests for Option 1: Ephemeral Form Restrictions in Live Voice Calling.
Verifies that:
1. GeminiLiveStreamClient injects the high-priority form restriction directive into system_instruction for each outbound form intent.
2. Inbound calls do not receive the outbound form restriction directive.
3. Guardrails explicitly scope website booking CTAs to inbound calls only.
4. Outbound booking intent instructions instruct Kiara to reassure rather than redirect.
"""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from agent.streaming.gemini_live_stream import GeminiLiveStreamClient, build_system_prompt


class TestFormRestrictions(unittest.TestCase):

    def test_outbound_booking_form_restriction(self):
        client = GeminiLiveStreamClient(
            call_id="test_booking_call",
            opening_intent="outbound_booking_form"
        )
        instruction = client.system_instruction
        self.assertIn("STRICT FORM RESTRICTION - OUTBOUND BOOKING", instruction)
        self.assertIn("NEVER, under any circumstances, ask or suggest the caller fill out the booking form", instruction)
        self.assertIn("NEVER tell the caller to visit ultimatesmiledesign.com to book an appointment", instruction)

    def test_outbound_smile_preview_restriction(self):
        client = GeminiLiveStreamClient(
            call_id="test_preview_call",
            opening_intent="outbound_smile_preview"
        )
        instruction = client.system_instruction
        self.assertIn("STRICT FORM RESTRICTION - OUTBOUND SMILE PREVIEW", instruction)
        self.assertIn("NEVER ask or tell the caller to try the AI Smile Preview", instruction)

    def test_outbound_contact_form_restriction(self):
        client = GeminiLiveStreamClient(
            call_id="test_contact_call",
            opening_intent="outbound_contact_form"
        )
        instruction = client.system_instruction
        self.assertIn("STRICT FORM RESTRICTION - OUTBOUND CONTACT FORM", instruction)
        self.assertIn("NEVER tell the caller to fill out a contact form", instruction)

    def test_inbound_does_not_contain_negative_restriction(self):
        client = GeminiLiveStreamClient(
            call_id="test_inbound_call",
            opening_intent="inbound"
        )
        instruction = client.system_instruction
        self.assertNotIn("STRICT FORM RESTRICTION - OUTBOUND BOOKING", instruction)
        self.assertNotIn("STRICT FORM RESTRICTION - OUTBOUND SMILE PREVIEW", instruction)
        self.assertNotIn("STRICT FORM RESTRICTION - OUTBOUND CONTACT FORM", instruction)

    def test_guardrails_scoping(self):
        guardrails_path = os.path.join(ROOT, "agent", "prompts", "core", "guardrails.md")
        with open(guardrails_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        self.assertIn("STRICT NEGATIVE CONSTRAINT: NEVER say this to a caller who already submitted the booking form", content)
        self.assertIn("STRICT EXCEPTION: If the caller already submitted the AI Smile Preview form or Booking Form", content)

    def test_outbound_booking_next_steps_reassurance(self):
        booking_prompt_path = os.path.join(ROOT, "agent", "prompts", "intents", "outbound_booking.md")
        with open(booking_prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        self.assertIn("clinical coordinator will contact them directly with their confirmed appointment slot", content)
        self.assertIn("NEVER redirect them back to the website", content)

    def test_outbound_contact_next_steps_reassurance(self):
        contact_prompt_path = os.path.join(ROOT, "agent", "prompts", "intents", "outbound_contact.md")
        with open(contact_prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        self.assertIn("Answer them directly right here on this call using your knowledge base", content)
        self.assertIn("NEVER tell them to visit the website to submit another enquiry or contact form", content)

    def test_outbound_smile_preview_next_steps_reassurance(self):
        preview_prompt_path = os.path.join(ROOT, "agent", "prompts", "intents", "outbound_smile_preview.md")
        with open(preview_prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        self.assertIn("NEVER ask them to upload another photo or retry the online preview", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
