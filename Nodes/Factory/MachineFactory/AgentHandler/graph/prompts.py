"""What each agent node actually asks for.

Kept apart from the node functions because these are the part most likely to be
tuned, and tuning a prompt should not mean reading control flow.

The division of labour is the point: the workhorse does the ticket and stops,
the git node reads what changed and writes the commit. When one agent did both,
the commit message was written by the same context that had just spent an hour
convincing itself the work was good, and it read like it.
"""

from __future__ import annotations

from collections.abc import Sequence

_DIFF_LIMIT = 6000


def pathfinder_prompt(*, ticket_name: str, instructions: str) -> str:
    """Deliberately small. This node clears the runway; it does not fly."""
    return "\n".join(
        [
            f"You are preparing a sandbox clone for the ticket {ticket_name!r}.",
            "",
            "The work that is about to happen here:",
            instructions.strip(),
            "",
            "Do not start that work. Your only job is to make sure it can start:",
            "- Look over the repository layout and find how it is meant to be built,"
            " run and tested (README, AGENTS.md, pyproject.toml, requirements*.txt,"
            " package.json, Makefile, or whatever this repo actually uses).",
            "- Install or set up only what is clearly missing and clearly required.",
            "- Do not refactor, do not fix unrelated problems, do not commit.",
            "",
            "Finish by reporting, in a few lines: the stack, how to run the tests,"
            " anything you installed, and anything that looks like it will block"
            " the work.",
        ]
    )


def workhorse_prompt(
    *,
    ticket_name: str,
    instructions: str,
    pathfinder_report: str = "",
    pictures: Sequence[str] = (),
) -> str:
    lines = [f"Ticket: {ticket_name}", "", instructions.strip()]
    if pathfinder_report.strip():
        lines += [
            "",
            "A survey of this sandbox clone was done before you started:",
            pathfinder_report.strip(),
        ]
    if pictures:
        lines += [
            "",
            f"{len(tuple(pictures))} reference image(s) are attached."
            " Use them as visual context.",
        ]
    lines += [
        "",
        "Leave your changes in the working tree. Do not commit and do not push:"
        " a later step reads your diff and writes the commit message.",
    ]
    return "\n".join(lines)


def git_prompt(
    *,
    ticket_name: str,
    ticket_id: str,
    repo: str,
    instructions: str,
    status_text: str,
    diff_text: str,
) -> str:
    work_name = ticket_name or repo or "unknown"
    return "\n".join(
        [
            "Commit the work already present in this sandbox clone.",
            "",
            f"Ticket: {work_name}",
            f"Ticket id: {ticket_id or '(none)'}",
            f"Repo: {repo or '(unknown)'}",
            "",
            "The instructions this work was done under:",
            instructions.strip(),
            "",
            "git status:",
            status_text.strip() or "(empty)",
            "",
            "git diff:",
            _clip(diff_text),
            "",
            "Stage everything that belongs to this ticket and make one commit."
            " Write a message that:",
            f"- Names the ticket: {work_name}",
            "- Summarizes the original instructions above",
            "- Describes what was actually changed, in enough detail for a"
            " reviewer who has not seen the diff",
            "",
            "Do not push. Do not amend anything that is already committed.",
            "If git refuses the commit because no author identity is set, set a"
            " local one for this repository and commit again.",
        ]
    )


def orchestrator_prompt(
    *,
    ticket_name: str,
    instructions: str,
    repo: str = "",
    repo_url: str = "",
    pictures: Sequence[str] = (),
) -> str:
    """Deep plan for a ticket that is too big for one workhorse turn."""
    lines = [
        "You are the orchestrator for a massive prototype change.",
        "Quality of the finished code is not the goal: a working slice that",
        "covers the ticket is. Do not implement anything yet.",
        "",
        f"Ticket: {ticket_name}",
        f"Repo: {repo or '(unknown)'}",
        f"Git URL: {repo_url or '(unknown)'}",
        "",
        "The ticket:",
        instructions.strip(),
        "",
        "Survey this cloned repository thoroughly. Then write an in-depth",
        "implementation plan that a later dispatcher can slice into a kanban",
        "board. The plan must include:",
        "- The outcome that means the ticket is done",
        "- The current layout of the relevant code (files, modules, entry points)",
        "- A sequenced list of features / layers / seams to change, with why",
        "  that order (dependencies first)",
        "- How to build and run the tests in this repo",
        "- Anything that will block the work",
        "",
        "Do not edit files. Do not commit. Do not start the work.",
    ]
    if pictures:
        lines += [
            "",
            f"{len(tuple(pictures))} reference image(s) are attached.",
            "Use them as visual context for the plan.",
        ]
    return "\n".join(lines)


def dispatcher_prompt(
    *,
    ticket_name: str,
    instructions: str,
    plan: str,
) -> str:
    return "\n".join(
        [
            "You are the dispatcher for a massive prototype change.",
            "Turn the orchestrator's plan into a ranked kanban board.",
            "Do not implement anything.",
            "",
            f"Ticket: {ticket_name}",
            "",
            "Original ticket:",
            instructions.strip(),
            "",
            "Orchestrator plan:",
            plan.strip() or "(no plan)",
            "",
            "Break the plan into the smallest slices that can each land as one",
            "commit. Rank them so the first card is the one that must happen",
            "first (foundations and dependencies before features).",
            "",
            "Reply with a JSON object and nothing else, in this shape:",
            '{',
            '  "cards": [',
            '    {"id": "1", "title": "short name", "detail": "what to do", "priority": 1}',
            "  ]",
            "}",
            "",
            "priority 1 is done first. Every card needs a title. Do not commit.",
        ]
    )


def ralph_prompt(
    *,
    ticket_name: str,
    instructions: str,
    plan: str,
    card: dict,
    remaining: int,
    pictures: Sequence[str] = (),
) -> str:
    title = str(card.get("title") or "").strip()
    detail = str(card.get("detail") or "").strip()
    lines = [
        "You are Ralph. You do one kanban card for a massive prototype.",
        "Ship something that works. Do not polish. Do not refactor unrelated",
        "code. Do not start the next card.",
        "",
        f"Ticket: {ticket_name}",
        f"Card: {title}",
        f"Cards left after this one: {max(0, remaining - 1)}",
        "",
        "This card:",
        detail or title,
        "",
        "Original ticket:",
        instructions.strip(),
        "",
        "Orchestrator plan (context only — you implement this card, not the plan):",
        plan.strip() or "(no plan)",
        "",
        "Implement this card in the working tree, then commit it.",
        f"Commit message should name the ticket ({ticket_name}) and this card",
        f"({title}). Do not push. Do not amend earlier commits.",
        "If git refuses the commit because no author identity is set, set a",
        "local one for this repository and commit again.",
        "If the tree is already what the card asked for, commit nothing and say so.",
    ]
    if pictures:
        lines += [
            "",
            f"{len(tuple(pictures))} reference image(s) are attached.",
            "Use them as visual context.",
        ]
    return "\n".join(lines)


def test_prompt(
    *,
    ticket_name: str,
    instructions: str,
    plan: str,
) -> str:
    return "\n".join(
        [
            "The massive-project cards are all committed. Run this repo's tests.",
            "",
            f"Ticket: {ticket_name}",
            "",
            "Original ticket:",
            instructions.strip(),
            "",
            "The orchestrator said this about how to test:",
            plan.strip() or "(no plan — find README / AGENTS.md / the usual files)",
            "",
            "Discover and run the real test command for this repository.",
            "Do not fix failures. Do not commit. Do not push.",
            "Finish with a short report: what you ran, pass/fail, and the",
            "first failures if any.",
        ]
    )


def _clip(text: str) -> str:
    body = text.strip()
    if not body:
        return "(no changes)"
    if len(body) <= _DIFF_LIMIT:
        return body
    return body[:_DIFF_LIMIT] + "\n... (diff truncated)"
