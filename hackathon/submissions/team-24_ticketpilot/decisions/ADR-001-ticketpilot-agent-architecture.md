# ADR-001: TicketPilot Agent Architecture

**Status:** Accepted
**Date:** 2026-03-23
**Deciders:** Engineering Lead, IT Ops Lead, Security
**Domain:** IT Helpdesk Intake Automation

---

## Context

IT receives ~350 tickets/day across 4 channels (email, Slack, web form, REST API from monitoring tools). A human triager classifies, enriches, and routes each one. Mean time-to-first-response: 4.2 hours. 61% of volume is repeatable/low-risk. We need an agent that makes real decisions at intake, not a classifier wrapper that still dumps everything on a human.

**Build constraint:** Claude Agent SDK (Python). Anthropic API key auth. Decision log mandatory. Human-in-the-loop for defined categories.

---

## Decision

We build a single primary triage agent with one specialist subagent, using the Claude Agent SDK's `query()` loop, custom tools, and `can_use_tool` permission hooks.

### Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                     INGESTION LAYER                          │
│  Email Poller │ Slack Events │ Web Form Webhook │ API Hook  │
│              ↓ normalize to TicketPayload schema             │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                  TRIAGE AGENT (Primary)                      │
│  Claude Agent SDK — claude-sonnet-4-6                        │
│                                                              │
│  Tools:                                                      │
│  • lookup_kb(query)           → KB article match + score    │
│  • lookup_employee(email)     → HRIS: dept, mgr, status     │
│  • lookup_asset(asset_id)     → CMDB: device, owner, SLA    │
│  • get_open_tickets(email)    → Prior/open ticket history   │
│  • check_system_status()      → Status page / monitoring    │
│  • write_ticket_decision(…)   → Ticketing system (write)    │
│  • trigger_password_reset(…)  → IdP API (scoped write)      │
│  • flag_for_human(reason)     → Approval queue              │
│  • handoff_to_security(…)     → SecurityOps subagent        │
│                                                              │
│  Permission hooks:                                           │
│  • trigger_password_reset → requires confidence ≥ 0.90      │
│  • write_ticket_decision  → always permitted (audit logged)  │
│  • handoff_to_security    → always permitted                 │
│  • flag_for_human         → always permitted                 │
└─────────────────────────────────────────────────────────────┘
                          ↓ (on security signal)
┌─────────────────────────────────────────────────────────────┐
│               SECURITY SUBAGENT (Specialist)                 │
│  Claude Agent SDK — claude-sonnet-4-6                        │
│                                                              │
│  Invoked by: triage agent via handoff_to_security()          │
│  Tools:                                                      │
│  • lookup_threat_intel(indicator)                            │
│  • check_watchlist(email, ip)                                │
│  • get_auth_log_summary(user, window)                        │
│  Returns: SecurityVerdict to triage agent                    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                   OBSERVABILITY LAYER                        │
│  DecisionLog (Postgres) │ TraceStore │ ApprovalQueue (Redis) │
└─────────────────────────────────────────────────────────────┘
```

---

## Tool Inventory

| Tool | Read/Write | External Call | Requires Approval |
|---|---|---|---|
| `lookup_kb` | Read | KB API | Never |
| `lookup_employee` | Read | HRIS API | Never |
| `lookup_asset` | Read | CMDB API | Never |
| `get_open_tickets` | Read | Ticketing API | Never |
| `check_system_status` | Read | Status API | Never |
| `write_ticket_decision` | Write | Ticketing API | Never (logged) |
| `trigger_password_reset` | Write | IdP API | confidence < 0.90 |
| `flag_for_human` | Write | ApprovalQueue | Never |
| `handoff_to_security` | Write | SubAgent | Never |

---

## Memory & State Model

**No persistent cross-session memory by design** (security posture: no ticket context bleeds across requests).

Per-session state carried in prompt context:
- Normalized `TicketPayload` (channel, body, requester metadata)
- Tool call results (enrichment data)
- Running `reasoning_trace` string appended at each decision step

Durable state lives in:
- **Ticketing system:** canonical record
- **DecisionLog:** structured decision + trace per ticket
- **ApprovalQueue:** pending human decisions (TTL: 4 hours before auto-escalation)

---

## Guardrails

### Input Guardrails (pre-agent)
- **PII scan:** detect and redact SSNs, credit card numbers before model call
- **Prompt injection detector:** regex + heuristic scan of ticket body for instruction-like patterns (`"ignore previous instructions"`, `"you are now"`, etc.). Flag and quarantine on match.
- **Size limit:** truncate ticket body at 8,000 tokens; attach overflow to ticket record separately
- **Encoding normalization:** strip HTML, decode MIME, transliterate non-UTF-8

### Agent Guardrails (in-loop)
- **Tool permission hooks** (`can_use_tool`): enforce confidence thresholds and category hard-stops
- **Max turns:** cap agent loop at 12 turns per ticket; if not resolved, auto-escalate
- **Scope enforcement:** system prompt explicitly enumerates what agent may and may not do

### Output Guardrails (post-agent)
- **Decision validator:** structured output parsed and validated against schema before write
- **Hard-stop checker:** final decision checked against hard-stop list before any write action
- **Audit write:** all decisions written to append-only log regardless of outcome

---

## Alternatives Considered

### Alt A: Pure classifier (no agent loop)
Fine-tuned classifier for routing, no tool use. **Rejected:** can't enrich with live context (CMDB, HRIS, status page), can't take write actions, can't reason about edge cases. High maintenance overhead for retraining.

### Alt B: Full LLM with no SDK, direct API
Build our own agent loop. **Rejected:** reinvents tool orchestration, context management, permission hooks. Agent SDK provides these primitives production-tested.

### Alt C: Multi-agent mesh (agent per channel)
One agent per ingest channel. **Rejected:** state fragmentation, routing inconsistency, harder to audit. Single triage agent with normalized input is simpler and more auditable.

### Alt D: Human-in-the-loop for every ticket
No automation, just AI-assisted summarization. **Rejected:** doesn't solve the throughput problem. Legal is fine with autonomous action within defined mandate.

---

## Consequences

### Good
- Fast path for 61% of ticket volume (password resets, FAQ, duplicate detection)
- Full reasoning trace per decision — explainable to any auditor
- Permission hooks enforce mandate boundaries at the code level, not just in prompts
- Subagent pattern keeps security logic isolated and auditable separately

### Bad / Watch
- Agent SDK is relatively new; API surface may evolve
- Security subagent adds latency (~2–4s) on security-flagged tickets
- Confidence calibration requires ongoing tuning; cold-start period will have higher escalation rate
- System prompt length grows as mandate expands; monitor context usage

---

## Open Questions

- [ ] Do we allow agent to send emails directly, or only write to ticketing system? *(Current: ticketing system only — safer)*
- [ ] SLA for human approvals in approval queue? Proposed: 30 min for P1, 4 hrs for P2
- [ ] How do we handle tickets arriving in languages other than English? *(MVP: English only; v2: translate-then-process)*
