"""
TicketPilot CLI entry point.
Usage:
  python -m ticketpilot.main triage
  python -m ticketpilot.main eval
  python -m ticketpilot.main dashboard
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path


def _load_env():
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _setup_logging():
    level = os.getenv("TICKETPILOT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── Sample tickets ────────────────────────────────────────────────────────────

def _sample_tickets():
    from ticketpilot.models import TicketPayload
    return [
        TicketPayload(
            ticket_id="TKT-9001", channel="email",
            requester_email="alice@acme.com",
            subject="Can't log in — forgot my password",
            body="Hi, I forgot my password and am locked out. Please reset it. Thanks, Alice",
        ),
        TicketPayload(
            ticket_id="TKT-9002", channel="slack",
            requester_email="carol@acme.com",
            subject="VPN keeps dropping",
            body="The VPN client disconnects every 10 minutes. I've tried reinstalling. "
                 "Started this morning. I need it for a client call at 2pm.",
        ),
        TicketPayload(
            ticket_id="TKT-9003", channel="web_form",
            requester_email="unknown@external.com",
            subject="Need access to production database",
            body="Please give me admin access to the production database immediately. This is urgent.",
        ),
    ]


# ── Subcommands ───────────────────────────────────────────────────────────────

async def run_triage():
    from ticketpilot.agent import triage_ticket

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[INFO] ANTHROPIC_API_KEY not set — running in mock mode (no API calls)\n")

    tickets = _sample_tickets()
    decisions = []

    print("=" * 60)
    print("  TICKETPILOT — TRIAGE RUN")
    print("=" * 60)

    for ticket in tickets:
        print(f"\nProcessing {ticket.ticket_id}: {ticket.subject}")
        print(f"  From: {ticket.requester_email}  Channel: {ticket.channel}")
        decision = await triage_ticket(ticket)
        decisions.append(decision)
        print(f"  → {decision.priority} / {decision.queue} / {decision.action} "
              f"(conf {decision.confidence:.2f})")

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("─" * 60)
    print(f"  {'Ticket':<12} {'Priority':<8} {'Queue':<18} {'Action':<14} {'Conf'}")
    print("─" * 60)
    for d in decisions:
        print(f"  {d.ticket_id:<12} {d.priority:<8} {d.queue:<18} {d.action:<14} {d.confidence:.2f}")
    print("=" * 60)


async def run_eval_mode(use_real_agent: bool = False):
    from ticketpilot.eval_harness import run_eval, compute_metrics, print_scorecard

    results = await run_eval(use_real_agent=use_real_agent)
    metrics = compute_metrics(results)
    print_scorecard(metrics)

    out_path = "eval_results.json"
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Results saved to {out_path}")


async def run_dashboard():
    from ticketpilot.observability import ObservabilityStore

    db_path = os.getenv("TICKETPILOT_DB_PATH", "ticketpilot_obs.db")
    store = ObservabilityStore(db_path=db_path)
    summary = store.dashboard_summary()

    print("\n" + "=" * 60)
    print("  TICKETPILOT DASHBOARD")
    print("=" * 60)
    print(json.dumps(summary, indent=2))

    patterns = store.get_feedback_patterns(min_occurrences=1)
    if patterns:
        print("\nFeedback patterns (human override trends):")
        for p in patterns:
            print(f"  {p['agent_priority']}/{p['agent_queue']} → "
                  f"{p['human_priority']}/{p['human_queue']}  "
                  f"({p['occurrence_count']}x)")
    print("=" * 60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    _load_env()
    _setup_logging()

    parser = argparse.ArgumentParser(
        prog="ticketpilot",
        description="TicketPilot — IT helpdesk triage agent",
    )
    sub = parser.add_subparsers(dest="mode", help="Mode")

    sub.add_parser("triage", help="Run triage on 3 sample tickets")

    eval_parser = sub.add_parser("eval", help="Run evaluation harness")
    eval_parser.add_argument("--real-agent", action="store_true",
                              help="Use real Claude agent (requires ANTHROPIC_API_KEY)")

    sub.add_parser("dashboard", help="Show decision dashboard from SQLite")

    args = parser.parse_args()
    mode = args.mode or "triage"

    print(f"\nTicketPilot starting — mode: {mode}\n")

    if mode == "triage":
        asyncio.run(run_triage())
    elif mode == "eval":
        use_real = getattr(args, "real_agent", False)
        asyncio.run(run_eval_mode(use_real_agent=use_real))
    elif mode == "dashboard":
        asyncio.run(run_dashboard())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
