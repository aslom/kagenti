"""
T3.1 Skill Execution Tests

Tests skill execution across all agents and models.

Capability: skill_pr_review, skill_rca, skill_security, skill_github_pr
Convention: test_skill_{type}__{description}[agent]
"""

import os

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import (
    a2a_send,
    extract_a2a_text,
    litellm_chat,
    litellm_chat_text,
    openclaw_chat,
    record_llm_metric,
    sandbox_crd_installed,
    CANONICAL_DIFF,
    CANONICAL_CODE,
    CANONICAL_CI_LOG,
    LLM_MODELS,
    _read_skill,
)

pytestmark = pytest.mark.openshell

LLM_AVAILABLE = os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() == "true"
skip_no_llm = pytest.mark.skipif(not LLM_AVAILABLE, reason="LLM not available")
skip_no_crd = pytest.mark.skipif(
    not sandbox_crd_installed(), reason="Sandbox CRD not installed"
)

REPO_ROOT = os.getenv(
    "REPO_ROOT",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
)

SKILL_MODELS = LLM_MODELS if LLM_MODELS else ["default"]


# ═══════════════════════════════════════════════════════════════════════════
# Skill infrastructure (all agents share this)
# ═══════════════════════════════════════════════════════════════════════════


class TestSkillInfra:
    """Verify key kagenti skills exist in the repo."""

    def test_skill_pr_review__all__skill_files_exist(self):
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

    def test_skill_pr_review__all__skill_structure(self):
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
class TestSkillPRReview:
    """PR review skill execution across ALL agent types."""

    @skip_no_llm
    async def test_skill_pr_review__claude_sdk_agent__follows_skill_instructions(
        self, claude_sdk_agent_url
    ):
        """Claude SDK agent follows pr-review skill instructions."""
        skill = _read_skill("github:pr-review")
        prompt = (
            f"You are executing the following code review skill:\n\n"
            f"```markdown\n{skill[:1000]}\n```\n\n"
            f"Now review this PR diff following the skill's instructions:\n\n"
            f"```diff\n{CANONICAL_DIFF}\n```"
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
    async def test_skill_pr_review__adk_agent__follows_skill_instructions(
        self, adk_agent_supervised_url
    ):
        """ADK agent follows pr-review skill instructions."""
        skill = _read_skill("github:pr-review")
        prompt = (
            f"Follow these review instructions:\n{skill[:800]}\n\n"
            f"Review this diff:\n```diff\n{CANONICAL_DIFF}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_supervised_url,
                prompt,
                request_id="skill-pr-review-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    @skip_no_llm
    async def test_skill_pr_review__adk_agent_supervised__skill_under_supervisor(
        self, adk_agent_supervised_url
    ):
        """Tier 2: ADK agent executes PR review under supervisor security."""
        skill = _read_skill("github:pr-review")
        prompt = (
            f"Follow these review instructions:\n{skill[:800]}\n\n"
            f"Review this diff:\n```diff\n{CANONICAL_DIFF}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_supervised_url,
                prompt,
                request_id="skill-pr-review-adk-supervised",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    async def test_skill_pr_review__weather_agent__no_llm(self):
        """Weather agent cannot execute skills — no LLM."""
        pytest.skip(
            "weather_agent: No LLM — cannot execute PR review skill. "
            "This is by design (weather agent is a pure tool-calling agent)."
        )

    async def test_skill_pr_review__weather_supervised__no_llm(self):
        """Supervised weather agent cannot execute skills — no LLM."""
        pytest.skip(
            "weather_supervised: No LLM — cannot execute PR review skill. "
            "Supervisor provides security isolation, not LLM capabilities."
        )

    @skip_no_llm
    @skip_no_crd
    async def test_skill_pr_review__openshell_claude__native_skill_execution(self):
        """Claude Code builtin sandbox executes pr-review skill via LiteLLM."""
        from kagenti.tests.e2e.openshell.conftest import run_claude_in_sandbox

        output = run_claude_in_sandbox(
            f"Review this diff for security issues:\n{CANONICAL_DIFF[:500]}",
        )
        if output is None:
            pytest.skip(
                "openshell_claude: sandbox or LiteLLM not available. "
                "Needs: Sandbox CRD + LiteLLM + claude-sonnet-4 model alias."
            )
        assert len(output) > 20, f"Claude Code response too short: {output[:200]}"

    @skip_no_llm
    @skip_no_crd
    async def test_skill_pr_review__openshell_opencode__litemaas_provider(self):
        """OpenCode builtin sandbox executes pr-review skill via LiteMaaS.

        Creates a sandbox with OpenCode + LiteLLM env vars, runs
        ``opencode run -m openai/gpt-4o-mini`` with the PR diff.
        LiteLLM maps gpt-4o-mini → llama-scout-17b on LiteMaaS.
        """
        from kagenti.tests.e2e.openshell.conftest import run_opencode_in_sandbox

        output = run_opencode_in_sandbox(
            f"Review this diff for security issues:\n{CANONICAL_DIFF[:500]}",
        )
        if output is None:
            pytest.skip(
                "openshell_opencode: sandbox or LiteLLM not available. "
                "Needs: Sandbox CRD + LiteLLM model proxy deployed."
            )
        assert len(output) > 20, f"OpenCode response too short: {output[:200]}"

    async def test_skill_pr_review__openshell_generic__no_agent(self):
        """Generic sandbox has no agent — cannot execute skills."""
        pytest.skip(
            "openshell_generic: No agent CLI in generic sandbox. "
            "Skills require an LLM-capable agent runtime."
        )


# ═══════════════════════════════════════════════════════════════════════════
# RCA skill (parametrized across ALL agent types)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestSkillRCA:
    """RCA (root cause analysis) skill execution across ALL agent types."""

    @skip_no_llm
    async def test_skill_rca__claude_sdk_agent__follows_skill_instructions(
        self, claude_sdk_agent_url
    ):
        """Claude SDK agent follows rca:ci skill instructions."""
        skill = _read_skill("rca:ci")
        prompt = (
            f"You are executing the following RCA skill:\n\n"
            f"```markdown\n{skill[:1000]}\n```\n\n"
            f"Analyze these CI logs following the skill's methodology:\n\n"
            f"```\n{CANONICAL_CI_LOG}\n```"
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
    async def test_skill_rca__adk_agent__follows_skill_instructions(
        self, adk_agent_supervised_url
    ):
        """ADK agent follows rca:ci skill instructions."""
        skill = _read_skill("rca:ci")
        prompt = (
            f"Follow these RCA instructions:\n{skill[:800]}\n\n"
            f"Analyze these CI logs:\n```\n{CANONICAL_CI_LOG}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_supervised_url,
                prompt,
                request_id="skill-rca-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    @skip_no_llm
    async def test_skill_rca__adk_agent_supervised__skill_under_supervisor(
        self, adk_agent_supervised_url
    ):
        """Tier 2: ADK agent executes RCA under supervisor security."""
        skill = _read_skill("rca:ci")
        prompt = (
            f"Follow these RCA instructions:\n{skill[:800]}\n\n"
            f"Analyze these CI logs:\n```\n{CANONICAL_CI_LOG}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_supervised_url,
                prompt,
                request_id="skill-rca-adk-supervised",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    async def test_skill_rca__weather_agent__no_llm(self):
        """Weather agent cannot execute RCA skill — no LLM."""
        pytest.skip("weather_agent: No LLM — cannot execute RCA skill.")

    async def test_skill_rca__weather_supervised__no_llm(self):
        """Supervised weather agent cannot execute RCA skill — no LLM."""
        pytest.skip("weather_supervised: No LLM — cannot execute RCA skill.")

    @skip_no_llm
    @skip_no_crd
    async def test_skill_rca__openshell_claude__native_execution(self):
        """Claude Code builtin sandbox executes rca:ci skill via LiteLLM."""
        from kagenti.tests.e2e.openshell.conftest import run_claude_in_sandbox

        output = run_claude_in_sandbox(
            f"Analyze this CI log and identify the root cause:\n{CANONICAL_CI_LOG}",
        )
        if output is None:
            pytest.skip("openshell_claude: sandbox or LiteLLM not available.")
        assert len(output) > 20, f"Claude Code response too short: {output[:200]}"

    @skip_no_llm
    @skip_no_crd
    async def test_skill_rca__openshell_opencode__litemaas_provider(self):
        """OpenCode executes rca:ci skill via LiteMaaS in sandbox."""
        from kagenti.tests.e2e.openshell.conftest import run_opencode_in_sandbox

        output = run_opencode_in_sandbox(
            f"Analyze these CI logs and identify the root cause:\n{CANONICAL_CI_LOG[:500]}",
        )
        if output is None:
            pytest.skip("openshell_opencode: sandbox or LiteLLM not available.")
        assert len(output) > 20, f"OpenCode RCA response too short: {output[:200]}"

    async def test_skill_rca__openshell_generic__no_agent(self):
        """Generic sandbox has no agent — cannot execute RCA skill."""
        pytest.skip("openshell_generic: No agent CLI — cannot execute skills.")

    @skip_no_llm
    async def test_skill_rca__claude_sdk_agent__identifies_root_cause(
        self, claude_sdk_agent_url
    ):
        """Send CI-style error logs and ask agent for root cause analysis."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                f"Analyze these CI logs and identify the root cause:\n\n"
                f"```\n{CANONICAL_CI_LOG}\n```",
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


# ═══════════════════════════════════════════════════════════════════════════
# Security Review skill (parametrized across ALL agent types)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestSkillSecurity:
    """Security review skill execution across ALL agent types."""

    @skip_no_llm
    async def test_skill_security__claude_sdk_agent__follows_skill(
        self, claude_sdk_agent_url
    ):
        """Claude SDK agent follows security review skill."""
        skill = _read_skill("test:review")
        prompt = (
            f"Execute this security review skill:\n{skill[:800]}\n\n"
            f"Review this code:\n```python\n{CANONICAL_CODE}\n```"
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
    async def test_skill_security__adk_agent__follows_skill(
        self, adk_agent_supervised_url
    ):
        """ADK agent follows security review skill."""
        skill = _read_skill("test:review")
        prompt = (
            f"Follow these security review instructions:\n{skill[:800]}\n\n"
            f"Review this code:\n```python\n{CANONICAL_CODE}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_supervised_url,
                prompt,
                request_id="skill-security-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    @skip_no_llm
    async def test_skill_security__adk_agent_supervised__skill_under_supervisor(
        self, adk_agent_supervised_url
    ):
        """Tier 2: ADK agent executes security review under supervisor."""
        skill = _read_skill("test:review")
        prompt = (
            f"Follow these security review instructions:\n{skill[:800]}\n\n"
            f"Review this code:\n```python\n{CANONICAL_CODE}\n```"
        )
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                adk_agent_supervised_url,
                prompt,
                request_id="skill-security-adk-supervised",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    async def test_skill_security__weather_agent__no_llm(self):
        """Weather agent cannot execute security review — no LLM."""
        pytest.skip("weather_agent: No LLM — cannot execute security review skill.")

    async def test_skill_security__weather_supervised__no_llm(self):
        """Supervised weather agent cannot execute security review — no LLM."""
        pytest.skip(
            "weather_supervised: No LLM — cannot execute security review skill."
        )

    @skip_no_llm
    @skip_no_crd
    async def test_skill_security__openshell_claude__native(self):
        """Claude Code builtin sandbox executes security review via LiteLLM."""
        from kagenti.tests.e2e.openshell.conftest import run_claude_in_sandbox

        output = run_claude_in_sandbox(
            "What security issues are in this code? "
            "Answer with a numbered list:\n"
            "import pickle\n"
            "def load(p): return pickle.load(open(p,'rb'))\n"
            "def run(c): return os.system(c)\n"
            "def q(n): return db.execute(f\"SELECT * FROM t WHERE n='{n}'\")\n",
        )
        if output is None:
            pytest.skip("openshell_claude: sandbox or LiteLLM not available.")
        stripped = output.strip()
        if len(stripped) < 10:
            pytest.xfail(
                "LLM returned empty — known flaky with llama-scout-17b via LiteMaaS"
            )
        assert len(stripped) > 10, f"Claude Code response too short: {stripped[:200]}"

    @skip_no_llm
    @skip_no_crd
    async def test_skill_security__openshell_opencode__litemaas(self):
        """OpenCode executes security review via LiteMaaS in sandbox."""
        from kagenti.tests.e2e.openshell.conftest import run_opencode_in_sandbox

        output = run_opencode_in_sandbox(
            f"Review this code for security issues:\n{CANONICAL_CODE[:400]}",
        )
        if output is None:
            pytest.skip("openshell_opencode: sandbox or LiteLLM not available.")
        assert len(output) > 20, f"OpenCode security review too short: {output[:200]}"

    async def test_skill_security__openshell_generic__no_agent(self):
        """Generic sandbox has no agent — cannot execute skills."""
        pytest.skip("openshell_generic: No agent CLI — cannot execute skills.")


# ═══════════════════════════════════════════════════════════════════════════
# GitHub PR skill (merged: code generation + real-world PR review)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestSkillGithubPR:
    """Code generation skill execution across ALL agent types."""

    @skip_no_llm
    async def test_skill_github_pr__claude_sdk_agent__generates_code(
        self, claude_sdk_agent_url
    ):
        """Claude SDK agent generates code from a natural language spec."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                "Write a Python function called `fibonacci(n)` that returns the "
                "nth Fibonacci number using iteration. Include a docstring.",
                request_id="code-gen-claude",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30
        text_lower = text.lower()
        assert "def " in text_lower or "fibonacci" in text_lower, (
            f"Response doesn't contain code: {text[:200]}"
        )

    @skip_no_llm
    async def test_skill_github_pr__claude_sdk_agent__fetches_and_reviews(
        self, claude_sdk_agent_url
    ):
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
    async def test_skill_github_pr__adk_agent__fetches_and_reviews(
        self, adk_agent_supervised_url
    ):
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
                adk_agent_supervised_url,
                f"Review this PR diff:\n```diff\n{diff_text}\n```",
                request_id="github-pr-review-adk",
                timeout=120.0,
            )
        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text and len(text) > 30

    @skip_no_llm
    @skip_no_crd
    async def test_skill_github_pr__openshell_claude__native_clone_and_review(self):
        """Claude Code sandbox reviews a code snippet via LiteLLM."""
        from kagenti.tests.e2e.openshell.conftest import run_claude_in_sandbox

        output = run_claude_in_sandbox(
            f"Review this diff for issues:\n{CANONICAL_DIFF[:500]}",
        )
        if output is None:
            pytest.skip("openshell_claude: sandbox or LiteLLM not available.")
        assert len(output) > 20, f"Claude Code response too short: {output[:200]}"

    @skip_no_llm
    @skip_no_crd
    async def test_skill_github_pr__openshell_opencode__litemaas_review(self):
        """OpenCode sandbox reviews real PR diff via LiteMaaS."""
        from kagenti.tests.e2e.openshell.conftest import run_opencode_in_sandbox

        output = run_opencode_in_sandbox(
            f"Review this PR diff:\n{CANONICAL_DIFF[:500]}",
        )
        if output is None:
            pytest.skip("openshell_opencode: sandbox or LiteLLM not available.")
        assert len(output) > 20, f"OpenCode PR review too short: {output[:200]}"


# ═══════════════════════════════════════════════════════════════════════════
# NemoClaw OpenClaw skill execution (via gateway HTTP API)
# ═══════════════════════════════════════════════════════════════════════════

from kagenti.tests.e2e.openshell.conftest import nemoclaw_enabled

skip_no_nemoclaw = pytest.mark.skipif(
    not nemoclaw_enabled(), reason="NemoClaw tests disabled"
)


@pytest.mark.asyncio
class TestSkillOpenClaw:
    """Skill execution via OpenClaw gateway HTTP API."""

    @skip_no_nemoclaw
    @skip_no_llm
    async def test_skill_pr_review__nemoclaw_openclaw__gateway(
        self, nemoclaw_openclaw_url, request
    ):
        """OpenClaw reviews code for security issues via gateway."""
        import time

        prompt = (
            f"Review this diff for security issues:\n```diff\n{CANONICAL_DIFF}\n```"
        )
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            resp = await openclaw_chat(client, nemoclaw_openclaw_url, prompt)
        duration = time.monotonic() - t0
        if resp is None:
            pytest.skip(
                "nemoclaw_openclaw: gateway does not expose /v1/chat/completions. "
                "OpenClaw uses its own gateway protocol. "
                "TODO: A2A adapter or NemoClaw OpenAI-compat plugin."
            )
        text = litellm_chat_text(resp)

        record_llm_metric(
            test_name=request.node.name,
            model=resp.get("model", "unknown"),
            agent="nemoclaw_openclaw",
            capability="skill_pr_review",
            status="PASSED" if text and len(text) > 30 else "FAILED",
            response=resp,
            duration_s=duration,
            response_text=text,
            keywords=["sql", "injection", "security"],
        )
        assert text and len(text) > 30, f"OpenClaw PR review too short: {text[:200]}"

    @skip_no_nemoclaw
    @skip_no_llm
    async def test_skill_rca__nemoclaw_openclaw__gateway(
        self, nemoclaw_openclaw_url, request
    ):
        """OpenClaw analyzes CI logs via gateway."""
        import time

        prompt = (
            f"Analyze these CI logs and identify the root cause:\n"
            f"```\n{CANONICAL_CI_LOG}\n```"
        )
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            resp = await openclaw_chat(client, nemoclaw_openclaw_url, prompt)
        duration = time.monotonic() - t0
        if resp is None:
            pytest.skip(
                "nemoclaw_openclaw: gateway does not expose /v1/chat/completions. "
                "TODO: A2A adapter or NemoClaw OpenAI-compat plugin."
            )
        text = litellm_chat_text(resp)

        record_llm_metric(
            test_name=request.node.name,
            model=resp.get("model", "unknown"),
            agent="nemoclaw_openclaw",
            capability="skill_rca",
            status="PASSED" if text and len(text) > 30 else "FAILED",
            response=resp,
            duration_s=duration,
            response_text=text,
            keywords=["secret", "webhook", "tls", "root cause"],
        )
        assert text and len(text) > 30, f"OpenClaw RCA too short: {text[:200]}"

    @skip_no_nemoclaw
    @skip_no_llm
    async def test_skill_security__nemoclaw_openclaw__gateway(
        self, nemoclaw_openclaw_url, request
    ):
        """OpenClaw reviews code for security vulnerabilities via gateway."""
        import time

        prompt = (
            f"Review this code for security vulnerabilities:\n"
            f"```python\n{CANONICAL_CODE}\n```"
        )
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            resp = await openclaw_chat(client, nemoclaw_openclaw_url, prompt)
        duration = time.monotonic() - t0
        if resp is None:
            pytest.skip(
                "nemoclaw_openclaw: gateway does not expose /v1/chat/completions. "
                "TODO: A2A adapter or NemoClaw OpenAI-compat plugin."
            )
        text = litellm_chat_text(resp)

        record_llm_metric(
            test_name=request.node.name,
            model=resp.get("model", "unknown"),
            agent="nemoclaw_openclaw",
            capability="skill_security",
            status="PASSED" if text and len(text) > 30 else "FAILED",
            response=resp,
            duration_s=duration,
            response_text=text,
            keywords=["pickle", "shell", "injection", "sql"],
        )
        assert text and len(text) > 30, (
            f"OpenClaw security review too short: {text[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Audit logging (CLI binary presence checks)
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditLogging:
    """Verify builtin sandbox images have expected CLI tools."""

    @skip_no_crd
    def test_audit_logging__openshell_claude__claude_binary_present(self):
        """Claude Code sandbox must have `claude` binary."""
        from kagenti.tests.e2e.openshell.conftest import (
            _ensure_claude_sandbox,
            kubectl_run,
        )

        pod = _ensure_claude_sandbox()
        if not pod:
            pytest.skip("Claude sandbox pod not available")
        result = kubectl_run(
            "exec",
            pod,
            "-n",
            "team1",
            "--",
            "sh",
            "-c",
            "which claude && claude --version 2>/dev/null || true",
            timeout=15,
        )
        assert result.returncode == 0, f"exec failed: {result.stderr}"
        assert "claude" in result.stdout.lower(), (
            f"claude binary not found in sandbox: {result.stdout}"
        )

    @skip_no_crd
    def test_audit_logging__openshell_opencode__opencode_binary_present(self):
        """OpenCode sandbox must have `opencode` binary."""
        from kagenti.tests.e2e.openshell.conftest import (
            _ensure_claude_sandbox,
            kubectl_run,
        )

        pod = _ensure_claude_sandbox()
        if not pod:
            pytest.skip("Sandbox pod not available")
        result = kubectl_run(
            "exec",
            pod,
            "-n",
            "team1",
            "--",
            "sh",
            "-c",
            "which opencode 2>/dev/null && echo found || echo missing",
            timeout=15,
        )
        assert result.returncode == 0, f"exec failed: {result.stderr}"
        if "missing" in result.stdout:
            pytest.skip(
                "opencode binary not in base image — "
                "OpenCode tests use a separate sandbox with opencode pre-installed"
            )
        assert "found" in result.stdout

    @skip_no_crd
    def test_audit_logging__openshell_generic__has_bash_and_tools(self):
        """Generic sandbox must have bash, git, curl."""
        from kagenti.tests.e2e.openshell.conftest import (
            _ensure_claude_sandbox,
            kubectl_run,
        )

        pod = _ensure_claude_sandbox()
        if not pod:
            pytest.skip("Sandbox pod not available")
        result = kubectl_run(
            "exec",
            pod,
            "-n",
            "team1",
            "--",
            "sh",
            "-c",
            "which bash && which git && which curl && echo all-found",
            timeout=15,
        )
        assert result.returncode == 0, f"exec failed: {result.stderr}"
        assert "all-found" in result.stdout, (
            f"Missing tools in sandbox: {result.stdout}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Per-model skill execution (parametrized across OPENSHELL_LLM_MODELS)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestSkillPerModel:
    """Run skill tests with each configured LLM model via direct LiteLLM calls.

    Calls LiteLLM proxy directly with model=<specific_model> to test each
    model independently. Records per-model metrics (tokens, time, quality)
    to $LOG_DIR/llm-metrics.json for the parser.
    """

    @skip_no_llm
    @pytest.mark.parametrize(
        "model",
        SKILL_MODELS,
        ids=lambda m: m.replace(":", "_").replace("/", "_"),
    )
    async def test_skill_pr_review__per_model__responds(self, model, request):
        """PR review skill works with each configured model."""
        if model == "default":
            pytest.skip("No OPENSHELL_LLM_MODELS configured")

        import time

        prompt = (
            f"Review this diff for security issues. List each issue found:\n"
            f"```diff\n{CANONICAL_DIFF}\n```"
        )
        keywords = ["sql", "injection", "os.system", "command", "security"]
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            resp = await litellm_chat(client, prompt, model=model)
        duration = time.monotonic() - t0
        text = litellm_chat_text(resp)

        record_llm_metric(
            test_name=request.node.name,
            model=model,
            agent="litellm_direct",
            capability="skill_pr_review",
            status="PASSED" if text and len(text) > 30 else "FAILED",
            response=resp,
            duration_s=duration,
            response_text=text,
            keywords=keywords,
        )
        assert text and len(text) > 30, (
            f"Model {model} produced insufficient output: {text[:200]}"
        )

    @skip_no_llm
    @pytest.mark.parametrize(
        "model",
        SKILL_MODELS,
        ids=lambda m: m.replace(":", "_").replace("/", "_"),
    )
    async def test_skill_rca__per_model__identifies_issue(self, model, request):
        """RCA skill works with each configured model."""
        if model == "default":
            pytest.skip("No OPENSHELL_LLM_MODELS configured")

        import time

        prompt = (
            f"Analyze these CI logs and identify the root cause of the failure:\n"
            f"```\n{CANONICAL_CI_LOG}\n```"
        )
        keywords = ["secret", "webhook", "tls", "mount", "root cause", "missing"]
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            resp = await litellm_chat(client, prompt, model=model)
        duration = time.monotonic() - t0
        text = litellm_chat_text(resp)

        record_llm_metric(
            test_name=request.node.name,
            model=model,
            agent="litellm_direct",
            capability="skill_rca",
            status="PASSED" if text and len(text) > 30 else "FAILED",
            response=resp,
            duration_s=duration,
            response_text=text,
            keywords=keywords,
        )
        assert text and len(text) > 30, (
            f"Model {model} RCA output too short: {text[:200]}"
        )

    @skip_no_llm
    @pytest.mark.parametrize(
        "model",
        SKILL_MODELS,
        ids=lambda m: m.replace(":", "_").replace("/", "_"),
    )
    async def test_skill_security__per_model__finds_issues(self, model, request):
        """Security review skill works with each configured model."""
        if model == "default":
            pytest.skip("No OPENSHELL_LLM_MODELS configured")

        import time

        prompt = (
            f"Review this code for security vulnerabilities. List each issue:\n"
            f"```python\n{CANONICAL_CODE}\n```"
        )
        keywords = ["pickle", "shell=true", "injection", "sql", "command"]
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            resp = await litellm_chat(client, prompt, model=model)
        duration = time.monotonic() - t0
        text = litellm_chat_text(resp)

        record_llm_metric(
            test_name=request.node.name,
            model=model,
            agent="litellm_direct",
            capability="skill_security",
            status="PASSED" if text and len(text) > 30 else "FAILED",
            response=resp,
            duration_s=duration,
            response_text=text,
            keywords=keywords,
        )
        assert text and len(text) > 30, (
            f"Model {model} security review too short: {text[:200]}"
        )
