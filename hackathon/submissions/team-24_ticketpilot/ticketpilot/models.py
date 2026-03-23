from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TicketPayload:
    ticket_id: str
    channel: str                  # email | slack | web_form | api
    requester_email: str
    subject: str
    body: str
    received_at: str = field(default_factory=_now)
    raw_metadata: dict = field(default_factory=dict)


@dataclass
class TriageDecision:
    ticket_id: str
    priority: str                 # P1 | P2 | P3 | P4
    queue: str                    # NetworkOps | EndpointSupport | AccountsAccess |
                                  # AppSupport | SecurityOps | ServiceDesk
    action: str                   # auto_resolve | escalate | route | hard_stop
    resolution_type: str          # resolved | human_required | security_review
    confidence: float             # 0.0 – 1.0
    reasoning_trace: str
    decided_at: str = field(default_factory=_now)
    agent_turns: int = 0
