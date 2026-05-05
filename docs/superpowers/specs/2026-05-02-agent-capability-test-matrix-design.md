# Agent Capability Test Matrix — Design Spec

> **Date:** 2026-05-02
> **PR:** #1395 (fix/litellm-security-and-waypoint)
> **Status:** Design — pending implementation plan

## Problem

The OpenShell E2E test suite has uneven coverage across agents. Claude SDK
has 11/19 capabilities tested; Claude Code has 5/19; OpenCode has 3/19;
OpenClaw has 0/19. Tests are scattered across agent-specific files with
inconsistent naming. There is no per-model parametrization and no tracking
of missing capabilities.

## Goal

1. Define a canonical set of 19 agent capabilities grouped into 4 tiers
2. Restructure tests so every capability is tested for every agent
3. Use per-agent adapters in unified test files (not separate files per agent)
4. Parametrize skill tests across LLM models
5. Track MISS/SKIP/PASS/FAIL per capability × agent × model in CI output
6. Update `parse-test-matrix.sh` and `e2e-test-matrix.md` to match

## Capability Set (19 capabilities, 4 tiers)

### Tier 1: Agent Infrastructure (no LLM required)

| # | ID | Capability | Test pattern | What it validates |
|---|---|---|---|---|
| 1 | `connectivity` | Connectivity | `test_connectivity__<agent>` | Agent responds to basic request |
| 2 | `credential_security` | Credential security | `test_credential_security__<agent>` | No hardcoded secrets in env/config |
| 3 | `sandbox_lifecycle` | Sandbox lifecycle | `test_sandbox_lifecycle__<agent>` | Create, list, delete sandbox |
| 4 | `workspace` | Workspace persistence | `test_workspace__<agent>` | Data persists across pod restarts |
| 5 | `resource_limits` | Resource limits | `test_resource_limits__<agent>` | Agent respects CPU/memory budgets |

### Tier 2: Agent Capabilities (requires LLM)

| # | ID | Capability | Test pattern | What it validates |
|---|---|---|---|---|
| 6 | `multiturn` | Multiturn conversation | `test_multiturn__<agent>` | Stateful conversation (3+ turns) |
| 7 | `context_isolation` | Context isolation | `test_context_isolation__<agent>` | Sessions don't leak between users |
| 8 | `session_resume` | Session resume | `test_session_resume__<agent>` | Survives pod restart, context preserved |
| 9 | `cross_session_memory` | Cross-session memory | `test_cross_session_memory__<agent>` | Remembers context from previous session |
| 10 | `streaming` | Streaming / SSE | `test_streaming__<agent>` | Real-time partial response delivery |
| 11 | `tool_calling` | Tool calling / MCP | `test_tool_calling__<agent>` | Discovers and invokes external tools |
| 12 | `concurrent_sessions` | Concurrent sessions | `test_concurrent_sessions__<agent>` | Multiple users don't interfere |

### Tier 3: Skill Execution (requires LLM, per-model parametrized)

| # | ID | Capability | Test pattern | What it validates |
|---|---|---|---|---|
| 13 | `skill_pr_review` | Skill: PR review | `test_skill_pr_review__<agent>__<model>` | LLM reviews code for issues |
| 14 | `skill_rca` | Skill: RCA | `test_skill_rca__<agent>__<model>` | LLM diagnoses failures |
| 15 | `skill_security` | Skill: Security review | `test_skill_security__<agent>__<model>` | LLM finds vulnerabilities |
| 16 | `skill_github_pr` | Skill: GitHub PR | `test_skill_github_pr__<agent>__<model>` | Clones live repo and reviews |

### Tier 4: Security & Policy

| # | ID | Capability | Test pattern | What it validates |
|---|---|---|---|---|
| 17 | `hitl_network` | HITL: Network policy | `test_hitl_network__<agent>` | Unauthorized egress blocked |
| 18 | `hitl_tool_approval` | HITL: Tool approval | `test_hitl_tool_approval__<agent>` | Agent requests permission before tool use |
| 19 | `audit_logging` | Audit logging | `test_audit_logging__<agent>` | Agent actions produce OTel spans |

## Test Architecture: Unified Files with Agent Adapters

### Current structure (fragmented)

```
test_02_a2a_connectivity.py     # A2A agents only
test_05_multiturn_conversation.py  # A2A agents only
test_07_skill_execution.py      # All agents, but different code paths
test_11_nemoclaw_smoke.py       # NemoClaw only
test_12_litellm_claude_sandbox.py  # Claude Code only
```

### Target structure (unified per capability)

```
kagenti/tests/e2e/openshell/
├── conftest.py                    # Agent registry + adapters
├── test_cap_connectivity.py       # Capability: connectivity
├── test_cap_credential_security.py
├── test_cap_multiturn.py
├── test_cap_context_isolation.py
├── test_cap_session_resume.py
├── test_cap_cross_session_memory.py
├── test_cap_streaming.py
├── test_cap_tool_calling.py
├── test_cap_concurrent_sessions.py
├── test_cap_sandbox_lifecycle.py
├── test_cap_workspace.py
├── test_cap_resource_limits.py
├── test_cap_skill_execution.py    # All 4 skills, parametrized by model
├── test_cap_hitl.py               # Network + tool approval
├── test_cap_audit_logging.py
├── test_infra_gateway.py          # Platform infra (not per-agent)
├── test_infra_litellm.py
├── test_infra_waypoint.py
└── test_infra_platform.py
```

Each `test_cap_*.py` file:
1. Imports all agents from the registry
2. Parametrizes across agents and (for Tier 3) models
3. Uses per-agent adapters for the actual interaction

### Agent Adapter Pattern

```python
# conftest.py

class AgentAdapter:
    """Interface for sending requests to any agent type."""
    name: str
    protocol: str  # "a2a", "sandbox_exec", "gateway_http"

    def send_message(self, prompt: str, model: str = None) -> str:
        """Send a prompt and return the response text."""
        ...

    def send_streaming(self, prompt: str) -> Iterator[str]:
        """Send a prompt and yield partial responses."""
        ...

    def check_connectivity(self) -> bool:
        """Verify the agent is reachable."""
        ...

    def get_session_id(self) -> str:
        """Return current session identifier."""
        ...

# Concrete adapters
class A2AAgentAdapter(AgentAdapter):
    """For Claude SDK, ADK — uses A2A JSON-RPC via port-forward."""

class SandboxExecAdapter(AgentAdapter):
    """For Claude Code, OpenCode — uses kubectl exec into sandbox pod."""

class GatewayHTTPAdapter(AgentAdapter):
    """For OpenClaw — uses HTTP gateway API."""

# Registry
ALL_AGENTS = [
    A2AAgentAdapter("claude_sdk_agent", ...),
    A2AAgentAdapter("adk_supervised", ...),
    A2AAgentAdapter("weather_supervised", ...),
    SandboxExecAdapter("openshell_claude", ...),
    SandboxExecAdapter("openshell_opencode", ...),
    GatewayHTTPAdapter("nemoclaw_openclaw", ...),
]

PRIORITY_AGENTS = [a for a in ALL_AGENTS if a.name in
    ("openshell_claude", "openshell_opencode", "nemoclaw_openclaw")]
```

### Per-Model Parametrization

```python
# conftest.py

MODELS = ["llama-scout-17b"]  # Default
if os.getenv("MAAS_DEEPSEEK_API_KEY"):
    MODELS.append("deepseek-r1")
if os.getenv("MAAS_MISTRAL_API_KEY"):
    MODELS.append("mistral-small")

# test_cap_skill_execution.py

@pytest.mark.parametrize("agent", ALL_AGENTS, ids=lambda a: a.name)
@pytest.mark.parametrize("model", MODELS)
def test_skill_pr_review(agent, model):
    response = agent.send_message(
        f"Review this diff for issues:\n{CANONICAL_DIFF}",
        model=model,
    )
    assert "security" in response.lower() or "injection" in response.lower()
```

### Test Naming Convention

Pattern: `test_<capability>__<agent>__<model>`

Examples:
- `test_connectivity__claude_sdk_agent`
- `test_multiturn__openshell_claude`
- `test_skill_pr_review__openshell_opencode__deepseek_r1`
- `test_hitl_network__adk_supervised`

## HITL Test Design

### Generic HITL: Network Policy

Every agent type has network isolation — the mechanism differs but the test
interface is the same:

| Agent type | Isolation mechanism | Test approach |
|---|---|---|
| Supervised (ADK, Weather) | OPA proxy in netns at 10.200.0.1:3128 | kubectl exec → python urllib with proxy |
| Sandbox (Claude Code, OpenCode) | Gateway sandbox network policy | kubectl exec → python urllib direct |
| NemoClaw (OpenClaw) | NemoClaw policy.yaml network_policies | kubectl exec → python urllib direct |

```python
@pytest.mark.parametrize("agent", ALL_AGENTS, ids=lambda a: a.name)
def test_hitl_network(agent):
    """Unauthorized egress is blocked regardless of sandbox model."""
    result = agent.exec_in_agent(
        "python3", "-c",
        "import urllib.request; urllib.request.urlopen('http://example.com', timeout=5)"
    )
    assert result.returncode != 0 or "403" in result.output or "denied" in result.output
```

### HITL: Tool Approval

For agents that support tool calling, test that the agent waits for approval
before executing a tool that requires it. This is agent-framework-specific:
- Claude Code: `allowedTools` in CLAUDE.md
- ADK: tool approval via A2A `requires_approval` field
- OpenClaw: NemoClaw exec-approvals

## Parser Updates

Update `parse-test-matrix.sh` to:
1. Use the canonical 19 capability IDs for classification
2. Show MISS for capabilities that have no test for an agent
3. Show per-model breakdown for Tier 3 capabilities
4. Output both the agent summary table and the capability × agent matrix

## Migration Strategy

1. **Phase 1 (this PR):** Update parser + skill + docs with the 19-capability
   framework. Track MISS/SKIP for capabilities that don't exist yet.
2. **Phase 2 (follow-up PR):** Add the `AgentAdapter` pattern to conftest.py.
   Migrate existing tests to use it. No new capabilities yet — just restructure.
3. **Phase 3 (follow-up PR):** Add missing Tier 1-2 capabilities for Claude Code
   and OpenCode (connectivity, credential, multiturn, isolation, resume).
4. **Phase 4 (follow-up PR):** Add per-model parametrization to Tier 3 tests.
5. **Phase 5 (follow-up PR):** Add Tier 2 advanced capabilities (cross-session
   memory, streaming, tool calling, concurrent sessions) and Tier 4 (HITL, audit).

## Parallel Test Execution

Tests should run efficiently in parallel where possible:

1. **Per-tier parallelism:** Tier 1 tests need no LLM and can run immediately
   while Tier 2-3 tests wait for LLM proxy readiness. Run Tier 1 first, then
   Tier 2-4 in parallel.

2. **Per-agent parallelism:** Each agent is independent — tests for different
   agents can run concurrently. Use pytest-xdist or mark-based grouping:
   ```bash
   pytest -m "tier1" --parallel  # All T1 tests, all agents, concurrently
   pytest -m "tier2 or tier3" --parallel  # LLM tests after proxy ready
   ```

3. **Per-model is sequential within an agent:** Skill tests across models for
   the SAME agent should run sequentially (agent state, LLM rate limits).
   Different agents × different models can run in parallel.

4. **Environment parallelism:** CI Kind and CI HyperShift already run in
   parallel (different workflows). Local Kind can run alongside CI.

Target test execution time: under 10 minutes for full suite.

## Priority Order

1. Claude Code — from 5/19 to 15/19 (add Tier 1 + 2 basics)
2. OpenClaw — from 0/19 to 10/19 (add Tier 1 + basic skills)
3. OpenCode — from 3/19 to 15/19 (add Tier 1 + 2 basics)
4. Per-model parametrization for all agents
5. Advanced capabilities (cross-session memory, streaming, tool calling)
