"""
Kagenti Skill Execution Tests

Tests that verify agents can load and execute kagenti skills.

Skill execution matrix (ALL agent types):

| Agent ID           | Skill Loading          | Execution Method      | Status     |
|--------------------|------------------------|-----------------------|------------|
| weather_agent      | N/A                    | No LLM                | N/A        |
| adk_agent          | Skill in user prompt   | LLM follows via tool  | Tested     |
| claude_sdk_agent   | Skill in system prompt | LLM follows via prompt| Tested     |
| weather_supervised | N/A                    | No LLM                | N/A        |
| openshell_claude   | Native .claude/skills/ | Claude Code reads dir | TODO       |
| openshell_opencode | Skill in prompt        | OpenCode follows      | TODO       |
| openshell_generic  | N/A                    | No agent CLI          | N/A        |
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


def _sandbox_crd_exists() -> bool:
    r = subprocess.run(
        ["kubectl", "get", "crd", "sandboxes.agents.x-k8s.io"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.returncode == 0


skip_no_crd = pytest.mark.skipif(
    not _sandbox_crd_exists(), reason="Sandbox CRD not installed"
)


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

SAMPLE_CODE = """
import pickle, os, subprocess

def load_data(path):
    return pickle.load(open(path, 'rb'))

def run(cmd):
    return subprocess.check_output(cmd, shell=True)

def query(name):
    return db.execute(f"SELECT * FROM users WHERE name='{name}'")
"""

# ═══════════════════════════════════════════════════════════════════════════
# Skill files exist (all agents share this)
# ═══════════════════════════════════════════════════════════════════════════


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

    def test_skill_directory_has_expected_structure(self):
        """Each skill directory must contain a SKILL.md file."""
        skills_dir = os.path.join(REPO_ROOT, ".claude", "skills")
        if not os.path.isdir(skills_dir):
            pytest.skip(f"Skills directory not found: {skills_dir}")

        skill_dirs = [
            d
            for d in os.listdir(skills_dir)
            if os.path.isdir(os.path.join(skills_dir, d))
        ]
        assert len(skill_dirs) >= 4, (
            f"Expected 4+ skill directories, found {len(skill_dirs)}"
        )
        for d in skill_dirs:
            skill_md = os.path.join(skills_dir, d, "SKILL.md")
            assert os.path.exists(skill_md), f"Skill {d} missing SKILL.md"


# ═══════════════════════════════════════════════════════════════════════════
# PR Review skill (parametrized across ALL agent types)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestPRReviewSkill:
    """PR review skill execution across ALL agent types."""

    @skip_no_llm
    async def test_pr_review_claude_sdk_agent(self, claude_sdk_agent_url):
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
                request_id="skill-pr-review-claude",
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
    async def test_pr_review_adk_agent(self, adk_agent_url):
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
                request_id="skill-pr-review-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    async def test_pr_review_weather_agent(self):
        """Weather agent cannot execute skills — no LLM."""
        pytest.skip(
            "weather_agent: No LLM — cannot execute PR review skill. "
            "This is by design (weather agent is a pure tool-calling agent)."
        )

    async def test_pr_review_weather_supervised(self):
        """Supervised weather agent cannot execute skills — no LLM."""
        pytest.skip(
            "weather_supervised: No LLM — cannot execute PR review skill. "
            "Supervisor provides security isolation, not LLM capabilities."
        )

    @skip_no_crd
    async def test_pr_review_openshell_claude(self):
        """Claude Code builtin sandbox executes pr-review skill natively."""
        pytest.skip(
            "openshell_claude: Native skill execution requires real Anthropic API key. "
            "Claude Code reads .claude/skills/ directly from the cloned workspace. "
            "TODO: Phase 2 provider credential integration via OpenShell gateway."
        )

    @skip_no_crd
    async def test_pr_review_openshell_opencode(self):
        """OpenCode builtin sandbox executes pr-review skill via prompt."""
        pytest.skip(
            "openshell_opencode: Skill execution requires ExecSandbox gRPC adapter. "
            "Backend would inject skill markdown into OpenCode prompt, exec in sandbox. "
            "TODO: Phase 2 ExecSandbox adapter + LiteMaaS provider on gateway."
        )

    async def test_pr_review_openshell_generic(self):
        """Generic sandbox has no agent — cannot execute skills."""
        pytest.skip(
            "openshell_generic: No agent CLI in generic sandbox. "
            "Skills require an LLM-capable agent runtime."
        )


# ═══════════════════════════════════════════════════════════════════════════
# RCA skill (parametrized across ALL agent types)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestRCASkill:
    """RCA (root cause analysis) skill execution across ALL agent types."""

    @skip_no_llm
    async def test_rca_claude_sdk_agent(self, claude_sdk_agent_url):
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
                request_id="skill-rca-claude",
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

    @skip_no_llm
    async def test_rca_adk_agent(self, adk_agent_url):
        """ADK agent follows rca:ci skill instructions."""
        skill = _read_skill("rca:ci")
        prompt = (
            f"Follow these RCA instructions:\n{skill[:800]}\n\n"
            f"Analyze these CI logs:\n```\n{SAMPLE_CI_LOG}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                prompt,
                request_id="skill-rca-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    async def test_rca_weather_agent(self):
        """Weather agent cannot execute RCA skill — no LLM."""
        pytest.skip("weather_agent: No LLM — cannot execute RCA skill.")

    async def test_rca_weather_supervised(self):
        """Supervised weather agent cannot execute RCA skill — no LLM."""
        pytest.skip("weather_supervised: No LLM — cannot execute RCA skill.")

    @skip_no_crd
    async def test_rca_openshell_claude(self):
        """Claude Code builtin sandbox executes rca:ci skill natively."""
        pytest.skip(
            "openshell_claude: Native rca:ci execution requires Anthropic API key. "
            "TODO: Phase 2 provider integration."
        )

    @skip_no_crd
    async def test_rca_openshell_opencode(self):
        """OpenCode executes rca:ci skill via prompt injection."""
        pytest.skip(
            "openshell_opencode: Requires ExecSandbox adapter + LiteMaaS. "
            "TODO: Phase 2 ExecSandbox adapter."
        )

    async def test_rca_openshell_generic(self):
        """Generic sandbox has no agent — cannot execute RCA skill."""
        pytest.skip("openshell_generic: No agent CLI — cannot execute skills.")


# ═══════════════════════════════════════════════════════════════════════════
# Security Review skill (parametrized across ALL agent types)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestSecurityReviewSkill:
    """Security review skill execution across ALL agent types."""

    @skip_no_llm
    async def test_security_review_claude_sdk_agent(self, claude_sdk_agent_url):
        """Claude SDK agent follows security review skill."""
        skill = _read_skill("test:review")
        prompt = (
            f"Execute this security review skill:\n{skill[:800]}\n\n"
            f"Review this code:\n```python\n{SAMPLE_CODE}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                prompt,
                request_id="skill-security-claude",
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

    @skip_no_llm
    async def test_security_review_adk_agent(self, adk_agent_url):
        """ADK agent follows security review skill."""
        skill = _read_skill("test:review")
        prompt = (
            f"Follow these security review instructions:\n{skill[:800]}\n\n"
            f"Review this code:\n```python\n{SAMPLE_CODE}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                prompt,
                request_id="skill-security-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    async def test_security_review_weather_agent(self):
        """Weather agent cannot execute security review — no LLM."""
        pytest.skip("weather_agent: No LLM — cannot execute security review skill.")

    async def test_security_review_weather_supervised(self):
        """Supervised weather agent cannot execute security review — no LLM."""
        pytest.skip(
            "weather_supervised: No LLM — cannot execute security review skill."
        )

    @skip_no_crd
    async def test_security_review_openshell_claude(self):
        """Claude Code builtin sandbox executes security review natively."""
        pytest.skip(
            "openshell_claude: Native security review requires Anthropic API key. "
            "TODO: Phase 2 provider integration."
        )

    @skip_no_crd
    async def test_security_review_openshell_opencode(self):
        """OpenCode executes security review via prompt."""
        pytest.skip(
            "openshell_opencode: Requires ExecSandbox adapter + LiteMaaS. "
            "TODO: Phase 2 ExecSandbox adapter."
        )

    async def test_security_review_openshell_generic(self):
        """Generic sandbox has no agent — cannot execute skills."""
        pytest.skip("openshell_generic: No agent CLI — cannot execute skills.")


# ═══════════════════════════════════════════════════════════════════════════
# Real-world skill execution (GitHub PR, CI logs)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestRealWorldSkillExecution:
    """Test skill execution with real-world data (GitHub API, CI logs)."""

    @skip_no_llm
    async def test_review_real_github_pr_claude_sdk(self, claude_sdk_agent_url):
        """Fetch a real PR diff from kagenti repo and review it."""
        gh_token = os.getenv("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3.diff"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        async with httpx.AsyncClient() as client:
            diff_resp = await client.get(
                "https://api.github.com/repos/kagenti/kagenti/pulls/1300",
                headers={**headers, "Accept": "application/vnd.github.v3.diff"},
                timeout=15.0,
            )
        if diff_resp.status_code != 200:
            pytest.skip(f"Cannot fetch PR diff: HTTP {diff_resp.status_code}")

        diff_text = diff_resp.text[:2000]
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                f"Review this pull request diff for security and code quality:\n\n"
                f"```diff\n{diff_text}\n```",
                request_id="github-pr-review",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 50

    @skip_no_llm
    async def test_rca_ci_logs_claude_sdk(self, claude_sdk_agent_url):
        """Send CI-style error logs and ask agent for root cause analysis."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                f"Analyze these CI logs and identify the root cause:\n\n"
                f"```\n{SAMPLE_CI_LOG}\n```",
                request_id="rca-logs",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text
        text_lower = text.lower()
        assert any(
            kw in text_lower
            for kw in ["secret", "webhook", "tls", "mount", "not found", "root cause"]
        ), f"Response doesn't identify root cause: {text[:200]}"

    @skip_no_llm
    async def test_review_real_github_pr_adk(self, adk_agent_url):
        """ADK agent reviews real GitHub PR."""
        gh_token = os.getenv("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3.diff"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        async with httpx.AsyncClient() as client:
            diff_resp = await client.get(
                "https://api.github.com/repos/kagenti/kagenti/pulls/1300",
                headers={**headers, "Accept": "application/vnd.github.v3.diff"},
                timeout=15.0,
            )
        if diff_resp.status_code != 200:
            pytest.skip(f"Cannot fetch PR diff: HTTP {diff_resp.status_code}")

        diff_text = diff_resp.text[:1500]
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_url,
                f"Review this PR diff:\n```diff\n{diff_text}\n```",
                request_id="github-pr-review-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    @skip_no_crd
    async def test_real_github_pr_openshell_claude(self):
        """Claude Code sandbox reviews real PR natively."""
        pytest.skip(
            "openshell_claude: Would clone repo and run `claude /review` natively. "
            "Requires Anthropic API key + workspace PVC with repo clone. "
            "TODO: Phase 2 — highest-value skill test (native .claude/skills/)."
        )

    @skip_no_crd
    async def test_real_github_pr_openshell_opencode(self):
        """OpenCode sandbox reviews real PR."""
        pytest.skip(
            "openshell_opencode: Would clone repo and review via OpenCode. "
            "Requires ExecSandbox adapter + LiteMaaS provider. "
            "TODO: Phase 2 ExecSandbox adapter."
        )
