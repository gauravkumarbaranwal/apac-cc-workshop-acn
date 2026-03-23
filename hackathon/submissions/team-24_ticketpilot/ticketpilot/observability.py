"""
ObservabilityStore: SQLite-backed decision log with trace steps, feedback patterns,
and dashboard summary. No external dependencies.
"""
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraceStep:
    step: int
    type: str
    content: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_result: Any = None
    timestamp: str = field(default_factory=_now)


@dataclass
class DecisionRecord:
    ticket_id: str
    channel: str
    requester_email: str
    subject: str
    received_at: str
    priority: str
    queue: str
    action: str
    resolution_type: str
    confidence: float
    trace_steps: list[TraceStep] = field(default_factory=list)
    enrichment: dict = field(default_factory=dict)
    raw_input: dict = field(default_factory=dict)
    escalated: bool = False
    approval_id: str | None = None


class ObservabilityStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("TICKETPILOT_DB_PATH", "ticketpilot_obs.db")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS decision_log (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                received_at TEXT,
                decided_at TEXT,
                channel TEXT,
                requester_email TEXT,
                subject TEXT,
                priority TEXT,
                queue TEXT,
                action TEXT,
                resolution_type TEXT,
                confidence REAL,
                reasoning_trace TEXT,
                tool_calls TEXT,
                enrichment TEXT,
                escalated INTEGER DEFAULT 0,
                approval_id TEXT,
                human_decision TEXT,
                human_override TEXT,
                reviewer TEXT,
                reviewed_at TEXT,
                override_delta TEXT,
                feedback_applied INTEGER DEFAULT 0,
                raw_input_snapshot TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_ticket_id ON decision_log(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_email ON decision_log(requester_email);
            CREATE INDEX IF NOT EXISTS idx_queue ON decision_log(queue);
            CREATE INDEX IF NOT EXISTS idx_action ON decision_log(action);
            CREATE INDEX IF NOT EXISTS idx_decided_at ON decision_log(decided_at);
            CREATE INDEX IF NOT EXISTS idx_confidence ON decision_log(confidence);
            CREATE INDEX IF NOT EXISTS idx_escalated ON decision_log(escalated);

            CREATE TABLE IF NOT EXISTS feedback_patterns (
                id TEXT PRIMARY KEY,
                pattern_type TEXT,
                agent_priority TEXT,
                agent_queue TEXT,
                human_priority TEXT,
                human_queue TEXT,
                occurrence_count INTEGER DEFAULT 1,
                first_seen TEXT,
                last_seen TEXT,
                example_tickets TEXT,
                notes TEXT
            );
        """)
        self.conn.commit()

    def record_decision(self, record: DecisionRecord) -> str:
        log_id = f"LOG-{uuid.uuid4().hex[:8].upper()}"
        trace_json = json.dumps([vars(s) for s in record.trace_steps], default=str)
        tool_calls = json.dumps([
            {"tool": s.tool_name, "input": s.tool_input, "result": s.tool_result}
            for s in record.trace_steps if s.tool_name
        ], default=str)
        self.conn.execute(
            """INSERT INTO decision_log
               (id, ticket_id, received_at, decided_at, channel, requester_email, subject,
                priority, queue, action, resolution_type, confidence,
                reasoning_trace, tool_calls, enrichment, escalated, approval_id, raw_input_snapshot)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                log_id, record.ticket_id, record.received_at, _now(),
                record.channel, record.requester_email, record.subject,
                record.priority, record.queue, record.action, record.resolution_type,
                record.confidence, trace_json, tool_calls,
                json.dumps(record.enrichment), int(record.escalated),
                record.approval_id, json.dumps(record.raw_input),
            ),
        )
        self.conn.commit()
        logger.debug("Recorded decision %s for ticket %s", log_id, record.ticket_id)
        return log_id

    def record_human_decision(self, log_id: str, human_decision: str, reviewer: str,
                               override_fields: dict | None = None, notes: str = ""):
        row = self.conn.execute(
            "SELECT priority, queue FROM decision_log WHERE id=?", (log_id,)
        ).fetchone()
        delta = {}
        if row and override_fields:
            if override_fields.get("priority") and override_fields["priority"] != row["priority"]:
                delta["priority"] = {"agent": row["priority"], "human": override_fields["priority"]}
            if override_fields.get("queue") and override_fields["queue"] != row["queue"]:
                delta["queue"] = {"agent": row["queue"], "human": override_fields["queue"]}
        self.conn.execute(
            """UPDATE decision_log SET human_decision=?, reviewer=?, reviewed_at=?,
               human_override=?, override_delta=? WHERE id=?""",
            (human_decision, reviewer, _now(),
             json.dumps(override_fields or {}), json.dumps(delta), log_id),
        )
        self.conn.commit()
        if delta:
            self._process_feedback(log_id, delta)

    def _process_feedback(self, log_id: str, delta: dict):
        row = self.conn.execute(
            "SELECT ticket_id, priority, queue FROM decision_log WHERE id=?", (log_id,)
        ).fetchone()
        if not row:
            return
        agent_p = row["priority"]
        agent_q = row["queue"]
        human_p = delta.get("priority", {}).get("human", agent_p)
        human_q = delta.get("queue", {}).get("human", agent_q)

        existing = self.conn.execute(
            "SELECT id, occurrence_count, example_tickets FROM feedback_patterns "
            "WHERE agent_priority=? AND agent_queue=? AND human_priority=? AND human_queue=?",
            (agent_p, agent_q, human_p, human_q),
        ).fetchone()
        if existing:
            tickets = json.loads(existing["example_tickets"] or "[]")
            tickets.append(row["ticket_id"])
            self.conn.execute(
                "UPDATE feedback_patterns SET occurrence_count=?, last_seen=?, example_tickets=? WHERE id=?",
                (existing["occurrence_count"] + 1, _now(), json.dumps(tickets[-10:]), existing["id"]),
            )
        else:
            self.conn.execute(
                """INSERT INTO feedback_patterns
                   (id, pattern_type, agent_priority, agent_queue, human_priority, human_queue,
                    first_seen, last_seen, example_tickets)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, "routing_override", agent_p, agent_q, human_p, human_q,
                 _now(), _now(), json.dumps([row["ticket_id"]])),
            )
        self.conn.execute("UPDATE decision_log SET feedback_applied=1 WHERE id=?", (log_id,))
        self.conn.commit()

    def search(self, ticket_id=None, requester_email=None, queue=None, action=None,
               priority=None, confidence_max=None, escalated_only=False,
               overridden_only=False, limit=50) -> list[dict]:
        clauses, params = [], []
        if ticket_id:
            clauses.append("ticket_id=?"); params.append(ticket_id)
        if requester_email:
            clauses.append("requester_email=?"); params.append(requester_email)
        if queue:
            clauses.append("queue=?"); params.append(queue)
        if action:
            clauses.append("action=?"); params.append(action)
        if priority:
            clauses.append("priority=?"); params.append(priority)
        if confidence_max is not None:
            clauses.append("confidence<=?"); params.append(confidence_max)
        if escalated_only:
            clauses.append("escalated=1")
        if overridden_only:
            clauses.append("human_override IS NOT NULL AND human_override != '{}'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM decision_log {where} ORDER BY decided_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trace(self, log_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM decision_log WHERE id=?", (log_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("reasoning_trace", "tool_calls", "enrichment", "human_override",
                    "override_delta", "raw_input_snapshot"):
            if result.get(key):
                try:
                    result[key] = json.loads(result[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    def replay_ticket(self, log_id: str) -> str:
        record = self.get_trace(log_id)
        if not record:
            return f"No record found for {log_id}"
        lines = [
            f"{'='*60}",
            f"REPLAY: {log_id}",
            f"Ticket:    {record['ticket_id']}",
            f"From:      {record['requester_email']}",
            f"Subject:   {record['subject']}",
            f"Received:  {record['received_at']}",
            f"{'─'*60}",
            f"Decision:  {record['priority']} → {record['queue']} / {record['action']}",
            f"Confidence:{record['confidence']:.2f}",
            f"Escalated: {'Yes' if record['escalated'] else 'No'}",
            f"{'─'*60}",
            "TOOL CALLS:",
        ]
        for tc in (record.get("tool_calls") or []):
            lines.append(f"  [{tc.get('tool')}] input={tc.get('input')} → {tc.get('result')}")
        if record.get("human_decision"):
            lines += [
                f"{'─'*60}",
                f"HUMAN REVIEW: {record['human_decision']} by {record.get('reviewer')}",
                f"Override delta: {record.get('override_delta')}",
            ]
        lines.append("=" * 60)
        return "\n".join(lines)

    def get_feedback_patterns(self, min_occurrences: int = 2) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM feedback_patterns WHERE occurrence_count>=? ORDER BY occurrence_count DESC",
            (min_occurrences,),
        ).fetchall()
        return [dict(r) for r in rows]

    def dashboard_summary(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
        if total == 0:
            return {"total": 0, "message": "No decisions recorded yet."}
        by_action = dict(self.conn.execute(
            "SELECT action, COUNT(*) FROM decision_log GROUP BY action"
        ).fetchall())
        by_queue = dict(self.conn.execute(
            "SELECT queue, COUNT(*) FROM decision_log GROUP BY queue"
        ).fetchall())
        avg_conf = self.conn.execute("SELECT AVG(confidence) FROM decision_log").fetchone()[0]
        escalated = self.conn.execute("SELECT COUNT(*) FROM decision_log WHERE escalated=1").fetchone()[0]
        overridden = self.conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE feedback_applied=1"
        ).fetchone()[0]
        return {
            "total": total,
            "by_action": by_action,
            "by_queue": by_queue,
            "avg_confidence": round(avg_conf or 0, 3),
            "escalation_rate_pct": round(escalated / total * 100, 1),
            "override_rate_pct": round(overridden / total * 100, 1),
        }
