"""Combine only validated data returned by enabled agent handlers."""

from __future__ import annotations


def build_reply(
    results: list[str],
    unavailable: list[str] | None = None,
    *,
    out_of_scope: bool = False,
    approval_action: str | None = None,
    future_approval_action: str | None = None,
) -> str:
    sections = list(results)
    if unavailable:
        sections.append("Unavailable results: " + ", ".join(dict.fromkeys(unavailable)) + ".")
    if out_of_scope:
        sections.append("Part of this request belongs to a later ShopSync AI phase; no action was performed for that part.")
    if approval_action:
        sections.append(f"The {approval_action.replace('_', ' ')} request was recorded as pending owner approval. It was not executed.")
    if future_approval_action:
        sections.append(
            f"A {future_approval_action.replace('_', ' ')} would require owner approval once that feature exists. "
            "No pending request was created and nothing was executed."
        )
    return "\n\n".join(sections) if sections else "No matching enabled agent could answer this request."
