"""E6: the products of a prospect's lane bound what the Brain offers, enforced here and not by the
model. A list narrows the catalog, ALL (no product ids) is the catalog as it is, no lane is legacy."""

from copy import deepcopy

import pytest
from brain_support import answer, make_brain, payload_of, request, settings_doc

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import BrainOutputError, build_context
from sales_brain_kit.consultative.errors import LaneScopeError
from sales_core.contracts import Channel, LaneRoute

AB = LaneRoute(lane_id="sheet-a", channel=Channel.WHATSAPP, product_ids=("alpha", "beta"))
ALL = LaneRoute(lane_id="crm", channel=Channel.EMAIL, product_ids=())


def offered(llm) -> set[str]:
    catalog = payload_of(llm)["catalog"]
    return {p["id"] for p in catalog["products"]}


def candidates(doc, lane) -> set[str]:
    return {
        p.id for p in build_context(request(lane=lane), parse_settings(doc)).selection.candidates
    }


def with_new_product(product_id: str) -> dict:
    doc = deepcopy(settings_doc())
    doc["catalog"]["products"].append(
        {"id": product_id, "name": product_id.title(), "summary": "New."}
    )
    return doc


class TestAListOfProducts:
    def test_the_model_is_only_told_about_the_lane_products(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request(lane=AB))
        assert offered(llm) == {"alpha", "beta"}

    def test_the_selection_never_includes_another_product(self):
        assert candidates(settings_doc(), AB) == {"alpha", "beta"}

    def test_a_product_outside_the_lane_chosen_by_the_model_is_refused(self):
        bad = answer(selected_product="gamma", presented_products=["gamma"])
        brain, llm = make_brain(None, bad, bad)
        with pytest.raises(BrainOutputError):
            brain.decide(request(lane=AB))
        assert len(llm.calls) == 2  # told, and given one more try; never accepted

    def test_the_same_answer_is_accepted_when_the_lane_allows_the_product(self):
        lane = LaneRoute(lane_id="g", channel=Channel.WHATSAPP, product_ids=("gamma",))
        brain, _ = make_brain(None, answer(selected_product="gamma", presented_products=["gamma"]))
        assert brain.decide(request(lane=lane)).message is not None

    def test_a_product_the_catalog_does_not_have_is_a_configuration_fault(self):
        lane = LaneRoute(lane_id="x", channel=Channel.WHATSAPP, product_ids=("alpha", "ghost"))
        brain, llm = make_brain(None, answer())
        with pytest.raises(LaneScopeError) as error:
            brain.decide(request(lane=lane))
        assert error.value.missing == ("ghost",)
        assert len(llm.calls) == 0  # nothing was sent to the model

    def test_a_product_added_to_the_catalog_later_stays_out_of_the_list(self):
        assert candidates(with_new_product("omega"), AB) == {"alpha", "beta"}


class TestAllProducts:
    def test_every_product_of_the_catalog_is_offered(self):
        brain, llm = make_brain(None, answer())
        brain.decide(request(lane=ALL))
        assert {"alpha", "beta", "gamma"} <= offered(llm)

    def test_a_product_added_later_is_offered_without_touching_the_lane(self):
        assert "omega" not in candidates(settings_doc(), ALL)
        assert "omega" in candidates(with_new_product("omega"), ALL)

    def test_a_removed_product_is_no_longer_offered(self):
        doc = deepcopy(settings_doc())
        doc["catalog"]["products"] = [p for p in doc["catalog"]["products"] if p["id"] != "beta"]
        assert "beta" not in candidates(doc, ALL)


class TestLegacy:
    def test_no_lane_offers_the_whole_catalog_as_before(self):
        assert candidates(settings_doc(), None) == candidates(settings_doc(), ALL)
        brain, llm = make_brain(None, answer())
        brain.decide(request())
        assert {"alpha", "beta", "gamma"} <= offered(llm)

    def test_the_settings_of_the_brain_are_not_changed_by_a_narrow_lane(self):
        brain, _ = make_brain(None, answer(), answer())
        before = [p.id for p in brain.settings.catalog.products]
        brain.decide(request(lane=AB))
        assert [p.id for p in brain.settings.catalog.products] == before
