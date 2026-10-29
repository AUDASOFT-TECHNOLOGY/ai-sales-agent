"""Builders for the learning tests: steps, cases and reviews, all invented."""

import json
from datetime import timedelta

from factories import NOW

from sales_core.learning import CaseReview, CaseStep, CaseText, LearningCase, Result


def step(index=0, *, length=120, questions=1, gap=None, unanswered=0, kind="first_contact", **kw):
    fields = dict(  # noqa: C408
        index=index,
        at=NOW + timedelta(hours=index),
        decision="send_message",
        action_type=kind,
        channel="whatsapp",
        length=length,
        questions=questions,
        gap_hours=gap,
        unanswered=unanswered,
        answers_reply=False,
        labels={},
    )
    return CaseStep(**(fields | kw))


def case(result=Result.DECLINED, steps=None, ref="c1", product=None, transcript=(), hide=()):
    return LearningCase(
        case_ref=ref,
        result=result,
        ended_at=NOW,
        steps=tuple(steps if steps is not None else [step()]),
        product=product,
        transcript=tuple(transcript),
        hide=tuple(hide),
    )


def lines(*pairs):
    return tuple(CaseText(role=role, text=text) for role, text in pairs)


def review(
    n,
    result,
    *,
    causes=(),
    practices=(),
    confidence="high",
    product=None,
    applied=(),
    at=None,
    ended=None,
):
    return CaseReview(
        case_ref=f"case-{n}",
        result=result,
        ended_at=ended or at or NOW,
        reviewed_at=NOW,
        causes=tuple(causes),
        practices=tuple(practices),
        confidence=confidence,
        product=product,
        applied_notes=frozenset(applied),
    )


def verdict(*, practices=(), causes=(), confidence="medium", unknown=False, turning_step=None):
    return json.dumps(
        {
            "unknown": unknown,
            "confidence": confidence,
            "turning_step": turning_step,
            "practices": list(practices),
            "causes": list(causes),
        }
    )
