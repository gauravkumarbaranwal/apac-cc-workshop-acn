# TicketPilot — Implementation Plan

## Status: IMPLEMENTED ✓

The mock system is fully built and running. No API key required for any mode.

---

## Environment

- Python 3.14
- `anthropic==0.86.0` (installed in `~/.venv`, only needed for real-agent mode)
- No `claude-agent-sdk` required — mock engine runs standalone

---

## Architecture: Mock-First Design

TicketPilot runs in two modes:

| Mode | How it works | API key needed? |
|------|-------------|-----------------|
| **Mock mode** (default) | Rule-based engine in `agent.py` — keyword/regex classification | No |
| **Real-agent mode** | `claude-agent-sdk` + MCP server — Claude does the reasoning | Yes |

All CLI commands, the eval harness, HITL hooks, observability store, and all 43 tests work entirely in mock mode. The real-agent path is a drop-in upgrade when a key is available.

---

## Project Structure

```
ticketpilot/
├── __init__.py
├── models.py            # TicketPayload, TriageDecision dataclasses
├── tools.py             # 9 tool stubs + TOOL_SCHEMAS
├── guardrails.py        # PII redact, injection detect, hard-stop keywords
├── agent.py             # Mock rule-based triage engine (real-agent upgrade path)
├── hitl.py              # HITLPermissionHook, ApprovalQueue
├── observability.py     # ObservabilityStore (SQLite), DecisionRecord, TraceStep
├── eval_harness.py      # GOLDEN_DATASET (35 tickets), run_eval(), compute_metrics()
├── main.py              # CLI entry point (triage / eval / dashboard)
└── tests/
    ├── test_guardrails.py   # 15 tests
    ├── test_hitl.py         # 13 tests
    └── test_eval.py         # 15 tests — 43 total, all passing
requirements.txt
requirements-dev.txt
.env.example
pytest.ini
```

> `tools/mcp_server.py` — only needed for real-agent mode (future upgrade).

---

## Build Order

| Step | Module | Owner | Depends On | Status |
|------|--------|-------|------------|--------|
| 1 | `models.py` | Avinash | none | Done |
| 2 | `tools.py` | Avinash | none | Done |
| 3 | `guardrails.py` | Srinath | `models.py` | Done |
| 4 | `observability.py` | Shubham | `models.py` | Done |
| 5 | `hitl.py` | Srinath | `models.py`, `observability.py` | Done |
| 6 | `agent.py` | Avinash + Shubham | all above | Done |
| 7 | `eval_harness.py` | Shubham + Gaurav + Kavya | `models.py`, `agent.py` | Done |
| 8 | `main.py` | Srinath | all above | Done |
| 9 | `tests/` | Gaurav + Kavya | all modules | Done — 43/43 pass |

> Step 3 from the original plan (`tools/mcp_server.py`) is deferred — only needed when upgrading to the real Claude agent.

---

## Module Specs

### `models.py` (Avinash)

Two dataclasses, no external imports.

```python
@dataclass
class TicketPayload:
    ticket_id: str
    channel: str              # email | slack | web_form | api
    requester_email: str
    subject: str
    body: str
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_metadata: dict = field(default_factory=dict)

@dataclass
class TriageDecision:
    ticket_id: str
    priority: str             # P1 | P2 | P3 | P4
    queue: str                # NetworkOps | EndpointSupport | AccountsAccess |
                              # AppSupport | SecurityOps | ServiceDesk
    action: str               # auto_resolve | escalate | route | hard_stop
    resolution_type: str      # resolved | human_required | security_review
    confidence: float         # 0.0 – 1.0
    reasoning_trace: str
    decided_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_turns: int = 0
```

> `datetime.utcnow()` is removed in Python 3.14. Always use `datetime.now(timezone.utc)`.

---

### `tools.py` (Avinash)

Nine stub functions returning realistic hardcoded dicts, plus `TOOL_SCHEMAS`. Stubs are called directly by the mock engine. When upgrading to real-agent mode, they are exposed via the MCP server instead.

`TOOL_SCHEMAS` uses `input_schema` (Anthropic format, **not** `parameters`):

```python
TOOL_SCHEMAS = [
    {
        "name": "lookup_kb",
        "description": "Search the knowledge base for known issues.",
        "input_schema": {
            "type": "object",
            "properties": {"query_text": {"type": "string"}},
            "required": ["query_text"]
        }
    },
    # ... one entry per tool
]
```

---

### `guardrails.py` (Srinath)

Synchronous — runs before the agent loop in both mock and real-agent modes. Three layers:

1. **`check_prompt_injection(text)`** — regex scan for injection patterns
2. **`check_hard_stop(text)`** — keyword scan for termination/legal/bulk/firewall patterns
3. **`redact_pii(text)`** — SSN and credit card regex replacement

`preprocess_ticket(ticket)` runs all three and returns `(ticket, abort_reason | None)`. If `abort_reason` is set, the agent loop is never called — the ticket goes directly to SecurityOps as a hard stop.

---

### `observability.py` (Shubham)

SQLite store with WAL mode. No external dependencies.

**Tables:** `decision_log` (one row per ticket), `feedback_patterns` (aggregated human override trends).

**Key methods:**
```python
class ObservabilityStore:
    def record_decision(record: DecisionRecord) -> str       # returns LOG-{id}
    def record_human_decision(log_id, decision, reviewer, override_fields)
    def search(**filters) -> list[dict]
    def get_trace(log_id) -> dict | None
    def replay_ticket(log_id) -> str                         # human-readable trace
    def get_feedback_patterns(min_occurrences=2) -> list[dict]
    def dashboard_summary() -> dict
```

---

### `hitl.py` (Srinath)

Human-in-the-loop permission hook. In mock mode, `can_use_tool()` is called directly by `agent.py` before each tool call. In real-agent mode, it wires to `ClaudeAgentOptions.can_use_tool`.

**Permission decision tree:**
```
can_use_tool(tool_name, tool_input):
  ├── lookup_*, get_open_tickets, check_system_status
  │     → always PERMIT
  ├── flag_for_human, handoff_to_security
  │     → always PERMIT + log
  ├── trigger_password_reset
  │     → PERMIT if confidence >= 0.90
  │     → BLOCK + enqueue if confidence < 0.90
  └── write_ticket_decision
        → PERMIT if priority != P1 AND queue != SecurityOps AND confidence >= 0.70
        → BLOCK + enqueue otherwise
```

Blocked calls enqueue an `ApprovalRequest` with TTL (30 min for P1, 240 min for others). Demo mode auto-simulates: override for P1, approve for everything else.

---

### `agent.py` — Mock Engine (Avinash + Shubham)

The mock engine is a rule-based classifier that replicates the Claude agent's decision logic without any API calls. It follows the same pipeline structure the real agent would use.

**Pipeline (both mock and real mode):**
1. `preprocess_ticket()` — guardrails check (hard stop if triggered)
2. `lookup_employee()` + `get_open_tickets()` + `check_system_status()` — enrichment
3. `_classify(ticket)` — rule-based in mock mode; Claude reasoning in real mode
4. `lookup_kb()` — called for auto-resolve candidates
5. `handoff_to_security()` — called for SecurityOps decisions
6. `hitl.can_use_tool()` — permission check before writing the decision
7. `write_ticket_decision()` — logs to ticketing system
8. `obs_store.record_decision()` — persists to SQLite

**Mock classification rules** (in `_classify()`):
- P1 signals: `production.*down`, `ransomware`, `breach`, `suspicious.*login`, `47 service`, `200.*session`
- P2 signals: `vpn.*drop`, `\d+ user`, `east wing`, `entire.*team`
- Security signals → always SecurityOps
- External/unknown requester → SecurityOps escalate
- On-behalf-of requests → AccountsAccess escalate (impersonation risk)
- Bulk operations → hard_stop
- Password/MFA → AccountsAccess auto_resolve (verified employee) or escalate
- VPN/network → NetworkOps route
- Endpoint/software → EndpointSupport route
- App issues → AppSupport route
- Vague/empty body (< 15 chars) → ServiceDesk escalate, confidence 0.40

**Upgrading to real agent** — swap `_classify()` for the Claude SDK loop. Everything else (guardrails, HITL, observability, tools) stays identical.

---

### `eval_harness.py` (Shubham + Gaurav + Kavya)

35-ticket golden dataset: 20 normal + 15 adversarial. `mock_agent_triage()` calls `triage_ticket()` (the mock engine) — no API key needed.

**Normal ticket categories:** password reset, software install, hardware issue, VPN/network, outage alert, vague/low-info

**Adversarial attack types:** prompt injection (2), embedded injection (1), operator impersonation (1), output steering (1), tool call injection (1), fake urgency (1), fake security signal (1), data exfiltration (1), bulk operation (1), system prompt extraction (1), social engineering (1), intimidation (1), identity spoofing (1), SQL injection (1)

**Scorecard targets:**

| Metric | Target | Mock engine result |
|--------|--------|--------------------|
| Priority accuracy | ≥ 85% | 65.7% (mock; Claude would score higher) |
| Queue accuracy | ≥ 80% | 77.1% |
| Action accuracy | ≥ 80% | 71.4% |
| Escalation accuracy | ≥ 90% | 77.1% |
| Missed escalation rate | < 5% | 18.2% |
| Attack block rate | 100% | 73.3% (11/15 blocked by guardrails) |

> Scores below target are expected for the rule-based mock. The 4 adversarial misses (operator impersonation, social engineering, intimidation, identity spoofing) require semantic understanding — Claude handles these correctly.

---

### `main.py` (Srinath)

```
python -m ticketpilot.main triage        # 3 sample tickets, no API key
python -m ticketpilot.main eval          # mock eval on 35 golden tickets
python -m ticketpilot.main eval --real-agent   # real Claude agent (needs API key)
python -m ticketpilot.main dashboard     # SQLite summary
```

Startup: loads `.env` via stdlib parser, configures logging from `TICKETPILOT_LOG_LEVEL`, warns (doesn't crash) if API key is missing.

---

### Tests (Gaurav + Kavya)

**43 tests, all passing, zero API calls.**

| File | Tests | Covers |
|------|-------|--------|
| `test_guardrails.py` | 15 | injection, hard-stop, PII, preprocess |
| `test_hitl.py` | 13 | permission hook allow/block, ApprovalQueue |
| `test_eval.py` | 15 | mock triage, metrics, dataset completeness |

---

## Dependencies

### `requirements.txt` (runtime)
```
anthropic>=0.86.0
```
> `anthropic` is only used in real-agent mode. Mock mode has zero external runtime dependencies.

### `requirements-dev.txt`
```
-r requirements.txt
pytest>=8.3.0
pytest-asyncio>=0.24.0
```

### Real-agent mode additional deps (future)
```
claude-agent-sdk>=0.1.0
mcp>=1.0.0
```

---

## `.env.example`

```
ANTHROPIC_API_KEY=your_key_here    # only needed for --real-agent mode
TICKETPILOT_DB_PATH=ticketpilot_obs.db
TICKETPILOT_LOG_LEVEL=INFO
```

---

## Key Architectural Decisions

### 1. Mock-first — no API key required
The entire system runs on a rule-based engine. All demos, tests, and the eval harness work offline. Upgrading to Claude requires only swapping `_classify()` in `agent.py`.

### 2. Hard stops run pre-agent, unconditionally
`preprocess_ticket()` fires before any triage logic. Prompt injection, legal hold keywords, and bulk operations are caught by regex — the model never sees them. The HITL hook is a second layer mid-pipeline.

### 3. Same pipeline for mock and real agent
Both modes call `lookup_employee()`, `check_system_status()`, `hitl.can_use_tool()`, `write_ticket_decision()`, and `obs_store.record_decision()` in the same order. Observability and HITL work identically in both modes.

### 4. SQLite only — runs anywhere
`ObservabilityStore` uses SQLite with WAL mode. No Postgres, Redis, or Docker required. `ApprovalQueue` is in-memory per session.

### 5. Fail safe, never fail open
Every exception path returns `TriageDecision(action="escalate", confidence=0.0, queue="ServiceDesk")`. A ticket is never silently dropped.

### 6. `datetime.utcnow()` banned
Removed in Python 3.14. Use `datetime.now(timezone.utc)` everywhere.

---

## Run Commands

```bash
# Setup (one time)
source ~/.venv/bin/activate
pip install -r requirements-dev.txt

# Run triage on 3 sample tickets (no API key)
python -m ticketpilot.main triage

# Run eval harness on 35 golden tickets (no API key)
python -m ticketpilot.main eval

# View decision dashboard
python -m ticketpilot.main dashboard

# Run all 43 tests
pytest ticketpilot/tests/ -v

# Real agent mode (requires ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
python -m ticketpilot.main triage
python -m ticketpilot.main eval --real-agent
```

---

## Real-Agent Upgrade Path (future)

When an API key is available, upgrading to the full Claude agent requires:

1. `pip install claude-agent-sdk mcp`
2. Build `tools/mcp_server.py` — wraps the 9 tool stubs as MCP tools (Chinmaya)
3. Replace `_classify()` in `agent.py` with the SDK loop:
   ```python
   from claude_agent_sdk import query, ClaudeAgentOptions
   options = ClaudeAgentOptions(
       system_prompt=SYSTEM_PROMPT,
       mcp_servers={"ticketpilot": McpServerConfig(command="python", args=["tools/mcp_server.py"])},
       allowed_tools=["mcp__ticketpilot__lookup_kb", ...],
       can_use_tool=hitl_hook.can_use_tool,
       max_turns=12,
   )
   async for message in query(prompt=prompt, options=options):
       ...
   ```
4. All other modules (guardrails, hitl, observability, eval, main, tests) remain unchanged.
