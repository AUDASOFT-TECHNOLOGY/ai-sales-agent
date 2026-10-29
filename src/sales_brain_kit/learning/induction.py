"""From many reviewed conversations to the few rules worth following.

A rule is never drawn from one conversation, and never from a pattern that also shows in the
conversations that went the other way. For each code of the catalog:

    avoid   held responsible in unfavourable conversations (`support`), compared with how often the
            same thing shows in conversations that were achieved (`against`);
    do      present in achieved conversations (`support`), compared with how often it also shows in
            the unfavourable ones (`against`).

Levels, from the cases that are independent people:

    fewer than MIN_WATCH     nothing: it is a coincidence until proven otherwise
    MIN_WATCH and up         a note in observation: kept and shown to the client, not given to the agent
    MIN_ACTIVE and up        and the rule is at least MIN_RATIO times likelier on its side than on the other: active

A comparison needs conversations on the other side (MIN_CONTROL): without them a note stays in
observation, because a practice present in everything proves nothing. Only `solid` notes (enough
cases and a wide ratio) reach `high`. Notes are recomputed from the retained reviews each time, so
duplicates merge, what stops being confirmed ages out, and a note the client switched off stays off.

Then each active note is checked against what happened since it began to apply: if conversations
that used it do no better than those before it, it is switched off ("not helping") for good.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from sales_brain_kit.learning.practices import BY_CODE
from sales_core.contracts import LearnedNote
from sales_core.learning import CaseReview, Result

MIN_WATCH = 3
MIN_ACTIVE = 5
MIN_CONTROL = 3
MIN_RATIO_TO_WATCH = 1.5  # likelier than on the other side, at the least
MIN_RATIO = 2.0
SOLID_CASES = 8
SOLID_RATIO = 3.0
MAX_ACTIVE = 6
MIN_EFFECT = 8  # conversations on each side before a note's effect is judged
WINDOW = timedelta(days=180)


def _counts_as(review: CaseReview, code: str, *, cause: bool) -> bool:
    """Whether a review holds `code`. A reading the model was unsure of does not count; a
    measurement always does."""
    held = review.causes if cause else review.practices
    if code not in held:
        return False
    return BY_CODE[code].how == "measured" or review.confidence != "low"


def _final(reviews: Sequence[CaseReview], now: datetime) -> list[CaseReview]:
    latest: dict[str, CaseReview] = {}
    for review in reviews:
        if not review.result.is_final or now - review.ended_at > WINDOW:
            continue
        kept = latest.get(review.case_ref)
        if kept is None or review.reviewed_at >= kept.reviewed_at:
            latest[review.case_ref] = review
    return list(latest.values())


def _candidates(
    reviews: Sequence[CaseReview],
) -> dict[str, tuple[str, int, int, float | None]]:
    """code -> (level, support, against, ratio) for a group of reviews.

    The ratio says how many times likelier the code is on the side it is held against than on the
    other side (with a small allowance so that a side with few cases cannot make it infinite)."""
    achieved = [r for r in reviews if r.result is Result.ACHIEVED]
    unfavourable = [r for r in reviews if r.result.is_unfavourable]
    out: dict[str, tuple[str, int, int, float | None]] = {}
    for code, practice in BY_CODE.items():
        if practice.kind == "avoid":
            support = sum(_counts_as(r, code, cause=True) for r in unfavourable)
            against = sum(_counts_as(r, code, cause=False) for r in achieved)
            side_total, other_total = len(unfavourable), len(achieved)
        else:
            support = sum(_counts_as(r, code, cause=False) for r in achieved)
            against = sum(_counts_as(r, code, cause=False) for r in unfavourable)
            side_total, other_total = len(achieved), len(unfavourable)
        if support < MIN_WATCH:
            continue
        ratio = None
        if other_total >= MIN_CONTROL:
            base = (against + 1) / (other_total + 2)
            ratio = (support / side_total) / base
            if ratio < MIN_RATIO_TO_WATCH:
                continue  # as common on the other side: not what makes the difference
        controlled = ratio is not None and ratio >= MIN_RATIO
        # Without anyone to compare with, only a clear-cut avoid rule may apply: many cases and
        # nothing on the other side.
        clear_cut = ratio is None and support >= SOLID_CASES and against == 0
        active = support >= MIN_ACTIVE and (controlled or (clear_cut and practice.kind == "avoid"))
        solid = active and support >= SOLID_CASES and ratio is not None and ratio >= SOLID_RATIO
        level = "high" if solid else "medium" if active else "low"
        out[code] = (level, support, against, ratio)
    return out


def _effect_fails(note: LearnedNote, reviews: Sequence[CaseReview]) -> bool:
    """Whether conversations that used this note do no better than those before it applied."""
    if note.active_since is None:
        return False
    before = [r for r in reviews if r.ended_at < note.active_since]
    after = [
        r for r in reviews if r.ended_at >= note.active_since and note.note_id in r.applied_notes
    ]
    if len(before) < MIN_EFFECT or len(after) < MIN_EFFECT:
        return False
    rate_before = sum(r.result is Result.ACHIEVED for r in before) / len(before)
    rate_after = sum(r.result is Result.ACHIEVED for r in after) / len(after)
    return rate_after <= rate_before


def consolidate(
    reviews: Sequence[CaseReview], notes: Sequence[LearnedNote], *, now: datetime
) -> list[LearnedNote]:
    current = _final(reviews, now)
    known = {n.note_id: n for n in notes}
    groups: list[tuple[str | None, list[CaseReview]]] = [(None, current)]
    by_product: dict[str, list[CaseReview]] = {}
    for review in current:
        if review.product:
            by_product.setdefault(review.product, []).append(review)
    groups += [(p, rs) for p, rs in sorted(by_product.items()) if len(rs) >= 2 * MIN_ACTIVE]

    found: dict[str, tuple[LearnedNote, float]] = {}
    for product, group in groups:
        for code, (level, support, against, ratio) in _candidates(group).items():
            if product is not None and code in found and found[code][0].state == "active":
                continue  # the global note already says it
            note_id = code if product is None else f"{code}:{product}"
            before = known.get(note_id)
            practice = BY_CODE[code]
            state = "active" if level in ("medium", "high") else "watching"
            note = LearnedNote(
                note_id=note_id,
                code=code,
                kind=practice.kind,
                theme=practice.theme,
                product=product,
                state=state,
                evidence=level,
                cases=support,
                against=against,
                reason="loss_cause" if practice.kind == "avoid" else "wins_pattern",
                created_at=before.created_at if before and before.created_at else now,
                updated_at=now,
                active_since=before.active_since if before else None,
                confirmed_by_client=before.confirmed_by_client if before else False,
            )
            found[note_id] = (note, support * (ratio if ratio is not None else 1.0))

    result: dict[str, LearnedNote] = {}
    strength: dict[str, float] = {}
    for note_id, (note, score) in found.items():
        before = known.get(note_id)
        if before is not None and before.state == "off":
            result[note_id] = replace(
                note, state="off", off_by=before.off_by, reason=before.reason, active_since=None
            )
            continue
        if before is not None and before.confirmed_by_client:
            note = replace(note, state="active", reason="client_confirmed")
        result[note_id] = note
        strength[note_id] = score
    # What is no longer supported: kept only if the client decided about it.
    for note_id, before in known.items():
        if note_id in result:
            continue
        if before.state == "off" or before.confirmed_by_client:
            result[note_id] = replace(before, updated_at=now)
            strength[note_id] = 0.0
        elif before.state == "active":
            continue  # aged out: nothing left that confirms it
    # Judge what the active notes did since they began.
    for note_id, note in list(result.items()):
        if note.state == "active" and not note.confirmed_by_client and _effect_fails(note, current):
            result[note_id] = replace(note, state="off", off_by="auto", reason="not_helping")
    # The prompt has a budget: confirmed first, then the best supported; the rest keep watching.
    active = sorted(
        (n for n in result.values() if n.state == "active"),
        key=lambda n: (not n.confirmed_by_client, -strength.get(n.note_id, 0.0), n.note_id),
    )
    for note in active[MAX_ACTIVE:]:
        result[note.note_id] = replace(note, state="watching")
    # Stamp the moment a note starts to apply, clear it when it stops.
    final: list[LearnedNote] = []
    for note in result.values():
        before = known.get(note.note_id)
        if note.state == "active":
            since = before.active_since if before and before.state == "active" else None
            note = replace(note, active_since=since or now)
        else:
            note = replace(note, active_since=None)
        final.append(note)
    return sorted(final, key=lambda n: (n.state != "active", n.state == "off", n.note_id))
