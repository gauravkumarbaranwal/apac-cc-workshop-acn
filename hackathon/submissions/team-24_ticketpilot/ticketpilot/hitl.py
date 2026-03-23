"""
Human-in-the-Loop permission hook and approval queue.
HITLPermissionHook.can_use_tool() is wired to ClaudeAgentOptions.can_use_tool
(or called directly in mock mode).
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)

# Tools that are always permitted — read-only or explicit human handoff
ALWAYS_PERMIT = {
    "lookup_kb", "lookup_employee", "lookup_asset",
    "get_open_tickets", "check_system_status",
    "flag_for_human", "handoff_to_security",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EscalationReason(Enum):
    LOW_CONFIDENCE = "low_confidence"
    MEDIUM_CONFIDENCE = "medium_confidence"
    CATEGORY_P1 = "category_p1"
    SECURITY_SIGNAL = "security_signal"
    UNKNOWN_REQUESTER = "unknown_requester"
    SENSITIVE_CATEGORY = "sensitive_category"


@dataclass
class ApprovalRequest:
    approval_id: str
    ticket_id: str
    tool_name: str
    tool_input: dict
    reason: EscalationReason
    context_summary: str
    agent_reasoning: str
    priority: str
    queue: str
    confidence: float
    created_at: str
    expires_at: str
    status: str = "pending"


@dataclass
class ApprovalDecision:
    approval_id: str
    decision: str              # approve | reject | override
    override_fields: dict
    reviewer: str
    reviewed_at: str
    notes: str


feedback_log: list[dict] = []


class ApprovalQueue:
    def __init__(self):
        self._pending: dict[str, ApprovalRequest] = {}
        self._completed: dict[str, ApprovalRequest] = {}

    def enqueue(self, req: ApprovalRequest) -> str:
        self._pending[req.approval_id] = req
        print(
            f"\n{'─'*55}\n"
            f"[HITL] APPROVAL REQUIRED\n"
            f"  Approval ID : {req.approval_id}\n"
            f"  Ticket      : {req.ticket_id}\n"
            f"  Tool        : {req.tool_name}\n"
            f"  Reason      : {req.reason.value}\n"
            f"  Priority    : {req.priority} → {req.queue}\n"
            f"  Confidence  : {req.confidence:.2f}\n"
            f"  Expires     : {req.expires_at}\n"
            f"{'─'*55}"
        )
        return req.approval_id

    def record_decision(self, decision: ApprovalDecision):
        req = self._pending.pop(decision.approval_id, None)
        if req:
            req.status = decision.decision
            self._completed[decision.approval_id] = req

    def pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._pending.get(approval_id) or self._completed.get(approval_id)


# Module-level singleton
approval_queue = ApprovalQueue()


class HITLPermissionHook:
    def __init__(
        self,
        ticket_id: str = "",
        priority: str = "P3",
        queue: str = "ServiceDesk",
        confidence: float = 1.0,
        agent_reasoning_getter: Callable[[], str] | None = None,
    ):
        self.ticket_id = ticket_id
        self.priority = priority
        self.queue = queue
        self.confidence = confidence
        self._reasoning_getter = agent_reasoning_getter or (lambda: "")
        self.pending_approvals: list[str] = []

    def can_use_tool(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name in ALWAYS_PERMIT:
            return True

        if tool_name == "trigger_password_reset":
            if self.confidence >= 0.90:
                return True
            reason = EscalationReason.LOW_CONFIDENCE
            self._block(tool_name, tool_input, reason, ttl_minutes=240)
            return False

        if tool_name == "write_ticket_decision":
            if self.priority == "P1":
                self._block(tool_name, tool_input, EscalationReason.CATEGORY_P1, ttl_minutes=30)
                return False
            if self.queue == "SecurityOps":
                self._block(tool_name, tool_input, EscalationReason.SECURITY_SIGNAL, ttl_minutes=30)
                return False
            if self.confidence < 0.70:
                self._block(tool_name, tool_input, EscalationReason.LOW_CONFIDENCE, ttl_minutes=240)
                return False
            return True

        # All other tools permitted by default
        return True

    def _block(self, tool_name: str, tool_input: dict, reason: EscalationReason, ttl_minutes: int):
        from datetime import timedelta
        approval_id = f"APR-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(minutes=ttl_minutes)).isoformat()
        req = ApprovalRequest(
            approval_id=approval_id,
            ticket_id=self.ticket_id,
            tool_name=tool_name,
            tool_input=tool_input,
            reason=reason,
            context_summary=f"{self.priority} ticket → {self.queue} (confidence {self.confidence:.2f})",
            agent_reasoning=self._reasoning_getter(),
            priority=self.priority,
            queue=self.queue,
            confidence=self.confidence,
            created_at=now.isoformat(),
            expires_at=expires,
        )
        approval_queue.enqueue(req)
        self.pending_approvals.append(approval_id)
        logger.warning(
            "HITL blocked tool '%s' for ticket %s — reason: %s (approval %s)",
            tool_name, self.ticket_id, reason.value, approval_id,
        )


async def interactive_approval_surface(approval_id: str) -> ApprovalDecision:
    req = approval_queue.get(approval_id)
    if not req:
        raise ValueError(f"No approval request found: {approval_id}")

    print(f"\n{'='*55}")
    print(f"APPROVAL SURFACE — {approval_id}")
    print(f"  Ticket   : {req.ticket_id}")
    print(f"  Tool     : {req.tool_name}")
    print(f"  Input    : {req.tool_input}")
    print(f"  Reason   : {req.reason.value}")
    print(f"  Priority : {req.priority}  Queue: {req.queue}  Conf: {req.confidence:.2f}")
    print(f"{'='*55}")

    # Demo mode: auto-simulate decisions
    if req.priority == "P1":
        decision_str = "override"
        override_fields = {"priority": "P1", "queue": "SecurityOps"}
        print("[DEMO] Auto-simulating OVERRIDE for P1 ticket")
    else:
        decision_str = "approve"
        override_fields = {}
        print("[DEMO] Auto-simulating APPROVE")

    decision = ApprovalDecision(
        approval_id=approval_id,
        decision=decision_str,
        override_fields=override_fields,
        reviewer="demo-auto",
        reviewed_at=_now(),
        notes="Auto-simulated in demo mode",
    )
    approval_queue.record_decision(decision)
    _log_feedback(req, decision)
    return decision


def _log_feedback(req: ApprovalRequest, decision: ApprovalDecision):
    if decision.decision == "override":
        entry = {
            "ticket_id": req.ticket_id,
            "agent_priority": req.priority,
            "agent_queue": req.queue,
            "human_override": decision.override_fields,
            "reviewer": decision.reviewer,
            "reason": req.reason.value,
            "timestamp": decision.reviewed_at,
            "reasoning_excerpt": req.agent_reasoning[:200],
        }
        feedback_log.append(entry)
        delta = []
        if decision.override_fields.get("priority") != req.priority:
            delta.append(f"priority {req.priority}→{decision.override_fields.get('priority')}")
        if decision.override_fields.get("queue") != req.queue:
            delta.append(f"queue {req.queue}→{decision.override_fields.get('queue')}")
        print(f"[FEEDBACK LOG] override on {req.ticket_id}: {', '.join(delta) or 'no field change'}")
