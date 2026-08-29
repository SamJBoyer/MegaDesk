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


def _clip(text: str) -> str:
    body = text.strip()
    if not body:
        return "(no changes)"
    if len(body) <= _DIFF_LIMIT:
        return body
    return body[:_DIFF_LIMIT] + "\n... (diff truncated)"
