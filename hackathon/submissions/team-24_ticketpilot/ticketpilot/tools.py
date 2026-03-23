"""
Stub tool implementations. Return hardcoded dicts that mirror real integrations.
In production these would call HRIS, CMDB, IdP, KB APIs.
"""

from __future__ import annotations


def lookup_kb(query_text: str) -> dict:
    """Search the knowledge base for known issues and resolutions."""
    return {
        "found": True,
        "article_id": "KB-1042",
        "title": "Password Reset via Self-Service Portal",
        "resolution": "Visit portal.acme.com/reset, enter your email, follow the link.",
        "confidence": 0.95,
    }


def lookup_employee(email: str) -> dict:
    """Look up an employee record by email address."""
    if "external" in email or "unknown" in email:
        return {"found": False, "email": email}
    return {
        "found": True,
        "employee_id": "EMP-0042",
        "name": "Alice Smith",
        "department": "Engineering",
        "manager_email": "manager@acme.com",
        "status": "active",
        "is_contractor": False,
        "location": "Sydney",
    }


def lookup_asset(asset_id: str) -> dict:
    """Look up a hardware or software asset by asset ID."""
    return {
        "asset_id": asset_id,
        "type": "laptop",
        "model": "MacBook Pro 14",
        "os": "macOS 14.4",
        "owner_email": "alice@acme.com",
        "last_seen": "2026-03-22T09:00:00Z",
        "compliance_status": "ok",
        "warranty_expiry": "2027-06-01",
    }


def get_open_tickets(email: str) -> dict:
    """Return all open tickets for a given requester email."""
    return {
        "open_count": 1,
        "tickets": [
            {"id": "TKT-8901", "subject": "VPN issue", "priority": "P3", "status": "open"},
        ],
    }


def check_system_status() -> dict:
    """Return overall system and service health status."""
    return {
        "overall": "degraded",
        "services": {
            "vpn": "degraded",
            "email": "operational",
            "idp": "operational",
            "confluence": "operational",
            "jira": "operational",
        },
        "active_incidents": ["INC-0031: VPN concentrator high CPU"],
    }


def write_ticket_decision(
    ticket_id: str,
    priority: str,
    queue: str,
    action: str,
    notes: str,
) -> dict:
    """Persist the triage decision for a ticket."""
    return {
        "status": "written",
        "ticket_id": ticket_id,
        "priority": priority,
        "queue": queue,
        "action": action,
        "log_id": f"LOG-{ticket_id}",
    }


def trigger_password_reset(employee_id: str, email: str) -> dict:
    """Trigger a self-service password-reset email for an employee."""
    return {
        "status": "reset_sent",
        "employee_id": employee_id,
        "email": email,
        "expires_in_minutes": 30,
    }


def flag_for_human(ticket_id: str, reason: str, context: str) -> dict:
    """Flag a ticket for human review and add context notes."""
    return {"status": "flagged", "ticket_id": ticket_id, "reason": reason}


def handoff_to_security(ticket_id: str, indicator: str, context: str) -> dict:
    """Hand a ticket off to the Security Operations team."""
    return {"status": "handed_off", "ticket_id": ticket_id, "indicator": indicator, "team": "SecurityOps"}


# Callable registry — used by eval_harness mock agent and any non-MCP callers.
TOOL_REGISTRY = {
    "lookup_kb": lookup_kb,
    "lookup_employee": lookup_employee,
    "lookup_asset": lookup_asset,
    "get_open_tickets": get_open_tickets,
    "check_system_status": check_system_status,
    "write_ticket_decision": write_ticket_decision,
    "trigger_password_reset": trigger_password_reset,
    "flag_for_human": flag_for_human,
    "handoff_to_security": handoff_to_security,
}

# ---------------------------------------------------------------------------
# MCP-compatible tool schemas (Anthropic format — uses `input_schema`, not
# `parameters`).  Consumed by tools/mcp_server.py and agent.py.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "lookup_kb",
        "description": "Search the knowledge base for known issues and resolutions.",
        "input_schema": {
            "type": "object",
            "properties": {"query_text": {"type": "string", "description": "Search query"}},
            "required": ["query_text"],
        },
    },
    {
        "name": "lookup_employee",
        "description": "Look up employee details by email address.",
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "description": "Employee email address"}},
            "required": ["email"],
        },
    },
    {
        "name": "lookup_asset",
        "description": "Look up a device or asset by asset ID.",
        "input_schema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string", "description": "Asset identifier"}},
            "required": ["asset_id"],
        },
    },
    {
        "name": "get_open_tickets",
        "description": "Get open support tickets for a given email address.",
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "description": "Requester email address"}},
            "required": ["email"],
        },
    },
    {
        "name": "check_system_status",
        "description": "Check current status of all company systems and active incidents.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "write_ticket_decision",
        "description": "Write the final triage decision to the ticketing system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
                "queue": {
                    "type": "string",
                    "enum": [
                        "NetworkOps",
                        "EndpointSupport",
                        "AccountsAccess",
                        "AppSupport",
                        "SecurityOps",
                        "ServiceDesk",
                    ],
                },
                "action": {
                    "type": "string",
                    "enum": ["auto_resolve", "escalate", "route", "hard_stop"],
                },
                "notes": {"type": "string"},
            },
            "required": ["ticket_id", "priority", "queue", "action", "notes"],
        },
    },
    {
        "name": "trigger_password_reset",
        "description": "Trigger a password reset email for a verified employee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "email": {"type": "string"},
            },
            "required": ["employee_id", "email"],
        },
    },
    {
        "name": "flag_for_human",
        "description": "Flag this ticket for human review with a reason.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "reason": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["ticket_id", "reason", "context"],
        },
    },
    {
        "name": "handoff_to_security",
        "description": "Hand off a ticket to the security team with a threat indicator.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "indicator": {"type": "string", "description": "Security indicator or IOC"},
                "context": {"type": "string"},
            },
            "required": ["ticket_id", "indicator", "context"],
        },
    },
]
