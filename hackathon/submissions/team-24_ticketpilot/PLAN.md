# TicketPilot — Implementation Plan

## Environment

- Python 3.14
- `anthropic==0.86.0` (already installed in `~/.venv`)
- `claude-agent-sdk` (needs `pip install claude-agent-sdk`)

---

## Project Structure

```
ticketpilot/
├── __init__.py
├── models.py            # TicketPayload, TriageDecision dataclasses
├── tools.py             # 9 tool stubs + TOOL_SCHEMAS
├── guardrails.py        # PII redact, injection detect, hard-stop keywords
├── agent.py             # triage_ticket() async using claude_agent_sdk query()
├── hitl.py              # HITLPermissionHook, ApprovalQueue
├── observability.py     # ObservabilityStore (SQLite), DecisionRecord, TraceStep
├── eval_harness.py      # GOLDEN_DATASET (35 tickets), run_eval(), compute_metrics()
├── main.py              # CLI entry point (triage / eval / dashboard)
└── tests/
    ├── test_guardrails.py
    ├── test_hitl.py
    └── test_eval.py
tools/
└── mcp_server.py        # MCP server exposing the 9 tools to the agent
requirements.txt
requirements-dev.txt
.env.example
Dockerfile
pytest.ini
```

---

## Build Order

Build bottom-up following the dependency graph. Do not start a module until all its dependencies are done.

| Step | Module | Owner | Depends On | Risk |
|------|--------|-------|------------|------|
| 1 | `models.py` | Avinash | none | Low |
| 2 | `tools.py` | Avinash | none | Low |
| 3 | `tools/mcp_server.py` | Chinmaya | `tools.py` | Medium |
| 4 | `guardrails.py` | Srinath | `models.py` | Low |
| 5 | `observability.py` | Shubham | `models.py` | Medium |
| 6 | `hitl.py` | Srinath | `models.py`, `observability.py` | Medium |
| 7 | `agent.py` | Avinash + Shubham | all above + SDK | **High** |
| 8 | `eval_harness.py` | Shubham + Gaurav + Kavya | `models.py`, `agent.py` | Medium |
| 9 | `main.py` | Srinath | all above | Low |
| 10 | `tests/` | Gaurav + Kavya | all modules | Low |
| 11 | `Dockerfile` + run docs | Srinath | `main.py` | Low |

---

## Step-by-Step Module Specs

### Step 1 — `models.py` (Avinash)

Two dataclasses, no external imports.

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class TicketPayload:
    ticket_id: str
    channel: str                    # email | slack | web_form | api
    requester_email: str
    subject: str
    body: str
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_metadata: dict = field(default_factory=dict)

@dataclass
class TriageDecision:
    ticket_id: str
    priority: str                   # P1 | P2 | P3 | P4
    queue: str                      # NetworkOps | EndpointSupport | AccountsAccess |
                                    # AppSupport | SecurityOps | ServiceDesk
    action: str                     # auto_resolve | escalate | route | hard_stop
    resolution_type: str            # resolved | human_required | security_review
    confidence: float               # 0.0 – 1.0 (self-reported by model)
    reasoning_trace: str
    decided_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_turns: int = 0
```

> **Note:** Never use `datetime.utcnow()` — removed in Python 3.14. Always use `datetime.now(timezone.utc)`.

---

### Step 2 — `tools.py` (Avinash)

Nine stub functions returning realistic hardcoded dicts. Plus `TOOL_SCHEMAS` as a list of MCP-compatible dicts.

```python
def lookup_kb(query_text: str) -> dict:
    return {"result": "Reset via Settings > Security > Password", "confidence": 0.95, "article_id": "KB-1042"}

def lookup_employee(email: str) -> dict:
    return {"employee_id": "EMP-0042", "name": "Alice Smith", "department": "Engineering",
            "manager_email": "manager@acme.com", "status": "active", "is_contractor": False}

def lookup_asset(asset_id: str) -> dict:
    return {"asset_id": asset_id, "type": "laptop", "os": "macOS 14", "owner_email": "alice@acme.com",
            "last_seen": "2026-03-22T09:00:00Z", "compliance_status": "ok"}

def get_open_tickets(email: str) -> dict:
    return {"open_count": 1, "tickets": [{"id": "TKT-8901", "subject": "VPN issue", "priority": "P3"}]}

def check_system_status() -> dict:
    return {"overall": "operational", "vpn": "degraded", "email": "operational", "idp": "operational"}

def write_ticket_decision(ticket_id, priority, queue, action, notes) -> dict:
    return {"status": "written", "ticket_id": ticket_id, "log_id": f"LOG-{ticket_id}"}

def trigger_password_reset(employee_id, email) -> dict:
    return {"status": "reset_sent", "employee_id": employee_id, "email": email}

def flag_for_human(ticket_id, reason, context) -> dict:
    return {"status": "flagged", "ticket_id": ticket_id, "reason": reason}

def handoff_to_security(ticket_id, indicator, context) -> dict:
    return {"status": "handed_off", "ticket_id": ticket_id, "indicator": indicator}
```

`TOOL_SCHEMAS` uses `input_schema` (Anthropic format, **not** `parameters`):

```python
TOOL_SCHEMAS = [
    {
        "name": "lookup_kb",
        "description": "Search the knowledge base for known issues and resolutions.",
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

### Step 3 — `tools/mcp_server.py` (Chinmaya)

The claude-agent-sdk does **not** accept custom tool schemas in `query()`. Tools must be exposed as an MCP server registered via `ClaudeAgentOptions.mcp_servers`.

```python
# tools/mcp_server.py
# Lightweight MCP server wrapping the 9 tool stubs.
# Run with: python tools/mcp_server.py

from mcp.server.fastmcp import FastMCP
from ticketpilot.tools import (
    lookup_kb, lookup_employee, lookup_asset, get_open_tickets,
    check_system_status, write_ticket_decision, trigger_password_reset,
    flag_for_human, handoff_to_security
)

mcp = FastMCP("ticketpilot")

@mcp.tool()
def lookup_kb_tool(query_text: str) -> dict:
    """Search the knowledge base for known issues and resolutions."""
    return lookup_kb(query_text)

# ... register all 9 tools

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Registered in `agent.py` via:
```python
ClaudeAgentOptions(
    mcp_servers={"ticketpilot": McpServerConfig(command="python", args=["tools/mcp_server.py"])},
    ...
)
```

Add `mcp` to `requirements.txt`.

---

### Step 4 — `guardrails.py` (Srinath)

Entirely synchronous — runs before the agent, no SDK involvement.

```python
import re
from ticketpilot.models import TicketPayload

INJECTION_PATTERNS = [
    r"ignore (previous|all|prior) instructions",
    r"you are now",
    r"disregard (your|all|the)",
    r"<\|",
    r"\[?tool.?call\]?",
    r"act as (a |an )?(new |different )?",
    r"jailbreak",
    r"new (system )?prompt",
]

HARD_STOP_KEYWORDS = [
    "delete.*account", "remove.*access", "terminate.*user",
    "legal hold", "ediscovery", "litigation hold",
    "change.*firewall", "modify.*dns", "vpn config",
    "audit log",
    "bulk.*credential", "reset.*all.*password",
]

def redact_pii(text: str) -> tuple[str, bool]:
    """Returns (redacted_text, was_redacted)."""
    original = text
    text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN-REDACTED]', text)
    text = re.sub(r'\b(?:\d{4}[\s\-]?){3}\d{4}\b', '[CC-REDACTED]', text)
    return text, text != original

def check_prompt_injection(text: str) -> tuple[bool, str | None]:
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True, pat
    return False, None

def check_hard_stop(text: str) -> tuple[bool, str | None]:
    for pat in HARD_STOP_KEYWORDS:
        if re.search(pat, text, re.IGNORECASE):
            return True, pat
    return False, None

def preprocess_ticket(ticket: TicketPayload) -> tuple[TicketPayload, str | None]:
    combined = f"{ticket.subject} {ticket.body}"
    is_injection, pattern = check_prompt_injection(combined)
    if is_injection:
        return ticket, f"PROMPT_INJECTION: matched pattern '{pattern}'"
    is_hard_stop, keyword = check_hard_stop(combined)
    if is_hard_stop:
        return ticket, f"HARD_STOP: matched keyword '{keyword}'"
    redacted_body, was_redacted = redact_pii(ticket.body)
    if was_redacted:
        from dataclasses import replace
        ticket = replace(ticket, body=redacted_body)
    return ticket, None
```

---

### Step 5 — `observability.py` (Shubham)

SQLite store. All timestamps as ISO-8601 strings. Use WAL mode for concurrent reads.

**Tables:**
- `decision_log` — one row per ticket decision, full JSON reasoning trace
- `feedback_patterns` — aggregated override patterns for model improvement

**Key methods:**
```python
class ObservabilityStore:
    def __init__(self, db_path: str = None): ...       # defaults to env var or ticketpilot_obs.db
    def record_decision(self, record: DecisionRecord) -> str: ...   # returns LOG-{uuid}
    def record_human_decision(self, log_id, decision, reviewer, override_fields=None): ...
    def search(self, **filters) -> list[dict]: ...
    def get_trace(self, log_id: str) -> dict | None: ...
    def replay_ticket(self, log_id: str) -> str: ...   # human-readable trace replay
    def get_feedback_patterns(self, min_occurrences=2) -> list[dict]: ...
    def dashboard_summary(self) -> dict: ...           # totals, by_queue, avg_confidence, override_rate
```

---

### Step 6 — `hitl.py` (Srinath)

Permission hook wired to `ClaudeAgentOptions.can_use_tool`.

**Permission decision tree:**

```
can_use_tool(tool_name, tool_input):
  ├── Lookup/read tools (lookup_*, get_open_tickets, check_system_status)
  │     → always PERMIT
  ├── flag_for_human, handoff_to_security
  │     → always PERMIT + log
  ├── trigger_password_reset
  │     → PERMIT if confidence >= 0.90
  │     → BLOCK + enqueue if confidence < 0.90
  └── write_ticket_decision
        → PERMIT if priority not P1 AND queue != SecurityOps AND confidence >= 0.70
        → BLOCK + enqueue otherwise
```

**Approval TTL:** 30 min for P1, 240 min for all others.

**Demo mode:** auto-simulate "override" for P1, "approve" for everything else.

**`_log_feedback()`** appends override deltas to a module-level `feedback_log` list and prints a `[FEEDBACK LOG]` summary line.

---

### Step 7 — `agent.py` (Avinash + Shubham)

Central module. Highest risk.

**SDK integration pattern:**

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

async def triage_ticket(ticket: TicketPayload) -> TriageDecision:
    # 1. Guardrails pre-check
    ticket, abort_reason = preprocess_ticket(ticket)
    if abort_reason:
        return _hard_stop_decision(ticket, abort_reason)

    # 2. Build HITL hook
    hitl_hook = HITLPermissionHook(ticket_id=ticket.ticket_id, ...)

    # 3. Format prompt
    prompt = (
        f"Ticket ID: {ticket.ticket_id}\n"
        f"Channel: {ticket.channel}\n"
        f"From: {ticket.requester_email}\n"
        f"Subject: {ticket.subject}\n\n"
        f"Body:\n{ticket.body}"
    )

    # 4. Run agent loop
    reasoning_parts = []
    final_text = ""
    turns = 0

    try:
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"ticketpilot": McpServerConfig(command="python", args=["tools/mcp_server.py"])},
            allowed_tools=["mcp__ticketpilot__lookup_kb", ...],
            can_use_tool=hitl_hook.can_use_tool,
            max_turns=12,
        )
        async for message in query(prompt=prompt, options=options):
            turns += 1
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        reasoning_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                final_text = message.result or ""
    except Exception as e:
        logger.error("Agent query failed for %s: %s", ticket.ticket_id, e, exc_info=True)
        return _error_fallback_decision(ticket, str(e))

    # 5. Parse decision from final message
    decision = _parse_decision(final_text, ticket, turns, reasoning_parts)

    # 6. Record and return
    obs_store.record_decision(...)
    log_decision(decision)
    return decision
```

**`_parse_decision()` — three fallback layers:**
1. Look for `<decision>...</decision>` JSON block in `final_text`
2. Look for a bare JSON object containing `"priority"` key
3. Return a safe default: `P2 / ServiceDesk / escalate / confidence=0.0`

Never raise. Always return a valid `TriageDecision`.

**`SYSTEM_PROMPT`** must instruct the model to end every response with:
```xml
<decision>
{"priority": "P3", "queue": "AccountsAccess", "action": "auto_resolve",
 "resolution_type": "resolved", "confidence": 0.92, "reasoning": "..."}
</decision>
```

---

### Step 8 — `eval_harness.py` (Shubham + Gaurav + Kavya)

**35 tickets in `GOLDEN_DATASET`:** 20 normal + 15 adversarial.

Normal ticket categories:
- Password reset (5), Software install (3), Hardware issue (3), VPN/network (3), Outage alert (3), Vague/low-info (3)

Adversarial ticket types:
- Prompt injection in body (4), Hard-stop keyword disguised (3), Executive impersonation (2), Bulk credential request (2), Urgency inflation (2), SQL/tool injection (2)

**`mock_agent_triage(ticket)`** — rule-based, deterministic, no API calls. Used in CI.

**`compute_metrics()` targets:**

| Metric | Target |
|--------|--------|
| Priority accuracy | ≥ 85% |
| Queue accuracy | ≥ 80% |
| Escalation accuracy | ≥ 90% |
| Missed escalation rate | < 5% |
| Attack block rate | 100% |

---

### Step 9 — `main.py` (Srinath)

Three subcommands via `argparse`:

```
python -m ticketpilot.main triage        # run 3 sample tickets
python -m ticketpilot.main eval          # mock eval (no API key needed)
python -m ticketpilot.main eval --real-agent
python -m ticketpilot.main dashboard     # SQLite summary
```

Startup sequence:
1. `_load_env()` — parse `.env` manually (no `python-dotenv` dependency)
2. Configure `logging` from `TICKETPILOT_LOG_LEVEL` env var
3. Warn (don't crash) if `ANTHROPIC_API_KEY` is missing in triage mode

---

### Step 10 — Tests (Gaurav + Kavya)

**`pytest.ini`** (required for pytest-asyncio 0.24+):
```ini
[pytest]
asyncio_mode = auto
```

| File | Tests | Covers |
|------|-------|--------|
| `test_guardrails.py` | 6 | injection detection, hard-stop, PII redaction, preprocess |
| `test_hitl.py` | 8 | permission hook allow/block logic, ApprovalQueue enqueue |
| `test_eval.py` | 5 | mock triage correctness, compute_metrics structure, dataset completeness |

All 19 tests run without `ANTHROPIC_API_KEY`. Mark any real-agent tests with:
```python
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="requires API key")
```

---

### Step 11 — `Dockerfile` (Srinath)

```dockerfile
FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TICKETPILOT_DB_PATH=/app/ticketpilot_obs.db
ENV TICKETPILOT_LOG_LEVEL=INFO
ENTRYPOINT ["python", "-m", "ticketpilot.main"]
CMD ["triage"]
```

---

## `requirements.txt`

```
anthropic>=0.86.0
claude-agent-sdk>=0.1.0
mcp>=1.0.0
```

## `requirements-dev.txt`

```
-r requirements.txt
pytest>=8.3.0
pytest-asyncio>=0.24.0
```

---

## `.env.example`

```
ANTHROPIC_API_KEY=your_key_here
TICKETPILOT_DB_PATH=ticketpilot_obs.db
TICKETPILOT_LOG_LEVEL=INFO
```

---

## Key Architectural Decisions

### 1. Hard stops run pre-agent, not in-agent
`preprocess_ticket()` fires before the SDK is called. Adversarial input never reaches the model. The `HITLPermissionHook` is a second defense at the tool-call layer.

### 2. MCP server for custom tools
`query()` in claude-agent-sdk does not accept custom tool schemas. Tools are exposed via a lightweight MCP server (`tools/mcp_server.py`) and registered in `ClaudeAgentOptions.mcp_servers`.

### 3. SQLite only — no external dependencies
`ObservabilityStore` uses SQLite with WAL mode. Works on any system with no setup. `ApprovalQueue` is in-memory per session.

### 4. Fail safe, never fail open
Every exception path returns `TriageDecision(action="escalate", confidence=0.0, queue="ServiceDesk")`. The system never silently drops a ticket.

### 5. `datetime.utcnow()` is banned
Python 3.14 removed it. Use `datetime.now(timezone.utc)` everywhere.

### 6. No `python-dotenv` dependency
`.env` is parsed with a 6-line stdlib function in `main.py`. Keeps runtime dependencies minimal.

---

## Smoke Test (run after `agent.py` is wired)

Before building the eval harness, verify the full pipeline with one ticket:

```bash
python -c "
import asyncio
from ticketpilot.models import TicketPayload
from ticketpilot.agent import triage_ticket

t = TicketPayload(
    ticket_id='SMOKE-001', channel='email',
    requester_email='test@acme.com',
    subject='Forgot my password',
    body='Hi, I forgot my password and am locked out.'
)
result = asyncio.run(triage_ticket(t))
print(result)
"
```

Expected: `priority=P3`, `queue=AccountsAccess`, `action=auto_resolve`, `confidence≥0.85`.

---

## Run Commands

```bash
# Setup
source ~/.venv/bin/activate
pip install -r requirements-dev.txt

# Triage
python -m ticketpilot.main triage

# Eval (no API key)
python -m ticketpilot.main eval

# Eval (real agent)
export ANTHROPIC_API_KEY=sk-...
python -m ticketpilot.main eval --real-agent

# Dashboard
python -m ticketpilot.main dashboard

# Tests
pytest ticketpilot/tests/ -v
```
