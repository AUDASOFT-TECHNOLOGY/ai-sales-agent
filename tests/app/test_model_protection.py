"""A model that clients use cannot be taken away from them.

Deleting it or changing its Model ID is refused (409, naming the clients); switching it off is
allowed, and the answer says who is left without a usable model. The same holds on every route that
can write the model list, the generic credential routes included. Clients count wherever their
model is named: published for them, in their `client.yaml`, in their running configuration, and
whether they run or are disabled."""

import time
from types import SimpleNamespace

import pytest
from app_support import write_client
from test_admin_integrations_api import SECRET, Fixture, put, user

from sales_app.admin_auth import AdminRole
from sales_app.config.offered_models import OfferedModel
from sales_app.config_store import InMemoryConfigStore
from sales_app.model_usage import impact, models_in_use

M = "/admin/v1/settings/openai/models"
G = "/admin/v1/settings/integrations"
LLM = "/admin/v1/clients/{}/llm"


@pytest.fixture
def fx(tmp_path):
    write_client(tmp_path / "clients", "dormant", enabled=False)  # disabled, but valid
    fixture = Fixture(tmp_path, clients=("alpha", "beta"))
    fixture.users.create(user("root", AdminRole.OWNER))
    fixture.users.create(user("eve", AdminRole.EDITOR, frozenset({"alpha"})))
    return fixture


def add(client, name, model_id, active=True):
    response = client.post(M, json={"name": name, "id": model_id, "active": active})
    assert response.status_code == 200, response.text
    return response


def body(name, model_id, active=True):
    return {"name": name, "id": model_id, "active": active}


def assign(client, client_id, model_id):
    response = client.put(LLM.format(client_id), json={"provider": "openai", "model": model_id})
    assert response.status_code == 200, response.text


def ids(client):
    return [m["id"] for m in client.get(M).json()["models"]]


def readiness(client, client_id="alpha", wanted="ready", seconds=5.0):
    """The client's readiness once the refresh a Settings change triggers has finished."""
    deadline = time.monotonic() + seconds
    status = {}
    while time.monotonic() < deadline:
        status = client.get(f"/admin/v1/clients/{client_id}/runtime-status").json()
        if status["readiness"] == wanted and status["apply_state"] != "applying":
            return status
        time.sleep(0.02)
    raise AssertionError(f"{client_id} stayed {status}")


@pytest.fixture
def root(fx):
    """An owner, with two models; alpha runs on the first, dormant is given the second."""
    client = fx.login("root")
    put(client, f"{G}/openai.api_key", SECRET)
    add(client, "Sol", "sol-id")
    add(client, "Lune", "lune-id")
    add(client, "Spare", "spare-id")
    assign(client, "alpha", "sol-id")
    response = client.patch(
        "/admin/v1/clients/dormant", json={"llm_provider": "openai", "llm_model": "lune-id"}
    )
    assert response.status_code == 200, response.text
    return client


class TestDeletingOrRenamingAModelThatIsUsed:
    def test_deleting_it_is_refused_and_names_the_clients(self, fx, root):
        refused = root.delete(M + "/sol-id")
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert detail["client_ids"] == ["alpha"]
        assert detail["models"] == {"sol-id": ["alpha"]}
        assert "another model" in detail["message"]
        assert "sol-id" in ids(root)  # nothing was deleted

    def test_a_disabled_client_counts_too(self, fx, root):
        refused = root.delete(M + "/lune-id")
        assert refused.status_code == 409
        assert refused.json()["detail"]["client_ids"] == ["dormant"]

    def test_a_client_named_only_in_its_client_yaml_counts(self, fx, root):
        add(root, "Legacy", "test-model")  # what alpha and beta were written with
        refused = root.delete(M + "/test-model")
        assert refused.json()["detail"]["client_ids"] == ["beta"]  # alpha has moved to sol-id

    def test_changing_its_id_is_refused(self, fx, root):
        refused = root.put(M + "/sol-id", json=body("Sol", "sol-id-v2"))
        assert refused.status_code == 409
        assert refused.json()["detail"]["client_ids"] == ["alpha"]
        assert ids(root) == ["sol-id", "lune-id", "spare-id"]
        assert fx.application.registry.get_config("alpha").spec.llm.model == "sol-id"

    def test_changing_its_id_while_switching_it_off_is_still_refused(self, fx, root):
        refused = root.put(M + "/sol-id", json=body("Sol", "sol-id-v2", active=False))
        assert refused.status_code == 409

    def test_a_model_nobody_uses_can_be_deleted_or_changed(self, fx, root):
        assert root.put(M + "/spare-id", json=body("Spare", "spare-id-v2")).status_code == 200
        deleted = root.delete(M + "/spare-id-v2")
        assert deleted.status_code == 200
        assert "spare-id-v2" not in ids(root)

    def test_it_can_be_deleted_once_its_clients_have_another_model(self, fx, root):
        assert root.delete(M + "/sol-id").status_code == 409
        assign(root, "alpha", "spare-id")
        assert root.delete(M + "/sol-id").status_code == 200

    def test_renaming_the_name_only_is_always_allowed(self, fx, root):
        renamed = root.put(M + "/sol-id", json=body("Sol, renamed", "sol-id"))
        assert renamed.status_code == 200
        assert renamed.json()["impacted_clients"] == []
        assert renamed.json()["warning"] is None


class TestSwitchingAModelOff:
    def test_it_is_allowed_and_says_who_is_affected(self, fx, root):
        response = root.put(M + "/sol-id", json=body("Sol", "sol-id", active=False))
        assert response.status_code == 200
        result = response.json()
        assert result["impacted_clients"] == ["alpha"]
        assert "alpha" in result["warning"] and "switched off" in result["warning"]
        assert {m["id"]: m["active"] for m in result["models"]}["sol-id"] is False

    def test_the_client_is_then_not_ready_and_recovers_when_it_is_switched_on(self, fx, root):
        assert readiness(root)["readiness"] == "ready"
        root.put(M + "/sol-id", json=body("Sol", "sol-id", active=False))
        status = readiness(root, wanted="not_ready")
        assert any("not active in Settings" in m for m in status["missing_credentials"])
        back = root.put(M + "/sol-id", json=body("Sol", "sol-id", active=True))
        assert back.json()["impacted_clients"] == [] and back.json()["warning"] is None
        assert readiness(root)["readiness"] == "ready"

    def test_a_model_nobody_uses_is_switched_off_without_a_warning(self, fx, root):
        response = root.put(M + "/spare-id", json=body("Spare", "spare-id", active=False))
        assert response.json()["impacted_clients"] == [] and response.json()["warning"] is None

    def test_a_model_that_was_already_off_costs_nothing_new(self, fx, root):
        root.put(M + "/sol-id", json=body("Sol", "sol-id", active=False))
        again = root.put(M + "/sol-id", json=body("Sol, renamed", "sol-id", active=False))
        assert again.status_code == 200 and again.json()["impacted_clients"] == []

    def test_a_disabled_client_is_named_too(self, fx, root):
        response = root.put(M + "/lune-id", json=body("Lune", "lune-id", active=False))
        assert response.json()["impacted_clients"] == ["dormant"]


class TestTheGenericCredentialRoutesAreProtectedToo:
    def raw(self, *models):
        import json

        return json.dumps([{"name": n, "id": i, "active": a, "key": k} for n, i, a, k in models])

    def test_writing_a_list_without_a_used_model_is_refused(self, fx, root):
        refused = put(root, f"{G}/openai.models", self.raw(("Lune", "lune-id", True, "lune")))
        assert refused.status_code == 409
        assert refused.json()["detail"]["client_ids"] == ["alpha"]
        assert ids(root) == ["sol-id", "lune-id", "spare-id"]  # the list is as it was

    def test_writing_a_list_that_switches_a_used_model_off_is_refused(self, fx, root):
        refused = put(
            root,
            f"{G}/openai.models",
            self.raw(("Sol", "sol-id", False, "sol"), ("Lune", "lune-id", True, "lune")),
        )
        assert refused.status_code == 409
        assert "its own routes" in refused.json()["detail"]["message"]

    def test_writing_a_list_that_keeps_every_used_model_on_is_allowed(self, fx, root):
        kept = put(
            root,
            f"{G}/openai.models",
            self.raw(
                ("Sol", "sol-id", True, "sol"),
                ("Lune", "lune-id", True, "lune"),
                ("New", "new-id", True, "new"),
            ),
        )
        assert kept.status_code == 200
        assert ids(root) == ["sol-id", "lune-id", "new-id"]

    def test_removing_the_list_is_refused_while_a_model_is_used(self, fx, root):
        refused = root.delete(f"{G}/openai.models?confirm=true")
        assert refused.status_code == 409
        assert ids(root) == ["sol-id", "lune-id", "spare-id"]

    def test_removing_the_list_is_allowed_when_nothing_is_used(self, fx):
        client = fx.login("root")
        add(client, "Spare", "spare-id")
        assert client.delete(f"{G}/openai.models?confirm=true").status_code == 200

    def test_another_credential_is_not_concerned(self, fx, root):
        assert put(root, f"{G}/openai.api_key", SECRET).status_code == 200
        assert put(root, f"{G}/openai.organization", "org-abc").status_code == 200

    def test_an_invalid_value_is_still_reported_as_invalid_not_as_a_conflict(self, fx):
        client = fx.login("root")
        put(client, f"{G}/openai.model_sol", "legacy-sol")
        assign(client, "alpha", "legacy-sol")
        assert put(client, f"{G}/openai.model_sol", "has space").status_code == 422

    def test_the_older_per_model_credentials_are_protected_while_they_stand_in(self, fx):
        client = fx.login("root")
        put(client, f"{G}/openai.model_sol", "legacy-sol")
        put(client, f"{G}/openai.model_lune", "legacy-lune")
        assign(client, "alpha", "legacy-sol")
        assert put(client, f"{G}/openai.model_sol", "legacy-sol-2").status_code == 409
        assert client.delete(f"{G}/openai.model_sol?confirm=true").status_code == 409
        # The one nobody uses is free, and so is adding another.
        assert put(client, f"{G}/openai.model_lune", "legacy-lune-2").status_code == 200
        assert client.delete(f"{G}/openai.model_lune?confirm=true").status_code == 200
        assert [m["id"] for m in client.get(M).json()["models"]] == ["legacy-sol"]

    def test_the_older_credentials_stop_mattering_once_the_list_is_stored(self, fx, root):
        # `openai.models` is stored, so these are no longer what offers the models.
        assert put(root, f"{G}/openai.model_sol", "legacy-sol").status_code == 200


class TestWhoMayChangeTheList:
    def test_only_an_owner_and_the_check_does_not_replace_the_role_check(self, fx, root):
        editor = fx.login("eve")
        assert editor.delete(M + "/sol-id").status_code == 403  # not 409: the role comes first
        assert editor.put(M + "/sol-id", json=body("Sol", "x")).status_code == 403
        assert put(editor, f"{G}/openai.models", "[]").status_code == 403
        assert editor.delete(f"{G}/openai.models?confirm=true").status_code == 403

    def test_anyone_may_still_read_the_list(self, fx, root):
        assert fx.login("eve").get(M).status_code == 200


class TestWhichClientsUseWhichModel:
    @staticmethod
    def running(client_id, provider, model):
        return SimpleNamespace(
            client_id=client_id,
            spec=SimpleNamespace(llm=SimpleNamespace(provider=provider, model=model)),
        )

    def test_the_published_model_the_yaml_and_the_running_one_all_count(self, tmp_path):
        write_client(tmp_path, "alpha")  # its client.yaml names "test-model"
        write_client(tmp_path, "_template")  # never a client
        store = InMemoryConfigStore()
        used = models_in_use(
            tmp_path, store, [self.running("alpha", "openai", "built-with-this")], "openai"
        )
        assert used == {"test-model": ["alpha"], "built-with-this": ["alpha"]}

    def test_a_model_of_another_provider_is_not_counted(self, tmp_path):
        write_client(tmp_path, "alpha")
        used = models_in_use(
            tmp_path, InMemoryConfigStore(), [self.running("alpha", "meta", "other")], "openai"
        )
        assert "other" not in used

    def test_a_directory_that_does_not_exist_has_no_clients(self, tmp_path):
        assert models_in_use(tmp_path / "missing", InMemoryConfigStore(), [], "openai") == {}


class TestImpact:
    def models(self, *spec):
        return [OfferedModel(name, model_id, active) for name, model_id, active in spec]

    def test_what_is_removed_and_what_is_switched_off(self):
        before = self.models(
            ("A", "a", True), ("B", "b", True), ("C", "c", True), ("D", "d", False)
        )
        after = self.models(("A2", "a", True), ("B", "b", False), ("D", "d", False))
        used = {"a": ["x"], "b": ["y", "z"], "c": ["x"], "d": ["w"], "e": ["v"]}
        result = impact(before, after, used)
        assert dict(result.removed) == {"c": ("x",)}
        assert dict(result.deactivated) == {"b": ("y", "z")}
        assert result.removed_clients == ["x"] and result.deactivated_clients == ["y", "z"]

    def test_a_changed_id_is_a_removal(self):
        result = impact(self.models(("A", "a", True)), self.models(("A", "a2", True)), {"a": ["x"]})
        assert dict(result.removed) == {"a": ("x",)}

    def test_nothing_used_nothing_to_report(self):
        result = impact(self.models(("A", "a", True)), [], {})
        assert not result.removed and not result.deactivated
