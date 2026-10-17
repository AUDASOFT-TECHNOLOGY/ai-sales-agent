"""Admin API: the LLM price list and a client's costs (isolation, roles, unknown is not zero)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_admin_settings_api import Fixture, as_alice, as_root, editor, owner

from sales_app.llm_pricing import InMemoryLLMPricingStore
from sales_app.llm_usage import STATUS_OK, InMemoryLLMUsageStore, LLMUsageRecord

AT = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
PRICING = "/admin/v1/settings/llm/pricing"
BODY = {
    "provider": "openai",
    "model": "gpt-x",
    "input_per_million": "2.50",
    "output_per_million": "10",
    "currency": "usd",
}


def call(client_id, model="gpt-x", inp: int | None = 1000, out: int | None = 500):
    return LLMUsageRecord(client_id, AT, "openai", model, STATUS_OK, inp, out)


@pytest.fixture
def fx(tmp_path):
    fixture = Fixture(tmp_path, "alpha", "beta")
    fixture.users.create(editor())  # reaches alpha only
    fixture.users.create(owner())
    fixture.application.llm_pricing = InMemoryLLMPricingStore()
    registers = {cid: InMemoryLLMUsageStore(cid) for cid in ("alpha", "beta")}
    fixture.registers = registers
    application = fixture.application
    application.builders = replace(application.builders, llm_usage=lambda cid: registers[cid])
    return fixture


class TestPriceList:
    def test_it_requires_authentication(self, fx):
        assert fx.client.get(PRICING).status_code == 401
        assert fx.client.put(PRICING, json=BODY).status_code == 401

    def test_an_owner_sets_lists_replaces_and_deletes_prices(self, fx):
        client = as_root(fx)
        put = client.put(PRICING, json=BODY)
        assert put.status_code == 200
        assert put.json()["currency"] == "USD" and put.json()["updated_by"] == "root"
        assert put.json()["input_per_million"] == "2.500000"
        client.put(PRICING, json={**BODY, "input_per_million": "3"})
        [only] = client.get(PRICING).json()["prices"]
        assert only["input_per_million"] == "3.000000"
        gone = client.delete(f"{PRICING}/openai/gpt-x")
        assert gone.status_code == 200 and gone.json() == {"prices": []}
        assert client.delete(f"{PRICING}/openai/gpt-x").status_code == 404

    def test_a_scoped_editor_reads_but_cannot_change_the_list(self, fx):
        client = as_root(fx)
        client.put(PRICING, json=BODY)
        client = as_alice(fx)
        assert len(client.get(PRICING).json()["prices"]) == 1
        assert client.put(PRICING, json=BODY).status_code == 403
        assert client.delete(f"{PRICING}/openai/gpt-x").status_code == 403

    @pytest.mark.parametrize(
        "change",
        [
            {"input_per_million": "-1"},
            {"output_per_million": "abc"},
            {"currency": "dollars"},
            {"model": "bad model"},
        ],
    )
    def test_an_invalid_price_is_refused_and_nothing_is_kept(self, fx, change):
        client = as_root(fx)
        assert client.put(PRICING, json={**BODY, **change}).status_code == 422
        assert client.get(PRICING).json() == {"prices": []}

    def test_without_a_database_it_says_so(self, fx):
        fx.application.llm_pricing = None
        client = as_root(fx)
        assert client.get(PRICING).status_code == 503
        assert client.put(PRICING, json=BODY).status_code == 503


class TestClientCosts:
    def test_a_known_cost_is_computed_from_recorded_tokens_and_the_price(self, fx):
        fx.registers["alpha"].record(call("alpha"))
        client = as_root(fx)
        client.put(PRICING, json=BODY)
        body = client.get("/admin/v1/clients/alpha/llm/usage").json()
        [line] = body["models"]
        assert (line["cost"], line["currency"], line["unknown_calls"]) == ("0.007500", "USD", 0)
        assert body["totals"] == {"USD": "0.007500"} and body["complete"] is True

    def test_no_price_means_an_unknown_cost_never_zero(self, fx):
        fx.registers["alpha"].record(call("alpha"))
        body = as_root(fx).get("/admin/v1/clients/alpha/llm/usage").json()
        [line] = body["models"]
        assert line["cost"] is None and line["currency"] is None and line["unknown_calls"] == 1
        assert body["totals"] == {} and body["complete"] is False

    def test_unknown_tokens_are_not_costed_even_with_a_price(self, fx):
        fx.registers["alpha"].record(call("alpha", inp=None, out=None))
        fx.registers["alpha"].record(call("alpha"))
        client = as_root(fx)
        client.put(PRICING, json=BODY)
        [line] = client.get("/admin/v1/clients/alpha/llm/usage").json()["models"]
        assert (line["calls"], line["costed_calls"], line["unknown_calls"]) == (2, 1, 1)
        assert line["cost"] == "0.007500"

    def test_the_list_without_a_database_still_shows_usage_with_unknown_costs(self, fx):
        fx.application.llm_pricing = None
        fx.registers["alpha"].record(call("alpha"))
        [line] = as_root(fx).get("/admin/v1/clients/alpha/llm/usage").json()["models"]
        assert line["cost"] is None

    def test_one_clients_usage_never_appears_in_another(self, fx):
        fx.registers["beta"].record(call("beta"))
        body = as_root(fx).get("/admin/v1/clients/alpha/llm/usage").json()
        assert body["models"] == [] and body["totals"] == {} and body["complete"] is True

    def test_an_admin_cannot_read_a_client_outside_their_scope(self, fx):
        client = as_alice(fx)
        assert client.get("/admin/v1/clients/alpha/llm/usage").status_code == 200
        assert client.get("/admin/v1/clients/beta/llm/usage").status_code == 404

    def test_an_unknown_client_is_a_404_and_anonymous_a_401(self, fx):
        assert fx.client.get("/admin/v1/clients/alpha/llm/usage").status_code == 401
        assert as_root(fx).get("/admin/v1/clients/nope/llm/usage").status_code == 404

    def test_a_deployment_without_a_register_says_so(self, fx):
        fx.application.builders = replace(fx.application.builders, llm_usage=None)
        assert as_root(fx).get("/admin/v1/clients/alpha/llm/usage").status_code == 503
