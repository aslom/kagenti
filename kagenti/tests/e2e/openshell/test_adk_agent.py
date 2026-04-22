"""
ADK Agent E2E Tests (OpenShell PoC)

Tests the Google ADK-based PR review agent deployed via OpenShell manifests.
This agent requires an LLM backend (Budget Proxy -> LiteLLM) so tests are
skipped when OPENSHELL_LLM_AVAILABLE is not "true".

Usage:
    OPENSHELL_LLM_AVAILABLE=true pytest kagenti/tests/e2e/openshell/test_adk_agent.py -v -m openshell
"""

import pytest

from kagenti.tests.e2e.openshell.conftest import a2a_send, extract_a2a_text


pytestmark = [pytest.mark.openshell, pytest.mark.asyncio]

SMALL_DIFF = """\
diff --git a/main.py b/main.py
index 1a2b3c4..5d6e7f8 100644
--- a/main.py
+++ b/main.py
@@ -1,5 +1,7 @@
 import os
+import subprocess

 def run_command(cmd):
-    os.system(cmd)
+    result = subprocess.run(cmd, shell=True, capture_output=True)
+    return result.stdout.decode()
"""


class TestAdkAgentA2A:
    """Test the ADK agent via A2A message/send."""

    async def test_hello(self, adk_agent_url):
        """Send a simple greeting — tests A2A connectivity (LLM response optional)."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                "Hello, who are you?",
            )

        # Agent should respond via A2A even if LLM is unavailable
        assert "result" in resp, f"A2A response missing 'result': {resp}"

    async def test_pr_review(self, adk_agent_url, llm_available):
        """Send a small diff for PR review and verify review feedback."""
        if not llm_available:
            pytest.skip("LLM backend not available (set OPENSHELL_LLM_AVAILABLE=true)")

        import httpx

        prompt = (
            "Please review this pull request diff and provide feedback:\n\n"
            f"```diff\n{SMALL_DIFF}\n```"
        )

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                prompt,
                request_id="test-pr-review",
                timeout=180.0,
            )

        assert "error" not in resp, f"A2A returned error: {resp.get('error')}"

        text = extract_a2a_text(resp)
        assert text, f"Empty response from ADK agent. Full response: {resp}"

        # The review should contain at least some code-review-related terms
        review_keywords = [
            "subprocess",
            "shell",
            "security",
            "injection",
            "review",
            "change",
            "command",
            "improvement",
            "suggest",
            "code",
        ]
        text_lower = text.lower()
        has_review_content = any(kw in text_lower for kw in review_keywords)
        assert has_review_content, (
            f"Response does not look like a code review. Response: {text[:500]}"
        )
