"""
Claude SDK Agent E2E Tests (OpenShell PoC)

Tests the Anthropic SDK-based code review agent deployed via OpenShell
manifests.  This agent requires an LLM backend (Budget Proxy -> LiteLLM)
so tests are skipped when OPENSHELL_LLM_AVAILABLE is not "true".

Usage:
    OPENSHELL_LLM_AVAILABLE=true pytest kagenti/tests/e2e/openshell/test_claude_sdk_agent.py -v -m openshell
"""

import pytest

from kagenti.tests.e2e.openshell.conftest import a2a_send, extract_a2a_text


pytestmark = [pytest.mark.openshell, pytest.mark.asyncio]

PYTHON_SNIPPET = """\
import pickle
import os

def load_config(path):
    with open(path, 'rb') as f:
        return pickle.load(f)

def delete_files(directory):
    for f in os.listdir(directory):
        os.remove(os.path.join(directory, f))
"""


class TestClaudeSdkAgentA2A:
    """Test the Claude SDK agent via A2A message/send."""

    async def test_hello(self, claude_sdk_agent_url, llm_available):
        """Send a simple greeting and verify a non-empty response."""
        if not llm_available:
            pytest.skip("LLM backend not available (set OPENSHELL_LLM_AVAILABLE=true)")

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                "Hello, who are you?",
            )

        assert "error" not in resp, f"A2A returned error: {resp.get('error')}"
        assert "result" in resp, f"A2A response missing 'result': {resp}"

        text = extract_a2a_text(resp)
        assert text, f"Empty response from Claude SDK agent. Full response: {resp}"
        assert len(text) > 5, f"Response too short: {text}"

    async def test_code_review(self, claude_sdk_agent_url, llm_available):
        """Send a Python code snippet for review and verify feedback."""
        if not llm_available:
            pytest.skip("LLM backend not available (set OPENSHELL_LLM_AVAILABLE=true)")

        import httpx

        prompt = (
            "Please review this Python code for security issues and "
            "suggest improvements:\n\n"
            f"```python\n{PYTHON_SNIPPET}\n```"
        )

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                prompt,
                request_id="test-code-review",
                timeout=180.0,
            )

        assert "error" not in resp, f"A2A returned error: {resp.get('error')}"

        text = extract_a2a_text(resp)
        assert text, f"Empty response from Claude SDK agent. Full response: {resp}"

        # The review should mention security-relevant concepts
        review_keywords = [
            "pickle",
            "security",
            "unsafe",
            "deserialization",
            "vulnerability",
            "risk",
            "review",
            "suggest",
            "improve",
            "code",
            "delete",
            "dangerous",
        ]
        text_lower = text.lower()
        has_review_content = any(kw in text_lower for kw in review_keywords)
        assert has_review_content, (
            f"Response does not look like a code review. Response: {text[:500]}"
        )
