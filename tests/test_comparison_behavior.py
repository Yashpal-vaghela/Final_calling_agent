import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# This test ensures that when comparisons are asked, the system instructions
# explicitly guide the LLM to give an example, but without breaking brevity on normal queries.
def test_comparison_instructions_exist():
    with open("agent/prompts/core/guardrails.md", "r", encoding="utf-8") as f:
        content = f.read()
    
    assert "Comparisons & Objections" in content
    assert "explicit comparison/difference" in content
    assert "Give one short, concrete, grounded example" in content
    assert "Do NOT force examples into unrelated questions" in content
