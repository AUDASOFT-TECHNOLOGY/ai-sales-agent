"""Cross-client guards that can only block, never write content (opt-out, contactability)."""

from sales_core.safety.checks import (
    ALLOWED,
    DEFAULT_OPT_OUT_KEYWORDS,
    BlockReason,
    SafetyVerdict,
    check_contactable,
    check_ready_to_send,
    is_opt_out_message,
    recipient_for,
)

__all__ = [
    "ALLOWED",
    "DEFAULT_OPT_OUT_KEYWORDS",
    "BlockReason",
    "SafetyVerdict",
    "check_contactable",
    "check_ready_to_send",
    "is_opt_out_message",
    "recipient_for",
]
