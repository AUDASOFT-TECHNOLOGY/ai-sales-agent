"""Validation of facts a Brain proposes, before the Core records them."""

from sales_core.facts.proposals import (
    ProposalRejection,
    ProposalReview,
    RejectedProposal,
    review_proposals,
)

__all__ = ["ProposalRejection", "ProposalReview", "RejectedProposal", "review_proposals"]
