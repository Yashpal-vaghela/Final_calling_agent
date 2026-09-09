"""
Unit test suite for the Three-Layer Architecture refactoring:
- Layer 2: KnowledgeRetriever & JSONFaqRetriever (ranked top_k and scoring)
- Layer 3: CallSession Python-Managed Session Memory & Mutable Language Preference
- Layer 1: Prompt architecture verification (size check & dynamic fact injection format)
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from agent.knowledge import get_retriever, KnowledgeRetriever, JSONFaqRetriever
from agent.session.call_session import CallSession
from agent.tools.get_faq import get_faq


class TestThreeLayerArchitecture(unittest.TestCase):

    def test_retriever_interface(self):
        retriever = get_retriever()
        self.assertIsInstance(retriever, KnowledgeRetriever)
        self.assertIsInstance(retriever, JSONFaqRetriever)

    def test_ranked_retrieval_scoring(self):
        retriever = get_retriever()
        # Query matching warranty and veneers
        results = retriever.retrieve(query="What is your warranty policy for veneers?", top_k=2)
        self.assertTrue(len(results) > 0)
        self.assertIn("score", results[0])
        self.assertGreater(results[0]["score"], 0.0)

        # Verify ranking when top_k > 1
        if len(results) > 1:
            self.assertGreaterEqual(results[0]["score"], results[1]["score"])

    def test_multilingual_retrieval_and_get_faq(self):
        # Test Hindi FAQ retrieval via get_faq wrapper
        res_hi = get_faq("cost", language="hi")
        self.assertIn("answer", res_hi)
        self.assertTrue(len(res_hi.get("answer", "")) > 10)

        # Test Gujarati FAQ retrieval via get_faq wrapper
        res_gu = get_faq("warranty", language="gu")
        self.assertIn("answer", res_gu)
        self.assertTrue(len(res_gu.get("answer", "")) > 10)

    def test_call_session_memory_and_mutable_language(self):
        session = CallSession("test_call_001", opening_intent="general")
        self.assertEqual(session.preferred_language, "en") # default
        
        # Test mutable language update with native Devanagari script
        switched = session.update_language_if_requested("मुझे हिंदी में बात करनी है")
        self.assertTrue(switched)
        self.assertEqual(session.preferred_language, "hi")

        # Test another language change back to English
        switched = session.update_language_if_requested("Can we switch back to English now?")
        self.assertTrue(switched)
        self.assertEqual(session.preferred_language, "en")

        # Test language change to Gujarati with native script
        switched = session.update_language_if_requested("મારે ગુજરાતીમાં વાત કરવી છે")
        self.assertTrue(switched)
        self.assertEqual(session.preferred_language, "gu")

        # Switch back to English
        session.update_language_if_requested("Okay, tell me about the cost")
        self.assertEqual(session.preferred_language, "en")

        # Test session topic and lead info tracking
        session.update_topic("cost")
        session.update_user_info(name="Rajesh Kumar", city="Mumbai", intent="consultation")
        
        context_prompt = session.get_session_context_prompt()
        self.assertIn("Preferred Language: English (en)", context_prompt)
        self.assertIn("Active Topic in Discussion: cost", context_prompt)
        self.assertIn("Rajesh Kumar", context_prompt)
        self.assertIn("Mumbai", context_prompt)

    def test_system_prompt_size_reduction(self):
        prompts = []
        for p in [
            os.path.join(ROOT, "agent", "prompts", "core", "persona.md"),
            os.path.join(ROOT, "agent", "prompts", "core", "guardrails.md"),
            os.path.join(ROOT, "agent", "prompts", "intents", "inbound.md")
        ]:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    prompts.append(f.read())
        prompt_text = "\n\n".join(prompts)
        
        # Check that prompt size is significantly reduced compared to old ~52KB
        file_size_kb = len(prompt_text.encode('utf-8')) / 1024.0
        self.assertLess(file_size_kb, 35.0, f"System prompt size {file_size_kb:.2f} KB is too large! Should be well under 35 KB after stripping static facts.")
        
        # Verify core behavioral sections exist
        self.assertIn("REAL-TIME LANGUAGE MIRRORING", prompt_text)
        self.assertIn("Kiara", prompt_text)
        self.assertIn("Haresh Savani", prompt_text)
        
        # Verify static factual bulk table is gone
        self.assertNotIn("Plot No. 1 to 8, Marutidham Industrial Estate", prompt_text)
        self.assertNotIn("++91 84 69 88 88 77", prompt_text)


if __name__ == "__main__":
    unittest.main()
