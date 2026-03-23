"""
Mock triage agent — no API key required.
Rule-based engine that mimics the Claude-powered agent's decision logic.
Drop-in replacement: same function signature as the real agent.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from ticketpilot.guardrails import preprocess_ticket
from ticketpilot.hitl import HITLPermissionHook
from ticketpilot.models import TicketPayload, TriageDecision
from ticketpilot.observability import DecisionRecord, ObservabilityStore, TraceStep
from ticketpilot.tools import (
    check_system_status,
    get_open_tickets,
    lookup_employee,
    lookup_kb,
    write_ticket_decision,
    flag_for_human,
    handoff_to_security,
)

logger = logging.getLogger(__name__)
obs_store = ObservabilityStore()

# ── Keyword rule sets ────────────────────────────────────────────────────────

_P1_SIGNALS = [
    r"production.*down", r"prod.*unreachable", r"service.*down",
    r"outage", r"ransomware", r"encrypted.*files", r"breach",
    r"security.*incident", r"suspicious.*login", r"vpn.*concentrator",
    r"\b200\s+.*session", r"47\s+service",
    r"data.*leak", r"unauthorized.*login",
]
_P2_SIGNALS = [
    r"vpn.*drop", r"keep.*disconnect", r"vpn.*disconnect",
    r"\d+\s+user", r"east\s+wing", r"slow.*internet",
    r"entire\s+(team|department|floor|wing)",
]
_SECURITY_SIGNALS = [
    r"ransomware", r"breach", r"data\s+leak", r"suspicious\s+login",
    r"unauthorized", r"hacked", r"phishing", r"malware",
]
_PASSWORD_SIGNALS = [r"password", r"reset.*password", r"forgot.*password", r"locked.*out"]
_VPN_SIGNALS = [r"vpn", r"virtual.*private"]
_NETWORK_SIGNALS = [r"internet.*slow", r"slow.*internet", r"network", r"wifi", r"wi-fi"]
_ENDPOINT_SIGNALS = [r"laptop", r"printer", r"install", r"software", r"zoom", r"windows", r"mac"]
_ACCOUNT_SIGNALS = [r"password", r"mfa", r"2fa", r"account.*locked", r"login", r"access"]
_APP_SIGNALS = [r"outlook", r"calendar", r"email.*sync", r"teams", r"app\s", r"slack.*not"]
_BEHALF_SIGNALS = [r"on behalf", r"for my intern", r"for the ceo", r"for.*manager", r"behalf of"]
_BULK_SIGNALS = [r"all\s+\d+\s+user", r"bulk", r"migrate.*account", r"all.*account"]
_EXTERNAL_DOMAINS = [r"@external\.", r"unknown@"]


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _classify(ticket: TicketPayload) -> tuple[str, str, str, str, float, str]:
    """Returns (priority, queue, action, resolution_type, confidence, reasoning)."""
    text = f"{ticket.subject} {ticket.body}".lower()
    steps = []

    # Unknown/external requester → escalate
    if _matches(ticket.requester_email, _EXTERNAL_DOMAINS):
        return "P1", "SecurityOps", "escalate", "human_required", 0.95,\
               "Unknown external requester — escalate to SecurityOps"

    # Security signals → always SecurityOps
    if _matches(text, _SECURITY_SIGNALS):
        steps.append("security signal detected")
        return "P1", "SecurityOps", "escalate", "security_review", 0.97,\
               f"Security signal in ticket body: {'; '.join(steps)}"

    # P1 outage/incident
    if _matches(text, _P1_SIGNALS):
        return "P1", "NetworkOps", "escalate", "human_required", 0.95,\
               "P1 service impact or outage signal detected"

    # On-behalf-of → escalate (impersonation risk)
    if _matches(text, _BEHALF_SIGNALS):
        return "P3", "AccountsAccess", "escalate", "human_required", 0.55,\
               "Request on behalf of another user — impersonation risk"

    # Bulk operations → hard stop
    if _matches(text, _BULK_SIGNALS):
        return "P2", "AccountsAccess", "hard_stop", "human_required", 0.99,\
               "Bulk account operation detected — hard stop"

    # Vague / empty body → escalate
    stripped = ticket.body.strip()
    if len(stripped) < 15 or stripped == "":
        return "P3", "ServiceDesk", "escalate", "human_required", 0.40,\
               "Ticket body too vague or empty — cannot classify"

    # Password reset → high confidence auto-resolve
    if _matches(text, _PASSWORD_SIGNALS):
        # Verify employee first (stub)
        emp = lookup_employee(ticket.requester_email)
        if emp.get("found") and not emp.get("is_contractor"):
            return "P3", "AccountsAccess", "auto_resolve", "resolved", 0.93,\
                   "Password reset for verified internal employee — auto-resolve via KB"
        return "P3", "AccountsAccess", "escalate", "human_required", 0.60,\
               "Password reset but employee verification unclear"

    # VPN / network issues
    if _matches(text, _VPN_SIGNALS) or _matches(text, _NETWORK_SIGNALS):
        if _matches(text, _P2_SIGNALS):
            return "P2", "NetworkOps", "route", "human_required", 0.85,\
                   "VPN/network issue affecting multiple users"
        return "P3", "NetworkOps", "route", "human_required", 0.80,\
               "Single-user VPN or network issue"

    # Endpoint (hardware/software)
    if _matches(text, _ENDPOINT_SIGNALS):
        return "P3", "EndpointSupport", "route", "human_required", 0.82,\
               "Endpoint or software issue — route to EndpointSupport"

    # Account/access
    if _matches(text, _ACCOUNT_SIGNALS):
        return "P3", "AccountsAccess", "route", "human_required", 0.75,\
               "Account or access issue — route to AccountsAccess"

    # App support
    if _matches(text, _APP_SIGNALS):
        return "P4", "AppSupport", "route", "human_required", 0.78,\
               "Application issue — route to AppSupport"

    # Fallback — low confidence, escalate
    return "P3", "ServiceDesk", "escalate", "human_required", 0.45,\
           "Could not classify with sufficient confidence — escalate to ServiceDesk"


async def triage_ticket(ticket: TicketPayload) -> TriageDecision:
    logger.info("Processing ticket %s from %s", ticket.ticket_id, ticket.requester_email)

    # 1. Guardrails pre-check
    ticket, abort_reason = preprocess_ticket(ticket)
    if abort_reason:
        logger.warning("Hard stop for ticket %s: %s", ticket.ticket_id, abort_reason)
        decision = TriageDecision(
            ticket_id=ticket.ticket_id,
            priority="P1",
            queue="SecurityOps",
            action="hard_stop",
            resolution_type="security_review",
            confidence=1.0,
            reasoning_trace=f"HARD STOP (pre-agent): {abort_reason}",
            agent_turns=0,
        )
        _record(ticket, decision, [
            TraceStep(step=0, type="guardrail", content=abort_reason)
        ])
        return decision

    trace: list[TraceStep] = []
    step = 0

    # 2. Enrich — call stub tools
    emp = lookup_employee(ticket.requester_email)
    trace.append(TraceStep(step=step, type="tool_call", content="lookup_employee",
                           tool_name="lookup_employee",
                           tool_input={"email": ticket.requester_email},
                           tool_result=emp)); step += 1

    open_tkts = get_open_tickets(ticket.requester_email)
    trace.append(TraceStep(step=step, type="tool_call", content="get_open_tickets",
                           tool_name="get_open_tickets",
                           tool_input={"email": ticket.requester_email},
                           tool_result=open_tkts)); step += 1

    sys_status = check_system_status()
    trace.append(TraceStep(step=step, type="tool_call", content="check_system_status",
                           tool_name="check_system_status",
                           tool_input={}, tool_result=sys_status)); step += 1

    # 3. Classify
    priority, queue, action, resolution_type, confidence, reasoning = _classify(ticket)
    trace.append(TraceStep(step=step, type="classification",
                           content=f"{priority}/{queue}/{action} conf={confidence:.2f}"))
    step += 1

    # 4. KB lookup for auto-resolve candidates
    if action == "auto_resolve":
        kb = lookup_kb(f"{ticket.subject} {ticket.body[:100]}")
        trace.append(TraceStep(step=step, type="tool_call", content="lookup_kb",
                               tool_name="lookup_kb",
                               tool_input={"query_text": ticket.subject},
                               tool_result=kb)); step += 1
        reasoning += f" | KB: {kb.get('title')} (conf {kb.get('confidence', 0):.2f})"

    # 5. Security handoff
    if queue == "SecurityOps" and action in ("escalate", "hard_stop"):
        handoff_result = handoff_to_security(
            ticket.ticket_id, "security_signal", reasoning[:200]
        )
        trace.append(TraceStep(step=step, type="tool_call", content="handoff_to_security",
                               tool_name="handoff_to_security",
                               tool_input={"ticket_id": ticket.ticket_id,
                                           "indicator": "security_signal",
                                           "context": reasoning[:200]},
                               tool_result=handoff_result)); step += 1

    # 6. HITL permission check before write
    hitl = HITLPermissionHook(
        ticket_id=ticket.ticket_id,
        priority=priority,
        queue=queue,
        confidence=confidence,
        agent_reasoning_getter=lambda: reasoning,
    )

    can_write = hitl.can_use_tool("write_ticket_decision", {
        "ticket_id": ticket.ticket_id, "priority": priority,
        "queue": queue, "action": action, "notes": reasoning,
    })

    if can_write:
        write_result = write_ticket_decision(ticket.ticket_id, priority, queue, action, reasoning)
        trace.append(TraceStep(step=step, type="tool_call", content="write_ticket_decision",
                               tool_name="write_ticket_decision",
                               tool_input={"ticket_id": ticket.ticket_id, "priority": priority,
                                           "queue": queue, "action": action},
                               tool_result=write_result)); step += 1
        logger.info("Decision written for %s: %s/%s/%s conf=%.2f",
                    ticket.ticket_id, priority, queue, action, confidence)
    else:
        trace.append(TraceStep(step=step, type="hitl_block",
                               content=f"write_ticket_decision blocked — pending: {hitl.pending_approvals}")); step += 1

    decision = TriageDecision(
        ticket_id=ticket.ticket_id,
        priority=priority,
        queue=queue,
        action=action,
        resolution_type=resolution_type,
        confidence=confidence,
        reasoning_trace=reasoning,
        agent_turns=step,
    )

    _record(ticket, decision, trace)
    log_decision(decision)
    return decision


def _record(ticket: TicketPayload, decision: TriageDecision, trace: list[TraceStep]):
    record = DecisionRecord(
        ticket_id=ticket.ticket_id,
        channel=ticket.channel,
        requester_email=ticket.requester_email,
        subject=ticket.subject,
        received_at=ticket.received_at,
        priority=decision.priority,
        queue=decision.queue,
        action=decision.action,
        resolution_type=decision.resolution_type,
        confidence=decision.confidence,
        trace_steps=trace,
        raw_input={"subject": ticket.subject, "body": ticket.body[:500]},
        escalated=decision.action in ("escalate", "hard_stop"),
    )
    try:
        obs_store.record_decision(record)
    except Exception as e:
        logger.error("Failed to record decision: %s", e)


def log_decision(decision: TriageDecision):
    import json
    print(json.dumps({
        "ticket_id": decision.ticket_id,
        "priority": decision.priority,
        "queue": decision.queue,
        "action": decision.action,
        "confidence": round(decision.confidence, 3),
        "turns": decision.agent_turns,
    }, indent=2))


if __name__ == "__main__":
    import asyncio
    t = TicketPayload(
        ticket_id="SMOKE-001", channel="email",
        requester_email="alice@acme.com",
        subject="Forgot my password",
        body="Hi, I forgot my password and am locked out. Please reset it.",
    )
    result = asyncio.run(triage_ticket(t))
    print(f"\nResult: {result.priority} / {result.queue} / {result.action}")
