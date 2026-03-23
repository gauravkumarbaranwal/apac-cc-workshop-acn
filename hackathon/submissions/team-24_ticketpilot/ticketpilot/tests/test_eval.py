import pytest
from ticketpilot.eval_harness import (
    GOLDEN_DATASET,
    EvalResult,
    GoldenTicket,
    compute_metrics,
    mock_agent_triage,
)


# ── mock_agent_triage ─────────────────────────────────────────────────────────

async def test_mock_triage_password_reset():
    ticket = next(t for t in GOLDEN_DATASET if t.ticket_id == "GT-001")
    result = await mock_agent_triage(ticket)
    assert result["action"] == "auto_resolve"
    assert result["queue"] == "AccountsAccess"
    assert result["escalate"] is False


async def test_mock_triage_prompt_injection_hard_stop():
    ticket = next(t for t in GOLDEN_DATASET if t.ticket_id == "GT-021")
    result = await mock_agent_triage(ticket)
    assert result["action"] in ("hard_stop", "escalate")
    assert result["attack_blocked"] is True


async def test_mock_triage_p1_monitoring_alert():
    ticket = next(t for t in GOLDEN_DATASET if t.ticket_id == "GT-005")
    result = await mock_agent_triage(ticket)
    assert result["priority"] == "P1"
    assert result["escalate"] is True


# ── compute_metrics ───────────────────────────────────────────────────────────

def _make_result(priority_correct=True, queue_correct=True, action_correct=True,
                 escalation_correct=True, adversarial=False, attack_type=None,
                 attack_blocked=None, expected_escalate=False, got_escalate=False):
    return EvalResult(
        ticket_id="T-001", adversarial=adversarial, attack_type=attack_type,
        expected_priority="P3", got_priority="P3" if priority_correct else "P1",
        expected_queue="ServiceDesk", got_queue="ServiceDesk" if queue_correct else "NetworkOps",
        expected_action="route", got_action="route" if action_correct else "escalate",
        expected_escalate=expected_escalate, got_escalate=got_escalate,
        priority_correct=priority_correct, queue_correct=queue_correct,
        action_correct=action_correct, escalation_correct=escalation_correct,
        attack_blocked=attack_blocked,
    )


def test_compute_metrics_returns_required_keys():
    results = [_make_result() for _ in range(5)]
    metrics = compute_metrics(results)
    assert "total" in metrics
    assert "overall" in metrics
    assert "escalation" in metrics
    assert "adversarial" in metrics
    assert "per_queue_precision_recall" in metrics
    assert "priority_accuracy" in metrics["overall"]
    assert "attack_block_rate" in metrics["adversarial"]


def test_compute_metrics_perfect_score():
    results = [_make_result() for _ in range(10)]
    metrics = compute_metrics(results)
    assert metrics["overall"]["priority_accuracy"] == 100.0
    assert metrics["overall"]["queue_accuracy"] == 100.0
    assert metrics["overall"]["action_accuracy"] == 100.0


def test_compute_metrics_partial_accuracy():
    results = [_make_result(priority_correct=(i % 2 == 0)) for i in range(10)]
    metrics = compute_metrics(results)
    assert metrics["overall"]["priority_accuracy"] == 50.0


def test_compute_metrics_attack_block_rate_100():
    results = [
        _make_result(adversarial=True, attack_type="prompt_injection", attack_blocked=True)
        for _ in range(5)
    ]
    metrics = compute_metrics(results)
    assert metrics["adversarial"]["attack_block_rate"] == 100.0


# ── Golden dataset completeness ───────────────────────────────────────────────

def test_golden_dataset_has_35_tickets():
    assert len(GOLDEN_DATASET) == 35


def test_golden_dataset_has_15_adversarial():
    adversarial = [t for t in GOLDEN_DATASET if t.adversarial]
    assert len(adversarial) == 15


def test_golden_dataset_has_20_normal():
    normal = [t for t in GOLDEN_DATASET if not t.adversarial]
    assert len(normal) == 20
