"""The LLM usage register: migration 0006, the SQL and in-memory stores, and the recording wrapper."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from sales_app.llm_usage import (
    STATUS_ERROR,
    STATUS_OK,
    InMemoryLLMUsageStore,
    LLMUsageRecord,
    RecordingLLMProvider,
)
from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.llm_usage_rows import SqlLLMUsageStore
from sales_app.persistence.schema import current_revision
from sales_app.persistence.tables import metadata
from sales_app.sourcing.errors import StoreUnavailableError
from sales_app.wiring.integrations import IntegrationBuilders
from sales_app.wiring.runtime import _record_usage
from sales_core.contracts import LLMMessage, LLMRequest, LLMResponse, LLMRole
from sales_core.ports import LLMError

AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
REQUEST = LLMRequest(messages=(LLMMessage(role=LLMRole.USER, content="hello"),))


def record(client_id="alpha", **kw) -> LLMUsageRecord:
    values = {
        "client_id": client_id,
        "occurred_at": AT,
        "provider": "openai",
        "model": "m",
        "status": STATUS_OK,
        "input_tokens": 10,
        "output_tokens": 5,
    }
    return LLMUsageRecord(**{**values, **kw})


@pytest.fixture
def engine():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine)
    return engine


# ---- migration 0006 -----------------------------------------------------------------------------


def test_0006_adds_the_usage_table_and_nothing_else():
    engine = create_database_engine("sqlite://")
    upgrade_database(engine, "0005")
    before = set(inspect(engine).get_table_names())
    assert "op_llm_usage" not in before
    upgrade_database(engine, "0006")
    assert current_revision(engine) == "0006"
    after = set(inspect(engine).get_table_names())
    assert after - before == {"op_llm_usage"}
    assert after - {"alembic_version"} < set(metadata.tables)
    columns = {c["name"]: c for c in inspect(engine).get_columns("op_llm_usage")}
    assert set(columns) == {
        "seq",
        "client_id",
        "occurred_at",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "status",
    }
    assert columns["input_tokens"]["nullable"] and columns["output_tokens"]["nullable"]
    assert not columns["status"]["nullable"]
    [fk] = inspect(engine).get_foreign_keys("op_llm_usage")
    assert fk["referred_table"] == "clients"


# ---- the stores ---------------------------------------------------------------------------------


def test_the_sql_store_keeps_each_call_in_order_and_unknown_counts_as_none(engine):
    store = SqlLLMUsageStore(engine, "alpha")
    store.record(record())
    store.record(record(status=STATUS_ERROR, input_tokens=None, output_tokens=None))
    first, second = store.list_usage()
    assert first == record()
    assert (second.status, second.input_tokens, second.output_tokens) == (STATUS_ERROR, None, None)


def test_the_sql_store_is_bound_to_its_client(engine):
    alpha, beta = SqlLLMUsageStore(engine, "alpha"), SqlLLMUsageStore(engine, "beta")
    alpha.record(record())
    assert beta.list_usage() == []
    with pytest.raises(ValueError):
        beta.record(record("alpha"))
    with pytest.raises(ValueError):
        SqlLLMUsageStore(engine, "")


def test_the_sql_store_reports_an_unreachable_database_with_a_fixed_sentence():
    broken = create_database_engine("sqlite://")  # no migration: the table is missing
    with pytest.raises(StoreUnavailableError) as caught:
        SqlLLMUsageStore(broken, "alpha").record(record())
    assert "alpha" not in str(caught.value)


def test_the_memory_store_matches_the_contract():
    store = InMemoryLLMUsageStore("alpha")
    store.record(record())
    store.record(record(model="n"))
    assert [r.model for r in store.list_usage()] == ["m", "n"]
    with pytest.raises(ValueError):
        store.record(record("beta"))
    with pytest.raises(ValueError):
        InMemoryLLMUsageStore("")


# ---- the recording wrapper ----------------------------------------------------------------------


class Inner:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error

    def complete(self, request):
        if self.error:
            raise self.error
        return self.response


def wrap(inner, store):
    return RecordingLLMProvider(
        inner, store, client_id="alpha", provider="openai", model="configured", now=lambda: AT
    )


def test_a_successful_call_is_recorded_with_what_the_provider_reported():
    store = InMemoryLLMUsageStore("alpha")
    response = LLMResponse(text="hi", model="gpt-x", input_tokens=12, output_tokens=3)
    assert wrap(Inner(response), store).complete(REQUEST) is response
    assert store.list_usage() == [
        record(model="gpt-x", input_tokens=12, output_tokens=3, provider="openai")
    ]


def test_unknown_consumption_is_not_made_up():
    store = InMemoryLLMUsageStore("alpha")
    wrap(Inner(LLMResponse(text="hi")), store).complete(REQUEST)
    [entry] = store.list_usage()
    assert (entry.model, entry.input_tokens, entry.output_tokens) == ("configured", None, None)


def test_a_failed_call_is_recorded_without_tokens_and_the_error_goes_on():
    store = InMemoryLLMUsageStore("alpha")
    error = LLMError("down")
    with pytest.raises(LLMError) as caught:
        wrap(Inner(error=error), store).complete(REQUEST)
    assert caught.value is error
    [entry] = store.list_usage()
    assert (entry.status, entry.model, entry.input_tokens) == (STATUS_ERROR, "configured", None)


class BrokenStore:
    def record(self, record):
        raise RuntimeError("secret-detail")

    def list_usage(self):
        return []


def test_a_register_that_fails_never_blocks_the_answer_or_leaks_its_message(caplog):
    response = LLMResponse(text="hi", input_tokens=1, output_tokens=1)
    assert wrap(Inner(response), BrokenStore()).complete(REQUEST) is response
    with pytest.raises(LLMError):
        wrap(Inner(error=LLMError("down")), BrokenStore()).complete(REQUEST)
    assert "RuntimeError" in caplog.text and "secret-detail" not in caplog.text


# ---- the runtime wiring -------------------------------------------------------------------------


def config():
    return SimpleNamespace(
        client_id="alpha", spec=SimpleNamespace(llm=SimpleNamespace(provider="openai", model="m"))
    )


def builders(llm_usage):
    return IntegrationBuilders(repositories={}, llms={}, channels={}, llm_usage=llm_usage)


def test_the_runtime_records_through_the_deployments_register():
    store = InMemoryLLMUsageStore("alpha")
    inner = Inner(LLMResponse(text="hi", input_tokens=2, output_tokens=1))
    wrapped = _record_usage(config(), builders(lambda client_id: store), inner)
    assert isinstance(wrapped, RecordingLLMProvider)
    wrapped.complete(REQUEST)
    assert [(r.client_id, r.input_tokens) for r in store.list_usage()] == [("alpha", 2)]


def test_a_deployment_without_a_register_keeps_the_model_as_it_is():
    inner = Inner(LLMResponse(text="hi"))
    assert _record_usage(config(), builders(None), inner) is inner


def test_a_register_that_cannot_be_opened_does_not_stop_the_client():
    def refuse(client_id):
        raise RuntimeError("no database")

    inner = Inner(LLMResponse(text="hi"))
    assert _record_usage(config(), builders(refuse), inner) is inner
