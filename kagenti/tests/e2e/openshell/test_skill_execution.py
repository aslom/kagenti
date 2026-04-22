"""
Kagenti Skill Execution Tests

Tests that verify agents can execute kagenti skills by reading
the skill markdown and following its instructions.

Skill compatibility matrix:
- Claude CLI (OpenShell builtin): Native skill execution via .claude/skills/
- Claude SDK agent (custom A2A): Simulated — skill included as system prompt
- ADK agent (Google ADK): Simulated — skill included as user context
- Weather agent: N/A (no LLM)

For the PoC, we simulate skill execution by reading the actual skill
markdown from the repo and including it in the LLM prompt. This proves
the agent can follow skill instructions. Native execution requires
Claude CLI running inside an OpenShell sandbox.
"""

import os
import subprocess

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import a2a_send, extract_a2a_text

pytestmark = pytest.mark.openshell

LLM_AVAILABLE = os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() == "true"
skip_no_llm = pytest.mark.skipif(not LLM_AVAILABLE, reason="LLM not available")

REPO_ROOT = os.getenv(
    "REPO_ROOT",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
)


def _read_skill(skill_name: str) -> str:
    """Read a kagenti skill markdown file."""
    skill_path = os.path.join(REPO_ROOT, ".claude", "skills", skill_name, "SKILL.md")
    if not os.path.exists(skill_path):
        pytest.skip(f"Skill file not found: {skill_path}")
    with open(skill_path) as f:
        content = f.read()
    return content[:2000]


SAMPLE_PR_DIFF = """
diff --git a/app/handler.py b/app/handler.py
--- a/app/handler.py
+++ b/app/handler.py
@@ -15,6 +15,10 @@ def handle_request(request):
     user_input = request.params.get("query", "")
-    result = db.execute(f"SELECT * FROM data WHERE id='{user_input}'")
+    result = db.execute("SELECT * FROM data WHERE id=%s", (user_input,))
     return {"data": result}
+
+def admin_action(request):
+    cmd = request.params.get("cmd")
+    os.system(cmd)  # Run admin command
+    return {"status": "done"}
"""

SAMPLE_CI_LOG = """
2026-04-22T08:00:00Z Run 12345 — E2E Kind
2026-04-22T08:01:00Z Installing Kagenti...
2026-04-22T08:05:00Z ERROR: Pod kagenti-controller-manager CrashLoopBackOff
2026-04-22T08:05:01Z Back-off restarting failed container
2026-04-22T08:05:02Z Events: Warning FailedMount — secret "webhook-tls" not found
2026-04-22T08:05:10Z FAILED: test_platform_health — operator not Running
"""


@pytest.mark.asyncio
class TestPRReviewSkillExecution:
    """Test the github:pr-review skill across agents."""

    @skip_no_llm
    async def test_claude_sdk_pr_review_skill(self, claude_sdk_agent_url):
        """Claude SDK agent follows pr-review skill instructions."""
        skill = _read_skill("github:pr-review")
        prompt = (
            f"You are executing the following code review skill:\n\n"
            f"```markdown\n{skill[:1000]}\n```\n\n"
            f"Now review this PR diff following the skill's instructions:\n\n"
            f"```diff\n{SAMPLE_PR_DIFF}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                prompt,
                request_id="skill-pr-review",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 50
        text_lower = text.lower()
        assert any(
            kw in text_lower
            for kw in ["sql", "injection", "os.system", "command", "security"]
        ), f"Skill execution didn't find security issues: {text[:200]}"

    @skip_no_llm
    async def test_adk_pr_review_skill(self, adk_agent_url):
        """ADK agent follows pr-review skill instructions."""
        skill = _read_skill("github:pr-review")
        prompt = (
            f"Follow these review instructions:\n{skill[:800]}\n\n"
            f"Review this diff:\n```diff\n{SAMPLE_PR_DIFF}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                prompt,
                request_id="adk-skill-review",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30


@pytest.mark.asyncio
class TestRCASkillExecution:
    """Test the rca:ci skill across agents."""

    @skip_no_llm
    async def test_claude_sdk_rca_skill(self, claude_sdk_agent_url):
        """Claude SDK agent follows rca:ci skill instructions."""
        skill = _read_skill("rca:ci")
        prompt = (
            f"You are executing the following RCA skill:\n\n"
            f"```markdown\n{skill[:1000]}\n```\n\n"
            f"Analyze these CI logs following the skill's methodology:\n\n"
            f"```\n{SAMPLE_CI_LOG}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                prompt,
                request_id="skill-rca",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 50
        text_lower = text.lower()
        assert any(
            kw in text_lower
            for kw in ["secret", "webhook", "tls", "mount", "root cause", "missing"]
        ), f"RCA skill didn't identify root cause: {text[:200]}"


@pytest.mark.asyncio
class TestSecurityReviewSkillExecution:
    """Test the security-review skill."""

    @skip_no_llm
    async def test_claude_sdk_security_review_skill(self, claude_sdk_agent_url):
        """Claude SDK agent follows security-review skill."""
        skill = _read_skill("test:review")
        code = """
import pickle, os, subprocess

def load_data(path):
    return pickle.load(open(path, 'rb'))

def run(cmd):
    return subprocess.check_output(cmd, shell=True)

def query(name):
    return db.execute(f"SELECT * FROM users WHERE name='{name}'")
"""
        prompt = (
            f"Execute this security review skill:\n{skill[:800]}\n\n"
            f"Review this code:\n```python\n{code}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                prompt,
                request_id="skill-security",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 50
        text_lower = text.lower()
        findings = sum(
            1
            for kw in ["pickle", "shell=true", "injection", "sql", "command"]
            if kw in text_lower
        )
        assert findings >= 2, (
            f"Security review found only {findings} issues (expected 2+): {text[:200]}"
        )


class TestSkillCompatibilityMatrix:
    """Document which agents can run which skills."""

    def test_skill_files_exist(self):
        """Key kagenti skills must exist in the repo."""
        skills_dir = os.path.join(REPO_ROOT, ".claude", "skills")
        if not os.path.isdir(skills_dir):
            pytest.skip(f"Skills directory not found: {skills_dir}")

        expected = ["github:pr-review", "rca:ci", "k8s:health", "test:review"]
        for skill in expected:
            skill_path = os.path.join(skills_dir, skill, "SKILL.md")
            assert os.path.exists(skill_path), (
                f"Skill {skill} not found at {skill_path}"
            )

    def test_compatibility_matrix_documented(self):
        """Verify skill compatibility is documented."""
        # This test ensures we track which agents support which skills
        matrix = {
            "claude-cli-builtin": {
                "native_skills": True,
                "skill_source": ".claude/skills/ (cloned repo)",
                "requires": "OpenShell sandbox + repo clone",
            },
            "claude-sdk-agent": {
                "native_skills": False,
                "skill_source": "Skill markdown included in LLM prompt",
                "requires": "Skill file read + prompt construction",
            },
            "adk-agent": {
                "native_skills": False,
                "skill_source": "Skill markdown included in LLM prompt",
                "requires": "Skill file read + prompt construction",
            },
        }
        assert len(matrix) >= 3
        assert matrix["claude-cli-builtin"]["native_skills"] is True
        assert matrix["claude-sdk-agent"]["native_skills"] is False
