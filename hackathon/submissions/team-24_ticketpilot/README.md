# Team 24 — TicketPilot

## Participants & Work Division

| Name | Role | Owns |
|------|------|------|
| Rakesha | PM | Project coordination, README, demo script, "How We Used Claude Code" |
| Avinash | Architect | `models.py`, `tools.py` (tool stubs + schemas), `agent.py` core loop, `SYSTEM_PROMPT` |
| Chinmaya | Architect | MCP server design, `tools.py` (MCP integration), ADR-001, architecture diagrams |
| Srinath | Dev + Infra | `guardrails.py`, `hitl.py`, `main.py`, `requirements.txt`, `.env.example`, Docker/run setup |
| Shubham | Dev | `observability.py` (SQLite store), `eval_harness.py`, `agent.py` `_parse_decision()` |
| Gaurav | Tester | `tests/test_guardrails.py`, `tests/test_hitl.py`, GOLDEN_DATASET (normal tickets) |
| Kavya | Tester | `tests/test_eval.py`, GOLDEN_DATASET (adversarial tickets), scorecard validation |

## Scenario
Scenario 5: Domain IT helpdesk

## What We Built

**TicketPilot** — an autonomous IT helpdesk intake agent that ingests support requests via email, Slack, web form, and ticketing API. It classifies, enriches, and routes each ticket, and resolves a narrow set of cases (password resets, MFA re-enrollment, known-fix FAQ) directly — without human triage intervention.

**Problem solved:** Average time-to-first-response was 4.2 hours. 61% of tickets were routine requests requiring no specialist judgment. TicketPilot reclaims ~40% of a triager's day spent on intake admin.

### Agent Mandate Summary

**Autonomous Zone** (agent decides alone):
- P1–P4 priority classification on all tickets
- Queue routing to: NetworkOps, EndpointSupport, AccountsAccess, AppSupport, SecurityOps, ServiceDesk
- Auto-resolve password resets (verified employee, no anomaly flags)
- Auto-resolve MFA re-enrollment (verified employee, device not flagged)
- Auto-resolve known software FAQ (confidence ≥ 0.90, no data involved)
- Duplicate detection and merging (>0.85 similarity)
- Ticket enrichment (dept, asset info, prior history, system status)

**Human-Required Zone** (agent prepares dossier, escalates):
- Tickets touching user data deletion/export (GDPR/CCPA)
- Requester acting on behalf of another user (impersonation risk)
- P1 incidents (service down, >10 users impacted)
- Any mention of security breach, data leak, ransomware
- Contractors, vendors, or external parties
- Actions requiring elevated privilege
- Classification confidence < 0.70
- Sanctions/fraud/watchlist matches
- P1/P2 outside business hours → page on-call directly

**Hard Stops** (log and immediately route to human, no autonomous action):
- Termination-adjacent requests ("Remove access for [name]")
- Legal hold data / eDiscovery references
- Executive accounts (C-suite, Board)
- Bulk credential operations (>5 accounts)
- Network perimeter changes (firewall, VPN, DNS)
- Audit log access or modification
- Suspected prompt injection attempts
- Requests to modify the agent's own config or permissions

### Confidence Thresholds
| Band | Action |
|---|---|
| ≥ 0.90 | Autonomous action permitted |
| 0.70 – 0.89 | Route + attach reasoning; human confirms |
| < 0.70 | Full escalation; human decides everything |

### Agent Identity & Permissions
Runs under a least-privilege service account with: read-only HRIS, read/write to ticketing system, read-only CMDB, scoped IdP API (password + MFA reset only), read-only KB. No access to email archives, financial systems, or production databases.

## Challenges Attempted
| # | Challenge | Status | Notes |
|---|---|---|---|
| 1 | Autonomous triage & routing | done | |
| 2 | Auto-resolve password reset / MFA | done | |
| 3 | Confidence-based escalation | done | |
| 4 | Prompt injection detection & quarantine | done | |
| 5 | Audit trail / explainability traces | done | |

## Key Decisions

Full architecture decision record: [ADR-001 — TicketPilot Agent Architecture](decisions/ADR-001-ticketpilot-agent-architecture.md)

- **Narrow autonomous zone by design** — only high-volume, low-risk actions (password reset, MFA, FAQ) are auto-resolved. Everything touching data, security, or privilege goes to a human. Reduces blast radius of any misclassification.
- **Per-decision confidence, not per-ticket** — a ticket can be auto-routed (high confidence) but require human approval for resolution (lower confidence). Finer-grained than a single ticket score.
- **Hard stops are unconditional** — no confidence score overrides a hard stop. Prompt injection, exec accounts, bulk credential ops always land with a human. Simplicity over cleverness.
- **Agent is not the system of record** — the ticketing system remains authoritative. All agent actions are reversible by any authorized human.

## How to Run It

Assumes Docker and nothing else.

```bash
# 1. Clone and enter the submission folder
git clone <repo-url>
cd hackathon/submissions/team-24_ticketpilot

# 2. Build the Docker image
docker build -t ticketpilot .

# 3. Set your API key
export ANTHROPIC_API_KEY=your_key_here

# 4. Run sample triage (3 demo tickets)
docker run --rm -e ANTHROPIC_API_KEY ticketpilot python -m ticketpilot.main triage

# 5. Run the eval harness (no API key needed — uses mock agent)
docker run --rm ticketpilot python -m ticketpilot.main eval

# 6. Run with real agent
docker run --rm -e ANTHROPIC_API_KEY ticketpilot python -m ticketpilot.main eval --real-agent

# 7. View decision dashboard
docker run --rm ticketpilot python -m ticketpilot.main dashboard

# 8. Run tests
docker run --rm ticketpilot pytest ticketpilot/tests/ -v
```

**Without Docker (Python 3.10+):**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY
python -m ticketpilot.main triage
```

**Environment variables (`.env.example`):**
```
ANTHROPIC_API_KEY=your_key_here
TICKETPILOT_DB_PATH=ticketpilot_obs.db
TICKETPILOT_LOG_LEVEL=INFO
```

## SDK Integration Note

`claude-agent-sdk` (`query()`) runs the Claude Code agent loop. It does **not** accept custom tool schemas directly. Our custom tools (`lookup_kb`, `lookup_employee`, etc.) are exposed in two ways:
- As an **MCP server** (`tools/mcp_server.py`) — registered via `ClaudeAgentOptions.mcp_servers`
- The **HITL permission hook** maps to `ClaudeAgentOptions.can_use_tool`

```python
options = ClaudeAgentOptions(
    system_prompt=SYSTEM_PROMPT,
    allowed_tools=["mcp__ticketpilot__lookup_kb", "mcp__ticketpilot__lookup_employee", ...],
    can_use_tool=hitl_hook.can_use_tool,
    max_turns=12,
)
async for message in query(prompt=ticket_prompt, options=options):
    if isinstance(message, AssistantMessage):
        # collect reasoning
    elif isinstance(message, ResultMessage):
        # parse final decision from message.result
```

## If We Had Another Day

1. **Replace stub tools with real integrations** — `lookup_employee` against a real HRIS (Workday/BambooHR), `lookup_kb` against a vector-search KB (Confluence/Notion). Currently returns hardcoded dicts.
2. **Calibrate confidence scores** — the model self-reports confidence; run 200 historical tickets to build a calibration curve. Current thresholds (0.90 / 0.70) are heuristics.
3. **Feedback loop from HITL decisions** — when a human overrides the agent's routing, feed that delta back into the system prompt as few-shot examples. The `observability.py` `get_feedback_patterns()` is wired for this but nothing consumes it yet.
4. **Streaming approval surface** — `interactive_approval_surface()` is terminal-only. A real deployment needs a Slack bot or web UI for human approvers.
5. **Multi-tenant routing** — currently one queue list hardcoded. Should be configurable per org.

## How We Used Claude Code

**What worked best:**
- Scaffolding all 8 modules in one shot with Prompt 1 — Claude Code generated correct dataclass field defaults, realistic tool stub return values, and the SQLite schema without us specifying column types.
- The guardrails regex patterns — we described the attack categories and Claude Code produced patterns that caught edge cases (Unicode lookalikes, mixed-case variants) we hadn't thought of.
- Test generation — giving Claude Code the module and saying "write pytest cases for every branch" produced 18 of 19 tests correctly on the first pass.

**What surprised us:**
- Claude Code flagged that `datetime.utcnow()` is removed in Python 3.14 (our local env) before we ran into the runtime error. Caught it at write time.
- It caught that `input_schema` (not `parameters`) is the correct key for Anthropic tool schemas — a common mistake when coming from OpenAI's API.

**Where it saved the most time:**
- The 35-ticket GOLDEN_DATASET — manually writing adversarial test cases with realistic attack payloads would have taken 2–3 hours. Claude Code did it in one prompt with correct expected labels.
- `observability.py` — the full SQLite schema with indexes, the `_process_feedback()` upsert logic, and `replay_ticket()` formatter came out complete and correct. That module alone would have been 90 minutes of manual work.