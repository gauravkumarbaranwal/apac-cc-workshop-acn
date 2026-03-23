import pytest
from ticketpilot.hitl import (
    ApprovalQueue,
    ApprovalRequest,
    EscalationReason,
    HITLPermissionHook,
    approval_queue,
)
from datetime import datetime, timezone


def _hook(priority="P3", queue="ServiceDesk", confidence=0.95):
    return HITLPermissionHook(
        ticket_id="TKT-TEST",
        priority=priority,
        queue=queue,
        confidence=confidence,
    )


# ── Read tools always permitted ───────────────────────────────────────────────

@pytest.mark.parametrize("tool", [
    "lookup_kb", "lookup_employee", "lookup_asset",
    "get_open_tickets", "check_system_status",
])
def test_read_tools_always_permitted(tool):
    hook = _hook(confidence=0.10)  # even with very low confidence
    assert hook.can_use_tool(tool, {}) is True


def test_flag_for_human_always_permitted():
    hook = _hook(confidence=0.10)
    assert hook.can_use_tool("flag_for_human", {"ticket_id": "X", "reason": "r", "context": "c"}) is True


def test_handoff_to_security_always_permitted():
    hook = _hook(confidence=0.10)
    assert hook.can_use_tool("handoff_to_security", {"ticket_id": "X", "indicator": "i", "context": "c"}) is True


# ── trigger_password_reset ────────────────────────────────────────────────────

def test_password_reset_permitted_high_confidence():
    hook = _hook(confidence=0.95)
    assert hook.can_use_tool("trigger_password_reset", {"employee_id": "EMP-1", "email": "a@b.com"}) is True


def test_password_reset_blocked_low_confidence():
    hook = _hook(confidence=0.65)
    result = hook.can_use_tool("trigger_password_reset", {"employee_id": "EMP-1", "email": "a@b.com"})
    assert result is False


def test_password_reset_blocked_enqueues_approval():
    q = ApprovalQueue()
    hook = HITLPermissionHook(
        ticket_id="TKT-ENQUEUE", priority="P3", queue="AccountsAccess", confidence=0.65
    )
    # Monkey-patch the module-level queue for this test
    import ticketpilot.hitl as hitl_mod
    original = hitl_mod.approval_queue
    hitl_mod.approval_queue = q
    try:
        hook.can_use_tool("trigger_password_reset", {"employee_id": "EMP-1", "email": "a@b.com"})
        assert len(q.pending()) == 1
        assert q.pending()[0].tool_name == "trigger_password_reset"
    finally:
        hitl_mod.approval_queue = original


# ── write_ticket_decision ─────────────────────────────────────────────────────

def test_write_blocked_for_p1():
    hook = _hook(priority="P1", confidence=0.95)
    assert hook.can_use_tool("write_ticket_decision", {"ticket_id": "X"}) is False


def test_write_blocked_for_security_ops():
    hook = _hook(queue="SecurityOps", confidence=0.95)
    assert hook.can_use_tool("write_ticket_decision", {"ticket_id": "X"}) is False


def test_write_blocked_low_confidence():
    hook = _hook(confidence=0.60)
    assert hook.can_use_tool("write_ticket_decision", {"ticket_id": "X"}) is False


def test_write_permitted_normal():
    hook = _hook(priority="P3", queue="AccountsAccess", confidence=0.90)
    assert hook.can_use_tool("write_ticket_decision", {"ticket_id": "X"}) is True


# ── ApprovalQueue ─────────────────────────────────────────────────────────────

def test_approval_queue_enqueue_and_pending():
    q = ApprovalQueue()
    req = ApprovalRequest(
        approval_id="APR-TEST01", ticket_id="TKT-001",
        tool_name="write_ticket_decision", tool_input={},
        reason=EscalationReason.CATEGORY_P1,
        context_summary="P1 ticket", agent_reasoning="...",
        priority="P1", queue="NetworkOps", confidence=0.95,
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=datetime.now(timezone.utc).isoformat(),
    )
    q.enqueue(req)
    assert len(q.pending()) == 1
    assert q.pending()[0].approval_id == "APR-TEST01"


def test_escalation_reason_enum_values():
    assert EscalationReason.LOW_CONFIDENCE.value == "low_confidence"
    assert EscalationReason.CATEGORY_P1.value == "category_p1"
    assert EscalationReason.SECURITY_SIGNAL.value == "security_signal"
