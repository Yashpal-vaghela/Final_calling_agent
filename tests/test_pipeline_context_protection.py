import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from agent.pipeline import VoicePipelineOrchestrator

class TestPipelineContextProtection(unittest.TestCase):
    
    def setUp(self):
        self.mock_ws = MagicMock()
        self.orchestrator = VoicePipelineOrchestrator(self.mock_ws, call_id="test_call", stream_sid="stream_test")
        
        # Scenario caller_context: city = "Ahmedabad"
        self.orchestrator.caller_context = {
            "first_name": "AuthFirst",
            "last_name": "AuthLast",
            "phone": "9999999999",
            "email": "auth@example.com",
            "city": "Ahmedabad"
        }
        self.orchestrator.lead_name = "AuthFirst AuthLast"

    @patch("agent.tools.book_consultation.book_consultation")
    def test_scenario_a_same_city(self, mock_book_consultation):
        tool_map = self.orchestrator._build_live_tool_mapping()
        live_book_consultation = tool_map["book_consultation"]

        live_book_consultation(
            first_name="HallucinatedFirst",
            last_name="HallucinatedLast",
            phone="0000000000",
            email="hallucinated@example.com",
            city="Ahmedabad",  # LLM provides same city
            message="Valid message from LLM"
        )

        kwargs = mock_book_consultation.call_args.kwargs
        self.assertEqual(kwargs["first_name"], "AuthFirst")
        self.assertEqual(kwargs["last_name"], "AuthLast")
        self.assertEqual(kwargs["phone"], "9999999999")
        self.assertEqual(kwargs["email"], "auth@example.com")
        self.assertEqual(kwargs["city"], "Ahmedabad")

    @patch("agent.tools.book_consultation.book_consultation")
    def test_scenario_b_different_city(self, mock_book_consultation):
        tool_map = self.orchestrator._build_live_tool_mapping()
        live_book_consultation = tool_map["book_consultation"]

        live_book_consultation(
            first_name="HallucinatedFirst",
            last_name="HallucinatedLast",
            phone="0000000000",
            email="hallucinated@example.com",
            city="Surat",  # Caller explicitly requests Surat
            message="Valid message from LLM"
        )

        kwargs = mock_book_consultation.call_args.kwargs
        # Identity fields remain protected
        self.assertEqual(kwargs["first_name"], "AuthFirst")
        self.assertEqual(kwargs["last_name"], "AuthLast")
        self.assertEqual(kwargs["phone"], "9999999999")
        self.assertEqual(kwargs["email"], "auth@example.com")
        # But city is allowed to change
        self.assertEqual(kwargs["city"], "Surat")

    @patch("agent.tools.book_consultation.book_consultation")
    def test_scenario_c_no_city_provided(self, mock_book_consultation):
        tool_map = self.orchestrator._build_live_tool_mapping()
        live_book_consultation = tool_map["book_consultation"]

        live_book_consultation(
            first_name="HallucinatedFirst",
            last_name="HallucinatedLast",
            phone="0000000000",
            email="hallucinated@example.com",
            city="",  # LLM provides no city
            message="Valid message from LLM"
        )

        kwargs = mock_book_consultation.call_args.kwargs
        # Fallback to existing form city
        self.assertEqual(kwargs["city"], "Ahmedabad")
        
    @patch("agent.tools.book_consultation.book_consultation")
    def test_scenario_d_invalid_city_passed_to_validation(self, mock_book_consultation):
        tool_map = self.orchestrator._build_live_tool_mapping()
        live_book_consultation = tool_map["book_consultation"]

        live_book_consultation(
            first_name="HallucinatedFirst",
            last_name="HallucinatedLast",
            phone="0000000000",
            email="hallucinated@example.com",
            city="InvalidCity",  # Caller requests an invalid city
            message="Valid message from LLM"
        )

        kwargs = mock_book_consultation.call_args.kwargs
        # City passes through to book_consultation for validation
        self.assertEqual(kwargs["city"], "InvalidCity")

    @patch("agent.tools.book_consultation.book_consultation")
    def test_scenario_e_single_name_and_missing_last_name(self, mock_book_consultation):
        # Caller submitted contact form with single name "Keval"
        self.orchestrator.caller_context = {
            "name": "Keval",
            "phone": "+918758657212",
            "email": "kkm.tech.7@gmail.com",
            "city": "Surat"
        }
        self.orchestrator.lead_name = "Keval"

        tool_map = self.orchestrator._build_live_tool_mapping()
        live_book_consultation = tool_map["book_consultation"]

        live_book_consultation(
            message="Single name booking"
        )

        kwargs = mock_book_consultation.call_args.kwargs
        self.assertEqual(kwargs["first_name"], "Keval")
        self.assertEqual(kwargs["last_name"], ".")
        self.assertEqual(kwargs["city"], "Surat")

    @patch("agent.tools.book_consultation.book_consultation")
    def test_scenario_f_booking_update_and_lead_id_propagation(self, mock_book_consultation):
        mock_book_consultation.return_value = {"status": "success", "message": "Booked", "lead_id": "lead_999"}
        
        self.orchestrator.caller_context = {
            "first_name": "Dream",
            "last_name": "Patel",
            "phone": "+918758657212",
            "email": "kkm.tech.7@gmail.com",
            "city": "Surat"
        }
        tool_map = self.orchestrator._build_live_tool_mapping()
        live_book = tool_map["book_consultation"]

        # First booking without doctor
        res1 = live_book(doctor_name="")
        self.assertEqual(res1["status"], "success")
        self.assertEqual(mock_book_consultation.call_count, 1)
        self.assertEqual(self.orchestrator.lead_id, "lead_999")

        # Second call to update booking with Dr. Jay Patel using persisted lead_id
        res2 = live_book(doctor_name="Dr. Jay Patel")
        self.assertEqual(res2["status"], "success")
        self.assertEqual(mock_book_consultation.call_count, 2)
        second_call_kwargs = mock_book_consultation.call_args.kwargs
        self.assertEqual(second_call_kwargs["lead_id"], "lead_999")
        self.assertEqual(second_call_kwargs["doctor_name"], "Dr. Jay Patel")

if __name__ == "__main__":
    unittest.main(verbosity=2)
