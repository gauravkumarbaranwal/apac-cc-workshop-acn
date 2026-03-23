# Team 24 — TicketPilot

## Participants
- Rakesha PM
- Avinash and Chinmaya Arch
- Srinath and Shubham Devs
- Gaurav and Kavya Tester
- Srinath Infra

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
Exact commands. Assume the reader has Docker and nothing else.

## If We Had Another Day
What you'd tackle next, in priority order. Be honest about what's held
together with tape.

## How We Used Claude Code
What worked. What surprised you. Where it saved the most time.