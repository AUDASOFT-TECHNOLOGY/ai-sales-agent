"""Writing Revansa's decisions back into a client's source.

See `policy` for what an operator may allow (nothing, by default), `planning` for which writes
follow from it, `ledger` for the journal of attempts (`memory` for an in-memory one, `durable` for one over rows that
survive a restart) and `syncer`
for the pass that sends them.
"""

from sales_app.sourcing.writeback.durable import DurableWriteBackLedger
from sales_app.sourcing.writeback.ledger import (
    FailureCode,
    LedgerEntry,
    Outcome,
    WriteAttempt,
    WriteBackLedger,
)
from sales_app.sourcing.writeback.memory import InMemoryWriteBackLedger
from sales_app.sourcing.writeback.planning import (
    PlannedWrite,
    SkipReason,
    WriteBackPlan,
    plan_field_writes,
    plan_status_writes,
    value_text,
    wanted_status,
)
from sales_app.sourcing.writeback.policy import (
    WRITEBACK_FIELDS,
    WriteBackPolicy,
    WriteBackPolicyError,
)
from sales_app.sourcing.writeback.syncer import SyncReport, WriteBackSyncer

__all__ = [
    "WRITEBACK_FIELDS",
    "DurableWriteBackLedger",
    "FailureCode",
    "InMemoryWriteBackLedger",
    "LedgerEntry",
    "Outcome",
    "PlannedWrite",
    "SkipReason",
    "SyncReport",
    "WriteAttempt",
    "WriteBackLedger",
    "WriteBackPlan",
    "WriteBackPolicy",
    "WriteBackPolicyError",
    "WriteBackSyncer",
    "plan_field_writes",
    "plan_status_writes",
    "value_text",
    "wanted_status",
]
