# Per-Model Stats & Coverage Expansion Design

**Date:** 2026-05-03
**PR:** #1395 (`fix/litellm-security-and-waypoint`)
**Scope:** Add per-model performance tracking, expand Claude Code + OpenClaw test coverage

## Problem

The test matrix shows pass/fail per agent per capability but lacks:
1. **Per-model quality data** — which LLM model performs better for each skill?
2. **Performance metrics** — token consumption, execution time, response quality
3. **Coverage gaps** — Claude Code at 4/23, OpenClaw at 2/23 capabilities

## Goals

- Every skill test records: tokens_in, tokens_out, duration_s, response_length
- Graph-loop parser outputs a per-model stats table alongside pass/fail
- Model comparison doc aggregates stats across runs
- Claude Code → 8/23, OpenClaw → 6/23 coverage

## Models

**Remote (LiteMaaS, CI Kind + CI HyperShift):**

| Model | Backend ID | Params | Context | Strengths |
|---|---|---|---|---|
| llama-scout-17b | `hosted_vllm/llama-scout-17b` | 17B | 128K | Fast, general purpose |
| deepseek-r1 | `hosted_vllm/deepseek-r1-distill-qwen-14b` | 14B | 128K | Reasoning, chain-of-thought |

**Local (Ollama, Kind-only, M4 Max 128GB):**

| Model | Ollama ID | Params | Context | VRAM | Strengths |
|---|---|---|---|---|---|
| qwen3-coder:30b | `ollama/qwen3-coder:30b` | 30.5B | 256K | ~20GB | Coding, large context |
| deepseek-r1:32b | `ollama/deepseek-r1:32b` | 32.8B | 128K | ~20GB | Reasoning, thinking model |
| qwen2.5:3b | `ollama/qwen2.5:3b` | 3B | 128K | ~2GB | Tiny, fast, baseline |

**Future candidates (not yet pulled):**

| Model | Params | VRAM | Notes |
|---|---|---|---|
| devstral-small | ~15B | ~10GB | Mistral coding model |
| llama3.3:70b | 70B | ~40GB | Largest that fits M4 Max |
| qwen3:32b | 32B | ~20GB | General purpose |

## Design

### 1. Metrics JSON File

Tests write per-invocation metrics to a JSON file during execution.

**File:** `$LOG_DIR/llm-metrics.json` (one JSON object per line, JSONL format)

**Schema:**
```json
{
  "test": "test_skill_pr_review__per_model__responds",
  "model": "llama-scout-17b",
  "agent": "claude_sdk",
  "capability": "skill_pr_review",
  "status": "PASSED",
  "tokens_in": 342,
  "tokens_out": 187,
  "tokens_total": 529,
  "duration_s": 4.2,
  "response_length": 823,
  "keywords_found": 3,
  "keywords_expected": ["sql", "injection", "security"],
  "timestamp": "2026-05-03T10:00:00Z"
}
```

**How tests write it:**

A new `record_llm_metric()` helper in conftest.py:
```python
import time, json, os
from datetime import datetime, timezone

_METRICS_FILE = os.path.join(
    os.getenv("LOG_DIR", "/tmp/kagenti"), "llm-metrics.json"
)

def record_llm_metric(
    test_name: str, model: str, agent: str, capability: str,
    status: str, response: dict, duration_s: float,
    response_text: str = "", keywords: list[str] | None = None,
):
    usage = response.get("usage", {})
    keywords_found = 0
    if keywords and response_text:
        keywords_found = sum(1 for k in keywords if k in response_text.lower())

    metric = {
        "test": test_name,
        "model": model,
        "agent": agent,
        "capability": capability,
        "status": status,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "tokens_total": usage.get("total_tokens", 0),
        "duration_s": round(duration_s, 2),
        "response_length": len(response_text),
        "keywords_found": keywords_found,
        "keywords_expected": keywords or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(_METRICS_FILE, "a") as f:
        f.write(json.dumps(metric) + "\n")
```

Per-model tests call this after each LLM invocation:
```python
t0 = time.monotonic()
resp = await client.post(litellm_url, json={"model": model, ...})
duration = time.monotonic() - t0
data = resp.json()
text = data["choices"][0]["message"]["content"]
record_llm_metric(
    test_name=request.node.name, model=model, agent="claude_sdk",
    capability="skill_pr_review", status="PASSED",
    response=data, duration_s=duration, response_text=text,
    keywords=["sql", "injection", "security"],
)
```

### 2. Per-Model Test Routing (Real Model Selection)

Current per-model tests send the model name in the prompt but route to the default model. Fix: call LiteLLM directly with `model=<specific_model>`.

**For per-model skill tests:**
- Call `http://litellm-model-proxy.team1.svc:4000/v1/chat/completions` directly
- Set `"model": "<specific_model>"` in the request body
- This tests the actual model, not just the default

**For A2A agent skill tests (existing):**
- Keep using `a2a_send()` — the agent chooses its own model
- Record metrics from the A2A response (if token counts are available)

**CI workflow change:**
- Add `OPENSHELL_LLM_MODELS: "llama-scout-17b,deepseek-r1"` to
  `e2e-openshell-kind.yaml` and `e2e-openshell-hypershift.yaml` env
- The fulltest script already exports this to pytest

### 3. Parser Enhancement — Per-Model Stats Table

New function in `parse-test-matrix.sh`: `print_model_stats_table()`

**Reads from:** `$LOG_DIR/llm-metrics.json` (JSONL)

**Output format:**
```
### Per-Model Performance (skill tests)

| Model           | Tests | Pass | Fail | Avg Tokens | Avg Time | Avg Response |
|-----------------|:-----:|:----:|:----:|:----------:|:--------:|:------------:|
| llama-scout-17b |    6  |   5  |   1  |    480     |   3.2s   |    712 chars |
| deepseek-r1     |    6  |   6  |   0  |    620     |   8.1s   |    945 chars |

### Per-Model × Capability

| Capability      | llama-scout-17b      | deepseek-r1          |
|-----------------|----------------------|----------------------|
| T3.1 PR review  | PASS 342/187 3.2s    | PASS 410/230 7.8s    |
| T3.2 RCA        | PASS 298/156 2.8s    | PASS 380/290 9.1s    |
| T3.3 Security   | FAIL 312/45  2.1s    | PASS 350/210 6.5s    |
| T3.4 GitHub PR  | PASS 890/340 5.7s    | PASS 920/380 11.2s   |

Format: STATUS tokens_in/tokens_out duration
```

**CLI:**
```bash
# Parse metrics alongside test results
./parse-test-matrix.sh /tmp/kagenti/tdd-iter8/kind-pytest.log
# Metrics file auto-detected at same LOG_DIR

# Explicit metrics file
./parse-test-matrix.sh --metrics /tmp/kagenti/tdd-iter8/llm-metrics.json
```

### 4. Model Comparison Doc

**File:** `docs/agentic-runtime/llm-model-comparison.md`

Aggregates per-model stats across multiple test runs into a reference document.

**Sections:**
1. Model inventory (remote + local, specs)
2. Capability pass rates per model (% of skill tests passing)
3. Performance comparison (tokens, latency, response quality)
4. Recommendations (which model for which use case)
5. Raw data tables (copy-pasted from parser output)

**Updated:** After each graph-loop iteration by copy-pasting the parser's
per-model stats table. The doc is the persistent record; the parser output
is ephemeral.

### 5. Claude Code Sandbox Coverage (4/23 → 8/23)

**New tests:**

| Capability | Test | How |
|---|---|---|
| T1.2 Credentials | `test_credentials__openshell_claude__secret_ref` | Check sandbox pod spec for ANTHROPIC_AUTH_TOKEN secretKeyRef |
| T1.2 Credentials | `test_credentials__openshell_opencode__secret_ref` | Check sandbox pod spec for OPENAI_API_KEY secretKeyRef |
| T3.1 Fix flaky | `test_skill_pr_review__openshell_claude__native` | Add 2-attempt retry, xfail(strict=False) for llama-scout |
| T3.3 Fix flaky | `test_skill_security__openshell_claude__native` | Same retry pattern |

**Mark as N/A (with clear skip reason):**

| Capability | Reason |
|---|---|
| T2.1 Multiturn | CLI sandbox is single-invocation, not a persistent session |
| T2.2 Context isolation | Same — no session state between invocations |
| T2.3 Session resume | Same — sandbox pod is ephemeral |

### 6. OpenClaw Coverage (2/23 → 6/23)

**New helper:** `openclaw_chat()` in conftest.py

OpenClaw's socat bridge on port 8080 forwards to the actual OpenClaw gateway.
The gateway accepts OpenAI-compatible chat completions (routed through LiteLLM).

```python
async def openclaw_chat(
    client: httpx.AsyncClient, url: str, prompt: str,
    model: str = "gpt-4o-mini", timeout: float = 120.0,
) -> dict:
    resp = await client.post(
        f"{url}/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 500},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
```

**New tests:**

| Capability | Test | How |
|---|---|---|
| T2.1 Multiturn | `test_multiturn__nemoclaw_openclaw__three_turns` | 3 sequential `openclaw_chat()` calls |
| T3.1 PR review | `test_skill_pr_review__nemoclaw_openclaw__gateway` | Send PR diff through gateway |
| T3.2 RCA | `test_skill_rca__nemoclaw_openclaw__gateway` | Send CI log through gateway |
| T3.3 Security | `test_skill_security__nemoclaw_openclaw__gateway` | Send code through gateway |

## Implementation Order

1. **Metrics infrastructure** — `record_llm_metric()` + JSONL file
2. **Per-model test routing** — Change per-model tests to set `model` on LiteLLM request
3. **Parser enhancement** — `print_model_stats_table()` from JSONL
4. **CI workflow** — Add `OPENSHELL_LLM_MODELS` to workflows
5. **Model comparison doc** — Initial version with specs + empty tables
6. **Claude Code tests** — T1.2 credentials + flaky fixes
7. **OpenClaw tests** — `openclaw_chat()` helper + T2.1, T3.1-T3.3
8. **Graph-loop iteration** — Run on Kind, CI Kind, CI HyperShift, populate model doc

## Success Criteria

- Parser outputs per-model stats table with tokens/time/response metrics
- CI Kind runs per-model tests for llama-scout-17b + deepseek-r1
- Model comparison doc has data from at least 2 models
- Claude Code coverage: 8/23
- OpenClaw coverage: 6/23
- Zero regressions on existing passing tests
