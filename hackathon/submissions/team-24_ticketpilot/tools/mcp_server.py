# tools/mcp_server.py — Lightweight MCP server wrapping the 9 TicketPilot tools.
# Owner: Chinmaya (Step 3)
#
# Run standalone:
#   python tools/mcp_server.py
#
# Registered in agent.py via:
#   ClaudeAgentOptions(
#       mcp_servers={"ticketpilot": McpServerConfig(command="python", args=["tools/mcp_server.py"])},
#       ...
#   )

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ticketpilot.tools import (
    check_system_status,
    flag_for_human,
    get_open_tickets,
    handoff_to_security,
    lookup_asset,
    lookup_employee,
    lookup_kb,
    trigger_password_reset,
    write_ticket_decision,
)

mcp = FastMCP("ticketpilot")


@mcp.tool()
def lookup_kb_tool(query_text: str) -> dict:
    """Search the knowledge base for known issues and resolutions."""
    return lookup_kb(query_text)


@mcp.tool()
def lookup_employee_tool(email: str) -> dict:
    """Look up an employee record by email address."""
    return lookup_employee(email)


@mcp.tool()
def lookup_asset_tool(asset_id: str) -> dict:
    """Look up a hardware or software asset by asset ID."""
    return lookup_asset(asset_id)


@mcp.tool()
def get_open_tickets_tool(email: str) -> dict:
    """Return all open tickets for a given requester email."""
    return get_open_tickets(email)


@mcp.tool()
def check_system_status_tool() -> dict:
    """Return overall system and service health status."""
    return check_system_status()


@mcp.tool()
def write_ticket_decision_tool(
    ticket_id: str,
    priority: str,
    queue: str,
    action: str,
    notes: str,
) -> dict:
    """Persist the triage decision for a ticket.

    priority: P1 | P2 | P3 | P4
    queue: NetworkOps | EndpointSupport | AccountsAccess | AppSupport | SecurityOps | ServiceDesk
    action: auto_resolve | escalate | route | hard_stop
    """
    return write_ticket_decision(ticket_id, priority, queue, action, notes)


@mcp.tool()
def trigger_password_reset_tool(employee_id: str, email: str) -> dict:
    """Trigger a self-service password-reset email for an employee."""
    return trigger_password_reset(employee_id, email)


@mcp.tool()
def flag_for_human_tool(ticket_id: str, reason: str, context: str) -> dict:
    """Flag a ticket for human review with a reason and context."""
    return flag_for_human(ticket_id, reason, context)


@mcp.tool()
def handoff_to_security_tool(ticket_id: str, indicator: str, context: str) -> dict:
    """Hand a ticket off to the Security Operations team."""
    return handoff_to_security(ticket_id, indicator, context)


if __name__ == "__main__":
    mcp.run(transport="stdio")
