# .md File CVE Audit - Kagenti

Date: 2026-03-03

## Summary

Searched the entire kagenti repository for CVE references, vulnerability descriptions, and security details in `.md` files and related content. The audit covered:
- All `.md` files in `docs/`, `docs/plans/`, `.claude/skills/`, `.repos/`, root directory
- All `.md` files in `.worktrees/repo-orchestration/`, `.worktrees/cve-awareness/`, `.claude/worktrees/`
- All `.yaml`/`.yml`/`.json`/`.py`/`.go`/`.ts`/`.tsx`/`.js` files for CVE patterns
- Issue template files (`.github/ISSUE_TEMPLATE*`, `.repos/kagenti-extensions/.github/ISSUE_TEMPLATE_EXAMPLE.md`)
- Security advisory URL patterns (NVD, GitHub Advisories, OSV, thehackernews, kaspersky, etc.)
- Git commit messages (Bash tool was unavailable; covered by file-level search)

**Total files with CVE IDs in `.md` files:** 2 (in main repo) + 1 (in cve-awareness worktree, pattern-only)

## Files with CVE References

| File | Line(s) | CVE ID(s) | Content Summary | Risk | Action Needed |
|------|---------|-----------|-----------------|------|---------------|
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 224 | CVE-2026-25253 | References OpenClaw sandbox bypass as contrast to Kagenti's nono design | MEDIUM | Internal design doc; not public-facing. Consider redacting CVE ID if doc is ever published. |
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 494 | CVE-2026-25253 | Links to thehackernews article about OpenClaw control API disabling sandbox | MEDIUM | Same doc. Contains full URL to exploit write-up. |
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 1212 | CVE-2026-25253, CVE-2026-24763 | Detailed OpenClaw failure analysis section with links to kaspersky, thehackernews, infosecurity-magazine, depthfirst, cyberdesserts, cyera | MEDIUM | Most concentrated CVE content. Lists 512 vulnerabilities, 40K exposed instances, 1-click RCE details, supply chain attack details. |
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 1218 | CVE-2026-25253 | Table row: sandbox bypass via API with CVSS 8.8, full exploit description | MEDIUM | Includes attacker technique (`config.patch` to set `tools.exec.host: "gateway"`). |
| `docs/plans/2026-02-23-sandbox-agent-research.md` | 1219 | CVE-2026-24763 | Table row: Docker sandbox escape via PATH manipulation | MEDIUM | Links to kaspersky blog with exploit details. |
| `docs/plans/2026-02-26-coding-agent-variants-research.md` | 449 | CVE-2026-22812 | Notes OpenCode RCE vulnerability fixed in v1.0.216 | MEDIUM | Brief mention in third-party tool assessment. One line. |
| `.worktrees/cve-awareness/.claude/skills/git:commit/SKILL.md` | 79, 89 | CVE-2026-12345 (example) | Uses a fictional CVE ID as pattern example for commit message scanning rules | LOW | Not a real CVE. Used only as regex example. No action needed. |

## Files with Vulnerability Keywords (No Specific CVE IDs)

| File | Line(s) | Content Summary | Risk |
|------|---------|-----------------|------|
| `docs/plans/2026-02-24-context-files-research-synthesis.md` | 71, 191 | Academic research citation: "26.1% of community skills contain vulnerabilities" | LOW |
| `docs/plans/2026-02-24-sandbox-agent-implementation-passover.md` | 76-77 | Generic discussion of kernel vulnerability isolation and exploit surface reduction (gVisor vs Linux) | LOW |
| `docs/plans/2026-02-26-coding-agent-variants-research.md` | 150, 154, 166, 503 | Discussion of Claude's vulnerability scanner finding 500+ unknown vulnerabilities in open-source code | LOW |
| `docs/plans/2026-03-01-sandbox-platform-design.md` | 192, 637 | Generic mention of kernel exploit protection and Trivy vulnerability scan | LOW |
| `TODO_SECURITY_ISSUES.md` | 3860, 3931, 4201, 4260 | Generic security discussion (XSS, container exploits, vulnerability scanning) -- no specific CVEs | LOW |
| `SECURITY.md` | 3, 13, 34 | Standard vulnerability reporting policy | LOW |
| `.repos/scan-reports/*.md` | Various | Recommendations to add SECURITY.md with vulnerability reporting policy | LOW |

## Upstream/Vendor CVE References (Not Kagenti Content)

| File | CVE ID | Context | Risk |
|------|--------|---------|------|
| `charts/gateway-api/crds/gateway-api-1.3.0.yaml` | CVE-2021-25740 | Standard Kubernetes Gateway API CRD comment (upstream content, not Kagenti-authored) | NONE |
| `.claude/worktrees/*/charts/gateway-api/crds/gateway-api-1.3.0.yaml` | CVE-2021-25740 | Same upstream CRD replicated in worktrees | NONE |

## Orchestrator Issue Templates Check

**Result: CLEAN** -- No CVE references found in any issue template files.

Checked locations:
- `.repos/kagenti-extensions/.github/ISSUE_TEMPLATE_EXAMPLE.md` -- Clean (CI/CD workflow template, no security content)
- `.worktrees/repo-orchestration/.claude/skills/repo:issue/SKILL.md` -- Clean (no CVE or vulnerability content)
- `.worktrees/repo-orchestration/.claude/skills/github:issues/SKILL.md` -- Clean
- No `.github/ISSUE_TEMPLATE/` directory exists in the main repo

## Commit Messages with CVE References

| Commit | Message | Risk |
|--------|---------|------|
| (Unable to run `git log --grep` -- Bash tool was unavailable during this audit) | N/A | **MANUAL CHECK REQUIRED**: Run `git log --oneline --all --grep="CVE-" \| head -20` to verify |

## External Security Advisory Links Found

All in `docs/plans/2026-02-23-sandbox-agent-research.md`:

| URL | Context |
|-----|---------|
| `https://thehackernews.com/2026/02/openclaw-bug-enables-one-click-remote.html` | 1-click RCE article |
| `https://www.kaspersky.com/blog/moltbot-enterprise-risk-management/55317/` | Moltbot/OpenClaw vulnerability analysis |
| `https://www.kaspersky.com/blog/openclaw-vulnerabilities-exposed/55263/` | 512 discovered vulnerabilities |
| `https://www.infosecurity-magazine.com/news/researchers-40000-exposed-openclaw/` | 40K exposed instances |
| `https://depthfirst.com/post/1-click-rce-to-steal-your-moltbot-data-and-keys` | RCE exploit details |
| `https://blog.cyberdesserts.com/openclaw-malicious-skills-security/` | ClawHavoc supply chain attack |
| `https://www.cyera.com/research-labs/the-openclaw-security-saga-how-ai-adoption-outpaced-security-boundaries` | Comprehensive security analysis |

## Recommendations

### Priority 1 -- Review before any public publication

1. **`docs/plans/2026-02-23-sandbox-agent-research.md`** (lines 1208-1227): This section contains the most concentrated security-sensitive content -- specific CVE IDs (CVE-2026-25253, CVE-2026-24763), CVSS scores, attacker techniques, exploit URLs, and counts of exposed instances. If this document is ever published (e.g., as a blog post or public design document), these details should be abstracted. The file is currently in `docs/plans/` which appears to be internal-only, so the risk is MEDIUM rather than HIGH.

2. **`docs/plans/2026-02-26-coding-agent-variants-research.md`** (line 449): Single-line CVE-2026-22812 reference about a third-party tool (OpenCode). Low information density but still a specific CVE ID that should be removed if the doc becomes public.

### Priority 2 -- Verify via git log

3. **Run `git log --oneline --all --grep="CVE-" | head -20`** manually to check for CVE references in commit messages. Commit messages are visible in the public GitHub repository and cannot be retroactively cleaned without history rewriting.

### Priority 3 -- No action needed

4. **`.worktrees/cve-awareness/.claude/skills/git:commit/SKILL.md`**: Uses `CVE-2026-12345` as a fictional example in a regex pattern. This is the *prevention* skill itself -- it teaches Claude to avoid leaking CVEs. No action needed.

5. **Upstream Gateway API CRD files** (`charts/gateway-api/crds/gateway-api-1.3.0.yaml`): CVE-2021-25740 is a standard upstream Kubernetes comment. No action needed.

6. **Generic vulnerability/exploit keyword mentions** in research docs: These discuss security concepts without specific CVE IDs. No action needed.

### Key Finding

**No HIGH-risk findings.** The repo-orchestration worktree (PR #691) does NOT contain CVE references in its issue templates or skill files. The CVE content is confined to two internal design documents in `docs/plans/` that reference third-party vulnerabilities (OpenClaw, OpenCode) for comparative analysis purposes. These are not Kagenti's own vulnerabilities and the docs appear to be internal planning documents, not public-facing content.
