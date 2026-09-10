"""Site identity, trust, and the central knowledge service:
enrollment, signed push/pull, immediate publish, and k-anonymity vocabulary
(docs/brain-sync.md)."""

from __future__ import annotations

import atexit
import os
import tempfile

import pytest

# Point the central store at a throwaway temp file BEFORE any central module
# import binds its engine — same pattern as tests/conftest.py for LESARIN_DB.
_fd, _tmp_central_db = tempfile.mkstemp(suffix=".central-test.db")
os.close(_fd)
os.environ["CENTRAL_DB"] = _tmp_central_db
os.environ["CENTRAL_ADMIN_BOOTSTRAP_TOKEN"] = "test-bootstrap-secret"
os.environ["CENTRAL_VOCAB_K"] = "2"
atexit.register(lambda: os.path.exists(_tmp_central_db) and os.remove(_tmp_central_db))

from fastapi.testclient import TestClient

from central.app import app
from central.db import Base, engine
from site_agent.identity import SiteIdentity


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client, tmp_path):
    r = client.post("/admin/bootstrap", json={
        "email": "root@central.test", "password": "adminpass1",
        "bootstrap_token": "test-bootstrap-secret",
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_site_identity(tmp_path, name="site") -> SiteIdentity:
    return SiteIdentity(key_path=tmp_path / f"{name}.pem")


def _enroll_and_activate(client, admin_headers, identity: SiteIdentity, name: str) -> None:
    tok = client.post(
        "/admin/enrollment-tokens", headers=admin_headers, json={"note": name}
    ).json()["token"]
    enrolled = client.post("/sites/enroll", json={
        "name": name, "public_key": identity.public_pem.decode("ascii"), "enrollment_token": tok,
    })
    assert enrolled.status_code == 200, enrolled.text
    site_id = enrolled.json()["id"]
    activated = client.post(f"/admin/sites/{site_id}/activate", headers=admin_headers)
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"


def _bundle(identifier="314188", output="VendorNumber", label="Veitara nr.") -> dict:
    return {
        "bundle_version": 1,
        "confirmed_templates": [
            {"identifier": identifier, "identifier_kind": "vtal", "name": "Effo",
             "match_keywords": [], "layout_fingerprint": f"fp-{label}",
             "mappings": [{"output": output, "strategy": "label", "label": label,
                           "relation": "right", "value_type": "string", "page": None, "bbox": None,
                           "confirmed": True}]},
        ],
        "label_observations": [
            {"identifier": identifier, "layout_fingerprint": f"fp-{label}",
             "label_set": [label.lower()],
             "positions": [{"label": label.lower(), "suggested_key": output,
                            "x_frac": 0.1, "y_frac": 0.2}]},
        ],
    }


# --- Bootstrap + enrollment --------------------------------------------------

def test_bootstrap_requires_correct_token(client):
    r = client.post("/admin/bootstrap", json={
        "email": "x@y.com", "password": "adminpass1", "bootstrap_token": "wrong",
    })
    assert r.status_code == 401


def test_enroll_requires_valid_one_time_token(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path)
    bad = client.post("/sites/enroll", json={
        "name": "s1", "public_key": identity.public_pem.decode("ascii"), "enrollment_token": "bogus",
    })
    assert bad.status_code == 401

    tok = client.post("/admin/enrollment-tokens", headers=admin_headers, json={"note": "s1"}).json()["token"]
    ok = client.post("/sites/enroll", json={
        "name": "s1", "public_key": identity.public_pem.decode("ascii"), "enrollment_token": tok,
    })
    assert ok.status_code == 200
    assert ok.json()["status"] == "pending"

    # The same one-time token can't be reused.
    replay = client.post("/sites/enroll", json={
        "name": "s2", "public_key": identity.public_pem.decode("ascii"), "enrollment_token": tok,
    })
    assert replay.status_code == 401


def test_unactivated_site_cannot_push(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path)
    tok = client.post("/admin/enrollment-tokens", headers=admin_headers, json={"note": "s1"}).json()["token"]
    client.post("/sites/enroll", json={
        "name": "s1", "public_key": identity.public_pem.decode("ascii"), "enrollment_token": tok,
    })
    r = client.post(
        "/sync/push", json=_bundle(),
        headers={"Authorization": f"Bearer {identity.signed_jwt()}"},
    )
    assert r.status_code == 401  # still "pending", not "active"


def test_revoked_site_cannot_push(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path)
    _enroll_and_activate(client, admin_headers, identity, "s1")
    sites = client.get("/admin/sites", headers=admin_headers).json()
    site_id = next(s["id"] for s in sites if s["fingerprint"] == identity.fingerprint)
    client.post(f"/admin/sites/{site_id}/revoke", headers=admin_headers)

    r = client.post(
        "/sync/push", json=_bundle(),
        headers={"Authorization": f"Bearer {identity.signed_jwt()}"},
    )
    assert r.status_code == 401


def test_tampered_signature_rejected(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path)
    _enroll_and_activate(client, admin_headers, identity, "s1")
    token = identity.signed_jwt()
    tampered = token[:-4] + ("0000" if not token.endswith("0000") else "1111")
    r = client.post("/sync/push", json=_bundle(), headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401


# --- Push publishes immediately; vocabulary needs k sites --------------------

def test_push_publishes_the_template_immediately(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path, "s1")
    _enroll_and_activate(client, admin_headers, identity, "s1")
    r = client.post(
        "/sync/push", json=_bundle(),
        headers={"Authorization": f"Bearer {identity.signed_jwt()}"},
    )
    assert r.status_code == 200
    assert r.json()["templates_created"] == 1

    # No approval step: a single confirmed push is already pullable.
    pulled = client.get("/sync/pull", headers={"Authorization": f"Bearer {identity.signed_jwt()}"})
    templates = pulled.json()["templates"]
    assert len(templates) == 1 and templates[0]["identifier"] == "314188"
    # But the vocabulary (k=2 here) hasn't crossed the threshold from one site.
    assert pulled.json()["vocabulary"] == []


def test_a_second_site_reveals_the_vocabulary(client, admin_headers, tmp_path):
    s1 = _new_site_identity(tmp_path, "s1")
    s2 = _new_site_identity(tmp_path, "s2")
    _enroll_and_activate(client, admin_headers, s1, "s1")
    _enroll_and_activate(client, admin_headers, s2, "s2")

    client.post("/sync/push", json=_bundle(), headers={"Authorization": f"Bearer {s1.signed_jwt()}"})
    r2 = client.post("/sync/push", json=_bundle(), headers={"Authorization": f"Bearer {s2.signed_jwt()}"})
    assert r2.json()["vocabulary_revealed"] == 1  # CENTRAL_VOCAB_K=2 → crossed the threshold

    pulled = client.get("/sync/pull", headers={"Authorization": f"Bearer {s1.signed_jwt()}"})
    vocab = pulled.json()["vocabulary"]
    assert vocab and vocab[0]["key"] == "VendorNumber" and "veitara nr." in vocab[0]["aliases"]

    vocab_admin = client.get("/admin/vocabulary", headers=admin_headers).json()
    row = next(v for v in vocab_admin if v["label"] == "veitara nr.")
    assert row["sites"] == 2 and row["revealed"] is True


def test_a_later_confirmed_push_replaces_the_same_layout(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path, "s1")
    _enroll_and_activate(client, admin_headers, identity, "s1")
    client.post("/sync/push", json=_bundle(), headers={"Authorization": f"Bearer {identity.signed_jwt()}"})

    changed = _bundle()
    changed["confirmed_templates"][0]["mappings"][0]["label"] = "Veitara nr. (new)"
    r = client.post(
        "/sync/push", json=changed, headers={"Authorization": f"Bearer {identity.signed_jwt()}"}
    )
    assert r.json()["templates_replaced"] == 1  # same identifier+fingerprint → replace, not duplicate

    templates = client.get("/admin/templates", headers=admin_headers).json()
    assert len(templates) == 1 and templates[0]["version"] == 2


def test_admin_can_withdraw_a_template(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path, "s1")
    _enroll_and_activate(client, admin_headers, identity, "s1")
    client.post("/sync/push", json=_bundle(), headers={"Authorization": f"Bearer {identity.signed_jwt()}"})

    templates = client.get("/admin/templates", headers=admin_headers).json()
    template_id = templates[0]["id"]
    withdrawn = client.post(f"/admin/templates/{template_id}/withdraw", headers=admin_headers)
    assert withdrawn.status_code == 200 and withdrawn.json()["withdrawn"] is True

    pulled = client.get("/sync/pull", headers={"Authorization": f"Bearer {identity.signed_jwt()}"})
    assert pulled.json()["templates"] == []  # withdrawn templates don't flow out anymore


def test_site_jwt_verifies_against_the_registered_public_key(client, admin_headers, tmp_path):
    """Direct check on the crypto plumbing: a real Ed25519 signature, verified
    centrally against the fingerprint-keyed public key on file."""
    from central.db import SessionLocal
    from central.trust import verify_site_jwt

    identity = _new_site_identity(tmp_path)
    _enroll_and_activate(client, admin_headers, identity, "s1")

    with SessionLocal() as session:
        site = verify_site_jwt(session, identity.signed_jwt())
        assert site is not None
        assert site.fingerprint == identity.fingerprint


# --- Stage G: evidence-based auto-withdrawal ----------------------------------

def _push_with_outcome(client, identity, valid: int, invalid: int) -> dict:
    bundle = _bundle()
    bundle["template_outcomes"] = [
        {"identifier": "314188", "identifier_kind": "vtal", "valid": valid, "invalid": invalid}
    ]
    r = client.post(
        "/sync/push", json=bundle, headers={"Authorization": f"Bearer {identity.signed_jwt()}"}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_one_site_reporting_failures_does_not_auto_withdraw(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path, "s1")
    _enroll_and_activate(client, admin_headers, identity, "s1")
    result = _push_with_outcome(client, identity, valid=0, invalid=5)
    assert result["auto_withdrawn"] == []  # only one site's evidence — not enough

    templates = client.get("/admin/templates", headers=admin_headers).json()
    assert templates[0]["withdrawn"] is False
    assert templates[0]["valid_count"] == 0 and templates[0]["invalid_count"] == 5
    assert templates[0]["reporting_sites"] == 1


def test_two_sites_reporting_failures_auto_withdraws(client, admin_headers, tmp_path):
    s1 = _new_site_identity(tmp_path, "s1")
    s2 = _new_site_identity(tmp_path, "s2")
    _enroll_and_activate(client, admin_headers, s1, "s1")
    _enroll_and_activate(client, admin_headers, s2, "s2")

    _push_with_outcome(client, s1, valid=0, invalid=3)
    result = _push_with_outcome(client, s2, valid=0, invalid=3)
    assert result["auto_withdrawn"] == ["314188"]

    templates = client.get("/admin/templates", headers=admin_headers).json()
    assert templates[0]["withdrawn"] is True
    assert templates[0]["reporting_sites"] == 2
    assert templates[0]["invalid_count"] == 6

    pulled = client.get("/sync/pull", headers={"Authorization": f"Bearer {s1.signed_jwt()}"})
    assert pulled.json()["templates"] == []  # auto-withdrawn — no longer pulled


def test_mostly_valid_outcomes_do_not_auto_withdraw(client, admin_headers, tmp_path):
    s1 = _new_site_identity(tmp_path, "s1")
    s2 = _new_site_identity(tmp_path, "s2")
    _enroll_and_activate(client, admin_headers, s1, "s1")
    _enroll_and_activate(client, admin_headers, s2, "s2")

    _push_with_outcome(client, s1, valid=8, invalid=1)
    result = _push_with_outcome(client, s2, valid=9, invalid=0)
    assert result["auto_withdrawn"] == []

    templates = client.get("/admin/templates", headers=admin_headers).json()
    assert templates[0]["withdrawn"] is False


# --- Output-name presets: hashed until k sites independently agree -----------

def _bundle_with_output_name(canonical="InvoiceNo", output_name="Bilagsnr") -> dict:
    bundle = _bundle()
    bundle["output_name_observations"] = [{"canonical": canonical, "output_name": output_name}]
    return bundle


def test_output_name_stays_hidden_until_k_sites_agree(client, admin_headers, tmp_path):
    identity = _new_site_identity(tmp_path, "s1")
    _enroll_and_activate(client, admin_headers, identity, "s1")
    r = client.post(
        "/sync/push", json=_bundle_with_output_name(),
        headers={"Authorization": f"Bearer {identity.signed_jwt()}"},
    )
    assert r.json()["presets_revealed"] == 0

    presets = client.get("/admin/output-name-presets", headers=admin_headers).json()
    row = next(p for p in presets if p["canonical"] == "InvoiceNo")
    # Only a hash and a count on file — the name itself isn't held yet.
    assert row["sites"] == 1 and row["revealed"] is False and row["name"] is None

    pulled = client.get("/sync/pull", headers={"Authorization": f"Bearer {identity.signed_jwt()}"})
    assert pulled.json()["output_name_presets"] == []


def test_a_second_site_reveals_the_output_name_preset(client, admin_headers, tmp_path):
    s1 = _new_site_identity(tmp_path, "s1")
    s2 = _new_site_identity(tmp_path, "s2")
    _enroll_and_activate(client, admin_headers, s1, "s1")
    _enroll_and_activate(client, admin_headers, s2, "s2")

    client.post(
        "/sync/push", json=_bundle_with_output_name(), headers={"Authorization": f"Bearer {s1.signed_jwt()}"}
    )
    r2 = client.post(
        "/sync/push", json=_bundle_with_output_name(), headers={"Authorization": f"Bearer {s2.signed_jwt()}"}
    )
    assert r2.json()["presets_revealed"] == 1  # CENTRAL_VOCAB_K=2 → crossed the threshold

    presets = client.get("/admin/output-name-presets", headers=admin_headers).json()
    row = next(p for p in presets if p["canonical"] == "InvoiceNo")
    assert row["revealed"] is True and row["name"] == "Bilagsnr"

    pulled = client.get("/sync/pull", headers={"Authorization": f"Bearer {s1.signed_jwt()}"})
    assert pulled.json()["output_name_presets"] == [{"key": "InvoiceNo", "names": ["Bilagsnr"]}]


def test_different_output_names_for_the_same_field_are_counted_separately(client, admin_headers, tmp_path):
    s1 = _new_site_identity(tmp_path, "s1")
    s2 = _new_site_identity(tmp_path, "s2")
    _enroll_and_activate(client, admin_headers, s1, "s1")
    _enroll_and_activate(client, admin_headers, s2, "s2")

    client.post(
        "/sync/push", json=_bundle_with_output_name(output_name="Bilagsnr"),
        headers={"Authorization": f"Bearer {s1.signed_jwt()}"},
    )
    r2 = client.post(
        "/sync/push", json=_bundle_with_output_name(output_name="inv_no_kommuna_fin"),
        headers={"Authorization": f"Bearer {s2.signed_jwt()}"},
    )
    assert r2.json()["presets_revealed"] == 0  # two different names, one site behind each so far
