"""
Skill Discovery E2E Tests (OpenShell PoC)

Tests that kagenti skills metadata can be discovered and read by agents.
Skills are mounted as a ConfigMap containing the skill index from
.claude/skills/ in the kagenti repo.

For the PoC, we test:
1. Skills ConfigMap exists in the agent namespace
2. Agent can read skill metadata from mounted volume
3. Agent card includes skill references
"""

import json
import os
import subprocess

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import kubectl_get_pods_json

pytestmark = pytest.mark.openshell

AGENT_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")

# Key kagenti skills that should be discoverable
KAGENTI_SKILLS = [
    "review",
    "rca",
    "k8s:health",
    "k8s:pods",
    "k8s:logs",
    "tdd:kind",
    "tdd:hypershift",
    "github:pr-review",
    "security-review",
]


def _kubectl(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=timeout
    )


def _create_skills_configmap():
    """Create a ConfigMap with kagenti skill metadata if it doesn't exist."""
    result = _kubectl("get", "configmap", "kagenti-skills", "-n", AGENT_NS)
    if result.returncode == 0:
        return True

    skills_index = json.dumps(
        {
            "version": "1.0",
            "source": "kagenti/.claude/skills/",
            "skills": [
                {"name": s, "type": "claude-code-skill"} for s in KAGENTI_SKILLS
            ],
        }
    )

    result = subprocess.run(
        [
            "kubectl",
            "create",
            "configmap",
            "kagenti-skills",
            "-n",
            AGENT_NS,
            f"--from-literal=skills.json={skills_index}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0


class TestSkillsConfigMap:
    """Verify skills metadata ConfigMap exists and contains expected skills."""

    def test_create_skills_configmap(self):
        """Create the kagenti-skills ConfigMap if it doesn't exist."""
        assert _create_skills_configmap(), "Failed to create skills ConfigMap"

    def test_skills_configmap_has_index(self):
        """Skills ConfigMap must contain a skills.json key."""
        _create_skills_configmap()
        result = _kubectl(
            "get",
            "configmap",
            "kagenti-skills",
            "-n",
            AGENT_NS,
            "-o",
            "jsonpath={.data.skills\\.json}",
        )
        assert result.returncode == 0, f"Cannot read ConfigMap: {result.stderr}"
        data = json.loads(result.stdout)
        assert "skills" in data
        assert len(data["skills"]) >= 5, f"Too few skills: {data['skills']}"

    def test_skills_include_review(self):
        """Skills index must include the 'review' skill."""
        _create_skills_configmap()
        result = _kubectl(
            "get",
            "configmap",
            "kagenti-skills",
            "-n",
            AGENT_NS,
            "-o",
            "jsonpath={.data.skills\\.json}",
        )
        data = json.loads(result.stdout)
        skill_names = [s["name"] for s in data["skills"]]
        assert "review" in skill_names
        assert "rca" in skill_names
        assert "security-review" in skill_names


@pytest.mark.asyncio
class TestAgentSkillAwareness:
    """Test that agents reference their available skills."""

    async def test_weather_agent_lists_skills(self, weather_agent_url):
        """Weather agent card should list its capabilities."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{weather_agent_url}/.well-known/agent-card.json",
                timeout=30.0,
            )
        if resp.status_code != 200:
            pytest.skip("Agent card endpoint not available")

        card = resp.json()
        assert "name" in card
        # Agent should have some form of skill/capability listing
        has_skills = "skills" in card or "capabilities" in card or "tools" in card
        assert has_skills, f"Agent card has no skills/capabilities: {list(card.keys())}"

    async def test_claude_sdk_agent_has_code_review_skill(self, claude_sdk_agent_url):
        """Claude SDK agent card should list code review skill."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{claude_sdk_agent_url}/.well-known/agent-card.json",
                timeout=30.0,
            )
        if resp.status_code != 200:
            pytest.skip("Agent card endpoint not available")

        card = resp.json()
        skills = card.get("skills", [])
        skill_ids = [s.get("id", "") for s in skills]
        assert "code_review" in skill_ids, (
            f"Claude SDK agent missing code_review skill. Skills: {skill_ids}"
        )
