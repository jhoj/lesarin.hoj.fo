"""Template edits must be recoverable.

Saving a vendor replaces its mappings wholesale, and these templates are
shared — every customer's extraction depends on them. So each change keeps a
snapshot, and any snapshot can be put back, including the one taken just
before a delete.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, repo
from app.db import Base, SessionLocal, engine, init_db
from app.main import app

GOOD = [{"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr", "relation": "right"}]
CARELESS = [{"output": "InvoiceNo", "strategy": "label", "label": "wrong", "relation": "right"}]


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    init_db()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        token = c.post(
            "/api/auth/register", json={"email": "staff@lesarin.fo", "password": "password1"}
        ).json()["token"]
        with SessionLocal() as session:
            auth.set_staff(session, "staff@lesarin.fo")
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def _create(client, mappings=GOOD):
    return client.post(
        "/api/vendors",
        json={"identifier": "314188", "name": "Effo", "match_keywords": [], "mappings": mappings},
    ).json()


# --- The repository layer --------------------------------------------------

def test_every_change_is_snapshotted():
    with SessionLocal() as session:
        vendor = repo.create_vendor(session, identifier="314188", name="Effo", mappings=GOOD)
        repo.update_vendor(session, vendor.id, mappings=CARELESS)

        versions = repo.list_versions(session, "314188")
        assert [v.version for v in versions] == [2, 1]  # newest first
        assert [v.change for v in versions] == ["updated", "created"]
        assert versions[1].mappings[0]["label"] == "Fakturanr"
        assert versions[0].mappings[0]["label"] == "wrong"


def test_a_deleted_template_is_kept_and_restorable():
    with SessionLocal() as session:
        vendor = repo.create_vendor(session, identifier="314188", name="Effo", mappings=GOOD)
        repo.delete_vendor(session, vendor.id)

        assert repo.get_vendor_by_identifier(session, "314188") is None
        versions = repo.list_versions(session, "314188")
        assert versions[0].change == "deleted"
        assert versions[0].vendor_id is None  # the vendor row is gone, this survived

        restored = repo.restore_version(session, versions[0].id)
        assert restored is not None
        assert restored.identifier == "314188"
        assert [m.source_label for m in restored.mappings] == ["Fakturanr"]


def test_restoring_is_itself_a_version():
    """Going back must be as reversible as the edit that caused it."""
    with SessionLocal() as session:
        vendor = repo.create_vendor(session, identifier="314188", name="Effo", mappings=GOOD)
        repo.update_vendor(session, vendor.id, mappings=CARELESS)
        good_version = [v for v in repo.list_versions(session, "314188") if v.version == 1][0]

        repo.restore_version(session, good_version.id)

        versions = repo.list_versions(session, "314188")
        assert versions[0].change == "restored"
        assert versions[0].version == 3
        assert versions[0].mappings[0]["label"] == "Fakturanr"


# --- Over HTTP -------------------------------------------------------------

def test_undoing_a_careless_edit_through_the_api(client):
    vendor = _create(client)
    client.put(
        f"/api/vendors/{vendor['id']}",
        json={"identifier": "314188", "name": "Effo", "match_keywords": [], "mappings": CARELESS},
    )
    assert client.get(f"/api/vendors/{vendor['id']}").json()["mappings"][0]["label"] == "wrong"

    versions = client.get(f"/api/vendors/{vendor['id']}/versions").json()
    original = [v for v in versions if v["version"] == 1][0]
    assert original["mappings"][0]["label"] == "Fakturanr"

    restored = client.post(f"/api/vendor-versions/{original['id']}/restore").json()
    assert restored["mappings"][0]["label"] == "Fakturanr"
    assert client.get(f"/api/vendors/{vendor['id']}").json()["mappings"][0]["label"] == "Fakturanr"


def test_a_deleted_vendor_is_found_by_identifier_and_recreated(client):
    vendor = _create(client)
    assert client.delete(f"/api/vendors/{vendor['id']}").status_code == 200
    assert client.get(f"/api/vendors/{vendor['id']}").status_code == 404

    versions = client.get("/api/vendor-versions?identifier=314188").json()
    assert versions[0]["change"] == "deleted"
    assert versions[0]["live"] is False

    recreated = client.post(f"/api/vendor-versions/{versions[0]['id']}/restore").json()
    assert recreated["identifier"] == "314188"
    assert recreated["mappings"][0]["label"] == "Fakturanr"


def test_changes_are_attributed(client):
    vendor = _create(client)
    versions = client.get(f"/api/vendors/{vendor['id']}/versions").json()
    assert versions[0]["changed_by_user_id"] is not None


def test_history_is_staff_only(client):
    vendor = _create(client)
    token = client.post(
        "/api/auth/register", json={"email": "customer@firm.fo", "password": "password1"}
    ).json()["token"]
    customer = {"Authorization": f"Bearer {token}"}

    assert client.get(f"/api/vendors/{vendor['id']}/versions", headers=customer).status_code == 403
    assert client.get("/api/vendor-versions?identifier=314188", headers=customer).status_code == 403
    assert client.post("/api/vendor-versions/1/restore", headers=customer).status_code == 403


def test_restoring_something_that_never_existed(client):
    assert client.post("/api/vendor-versions/9999/restore").status_code == 404
