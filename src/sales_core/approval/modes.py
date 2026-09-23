"""Approval modes: what happens to a proposed message before it can be sent."""

from enum import StrEnum

from sales_core.contracts import ActionStatus


class ApprovalMode(StrEnum):
    """Values match `mode` in clients/<client>/client.yaml."""

    APPROVAL = "approval"  # a human approves or rejects each proposal in the Sheet
    AUTONOMOUS = "autonomous"  # proposals are approved automatically
    DRY_RUN = "dry_run"  # nothing is persisted or sent; only the audit log is written


def status_after_proposal(mode: ApprovalMode) -> ActionStatus:
    """Status a freshly proposed action takes under `mode`.

    Not meaningful for DRY_RUN, where proposals are never persisted.
    """
    if mode is ApprovalMode.AUTONOMOUS:
        return ActionStatus.APPROVED
    return ActionStatus.PROPOSED
