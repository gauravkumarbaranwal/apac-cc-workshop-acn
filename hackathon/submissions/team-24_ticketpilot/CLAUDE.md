# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: TicketPilot

Scenario 5 — Agentic IT helpdesk intake automation. Autonomous triage agent built on the **Claude Agent SDK** (`claude-agent-sdk`) with a specialist security subagent. See `decisions/ADR-001-ticketpilot-agent-architecture.md` for the full architecture decision record.

## Run Commands

```bash
# Setup (Python 3.10+, or use Docker below)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # set ANTHROPIC_API_KEY

# Triage 3 sample tickets
python -m ticketpilot.main triage

# Eval harness — no API key needed (uses mock agent)
python -m ticketpilot.main eval

# Eval with real agent
export ANTHROPIC_API_KEY=sk-...
python -m ticketpilot.main eval --real-agent

# Decision dashboard
python -m ticketpilot.main dashboard

# Tests (all 19 pass without ANTHROPIC_API_KEY)
pytest ticketpilot/tests/ -v
```

**Docker (assumes Docker and nothing else):**
```bash
docker build -t ticketpilot .
docker run --rm -e ANTHROPIC_API_KEY ticketpilot python -m ticketpilot.main triage
docker run --rm ticketpilot pytest ticketpilot/tests/ -v
```

## Module Map

| Module | Role |
|--------|------|
| `ticketpilot/models.py` | `TicketPayload`, `TriageDecision` dataclasses |
| `ticketpilot/tools.py` | 9 tool stubs + `TOOL_SCHEMAS` (MCP-compatible) |
| `tools/mcp_server.py` | FastMCP server exposing tools to the agent via stdio |
| `ticketpilot/guardrails.py` | Pre-agent PII redaction, injection detection, hard-stop keywords |
| `ticketpilot/agent.py` | `triage_ticket()` — SDK `query()` loop, `_parse_decision()`, `SYSTEM_PROMPT` |
| `ticketpilot/hitl.py` | `HITLPermissionHook` wired to `can_use_tool`, `ApprovalQueue` |
| `ticketpilot/observability.py` | SQLite `ObservabilityStore` (WAL mode), `DecisionRecord`, `TraceStep` |
| `ticketpilot/eval_harness.py` | 35-ticket `GOLDEN_DATASET`, `run_eval()`, `compute_metrics()` |
| `ticketpilot/main.py` | CLI entry point — `triage` / `eval` / `dashboard` subcommands |

## Architecture Notes

**Custom tools use MCP, not inline schemas.** `query()` in `claude-agent-sdk` does not accept custom tool schemas directly. All 9 tools are exposed via `tools/mcp_server.py` (FastMCP over stdio) and registered in `ClaudeAgentOptions.mcp_servers`.

**Two-layer safety.** Hard stops and prompt injection are blocked by `guardrails.preprocess_ticket()` *before* the SDK is called. The `HITLPermissionHook` enforces confidence thresholds at tool-call time as a second layer.

**Fail safe, never fail open.** Every exception path in `agent.py` returns `TriageDecision(action="escalate", confidence=0.0, queue="ServiceDesk")`. No ticket is ever silently dropped.

**Tool schema key is `input_schema`, not `parameters`.** Anthropic's format differs from OpenAI's.

**Never use `datetime.utcnow()`.** Removed in Python 3.14. Use `datetime.now(timezone.utc)` everywhere.

**No `python-dotenv` dependency.** `.env` is parsed with a stdlib function in `main.py`.

## HITL Permission Logic

```
trigger_password_reset  → PERMIT if confidence ≥ 0.90, else BLOCK + enqueue
write_ticket_decision   → PERMIT if not P1 AND queue != SecurityOps AND confidence ≥ 0.70
flag_for_human          → always PERMIT
handoff_to_security     → always PERMIT
lookup_* / read tools   → always PERMIT
```

Approval TTL: 30 min (P1), 240 min (all others).

## Eval Targets

| Metric | Target |
|--------|--------|
| Priority accuracy | ≥ 85% |
| Queue accuracy | ≥ 80% |
| Escalation accuracy | ≥ 90% |
| Missed escalation rate | < 5% |
| Attack block rate | 100% |

## Environment Variables

```
ANTHROPIC_API_KEY=your_key_here
TICKETPILOT_DB_PATH=ticketpilot_obs.db   # defaults to this if unset
TICKETPILOT_LOG_LEVEL=INFO
```
