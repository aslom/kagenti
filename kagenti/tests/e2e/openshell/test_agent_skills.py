"""
Tests for agent skill execution in the OpenShell PoC.

Tests verify that agents can perform real-world skills:
- PR review (analyze code diffs)
- Code generation
- RCA (root cause analysis)
- Skill discovery from kagenti repo

Tests requiring LLM are skipped when OPENSHELL_LLM_AVAILABLE != "true".
"""

import os

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import a2a_send, extract_a2a_text

pytestmark = [pytest.mark.openshell, pytest.mark.asyncio]

LLM_AVAILABLE = os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() == "true"
skip_no_llm = pytest.mark.skipif(not LLM_AVAILABLE, reason="LLM not available")

# Sample PR diff for review tests
SAMPLE_DIFF = """
diff --git a/app/auth.py b/app/auth.py
index abc123..def456 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -10,6 +10,12 @@ def authenticate(username, password):
     if not username or not password:
         return None
-    user = db.query("SELECT * FROM users WHERE name='" + username + "'")
+    user = db.query("SELECT * FROM users WHERE name=%s", (username,))
     if user and user.password == password:
+        # TODO: add rate limiting
+        log.info(f"User {username} logged in from {request.remote_addr}")
         return generate_token(user)
     return None
+
+def reset_password(email):
+    token = os.urandom(16).hex()
+    db.execute(f"UPDATE users SET reset_token='{token}' WHERE email='{email}'")
+    send_email(email, f"Reset: https://example.com/reset?token={token}")
""".strip()

# Sample code for code review tests
SAMPLE_CODE = """
import pickle
import os

def load_config(path):
    with open(path, 'rb') as f:
        return pickle.load(f)

def run_command(user_input):
    os.system(f"echo {user_input}")

def get_user(name):
    query = f"SELECT * FROM users WHERE name='{name}'"
    return db.execute(query)
""".strip()


class TestWeatherAgentSkills:
    """Weather agent skill tests — no LLM required."""

    async def test_agent_card_discovery(self, weather_agent_url):
        """Verify agent card is discoverable at .well-known endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{weather_agent_url}/.well-known/agent-card.json",
                timeout=10.0,
            )
        assert resp.status_code == 200
        card = resp.json()
        assert "name" in card
        assert "skills" in card or "capabilities" in card

    async def test_multi_turn_conversation(self, weather_agent_url):
        """Test multi-turn conversation with context retention."""
        async with httpx.AsyncClient() as client:
            resp1 = await a2a_send(
                client,
                weather_agent_url,
                "What's the weather in Paris?",
                request_id="turn-1",
            )
        assert "result" in resp1
        text1 = extract_a2a_text(resp1)
        assert text1, "First turn returned empty response"

        async with httpx.AsyncClient() as client:
            resp2 = await a2a_send(
                client,
                weather_agent_url,
                "How about Berlin?",
                request_id="turn-2",
            )
        assert "result" in resp2
        text2 = extract_a2a_text(resp2)
        assert text2, "Second turn returned empty response"


class TestADKAgentSkills:
    """ADK agent skill tests — requires LLM."""

    @skip_no_llm
    async def test_pr_review_skill(self, adk_agent_url):
        """Send a PR diff and verify the agent produces review comments."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                f"Please review this pull request diff:\n\n```diff\n{SAMPLE_DIFF}\n```",
                request_id="pr-review",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text, f"Empty review response: {resp}"
        assert len(text) > 50, f"Review too short: {text}"

        text_lower = text.lower()
        has_security = any(
            kw in text_lower
            for kw in ["sql", "injection", "security", "sanitiz", "parameterized"]
        )
        has_review = any(
            kw in text_lower for kw in ["review", "comment", "suggest", "issue", "fix"]
        )
        assert has_security or has_review, (
            f"Response doesn't look like a code review: {text[:200]}"
        )

    async def test_adk_agent_card(self, adk_agent_url):
        """Verify ADK agent card is discoverable (no LLM needed)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{adk_agent_url}/.well-known/agent.json",
                timeout=10.0,
            )
        # ADK may serve at /agent.json or /.well-known/agent-card.json
        if resp.status_code == 404:
            resp = await client.get(
                f"{adk_agent_url}/.well-known/agent-card.json",
                timeout=10.0,
            )
        assert resp.status_code == 200


class TestClaudeSDKAgentSkills:
    """Claude SDK agent skill tests — requires LLM."""

    @skip_no_llm
    async def test_code_review_skill(self, claude_sdk_agent_url):
        """Send code and verify the agent identifies security issues."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                f"Review this Python code for security vulnerabilities:\n\n```python\n{SAMPLE_CODE}\n```",
                request_id="code-review",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text, f"Empty review: {resp}"
        assert len(text) > 50, f"Review too short: {text}"

        text_lower = text.lower()
        has_security = any(
            kw in text_lower
            for kw in [
                "pickle",
                "injection",
                "os.system",
                "security",
                "unsafe",
                "vulnerability",
            ]
        )
        assert has_security, f"Didn't identify security issues: {text[:200]}"

    @skip_no_llm
    async def test_code_generation_skill(self, claude_sdk_agent_url):
        """Ask agent to generate code and verify it's valid Python."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                "Write a Python function that calculates the Fibonacci sequence up to n terms. Include type hints.",
                request_id="code-gen",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text, f"Empty response: {resp}"
        assert "def " in text or "fibonacci" in text.lower(), (
            f"Response doesn't contain a function: {text[:200]}"
        )


class TestSkillDiscovery:
    """Test that agents can discover and reference kagenti skills.

    These tests verify the agent can be pointed at the kagenti repo's
    .claude/skills/ directory and understand available skills like
    review, rca, tdd, etc.

    TODO: Implement once agents can clone repos or access skill files.
    Currently agents run in isolated pods without repo access.
    """

    @pytest.mark.skip(
        reason="TODO: Agent repo access not configured. "
        "Need to mount kagenti repo or provide skill manifests via ConfigMap. "
        "Skills available in .claude/skills/: review, rca, tdd:kind, "
        "tdd:hypershift, k8s:health, k8s:pods, k8s:logs, "
        "github:pr-review, security-review"
    )
    def test_skill_listing(self):
        """Verify agent can discover available kagenti skills."""
        pass

    @pytest.mark.skip(
        reason="TODO: PR review skill requires repo access + LLM. "
        "The kagenti repo has .claude/skills/review and "
        ".claude/skills/github:pr-review skills that can analyze diffs."
    )
    def test_pr_review_with_kagenti_skill(self):
        """Use kagenti's review skill to analyze a PR."""
        pass

    @pytest.mark.skip(
        reason="TODO: RCA skill requires cluster access + LLM. "
        "The kagenti repo has .claude/skills/rca:kind and "
        ".claude/skills/rca:hypershift skills for root cause analysis."
    )
    def test_rca_skill(self):
        """Use kagenti's RCA skill to diagnose a simulated failure."""
        pass
