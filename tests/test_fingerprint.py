"""Layout fingerprinting + label harvesting (docs/brain-sync.md Stage B):
built only from recognised labels, queued locally on every read, and carried
in app.brain's push bundle for the next central push."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from app import brain, fingerprint, repo
from app.db import Base, SessionLocal, engine, init_db
from app.extraction import loader
from app.extraction import template as templater
from app.extraction.fields import load_config
from app.main import app


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
        yield c


def _invoice_with_vtal(vtal="314188") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    s = getSampleStyleSheet()
    doc.build([
        Paragraph("Effo P/F", s["Title"]),
        Paragraph(f"Vtal: {vtal}", s["Normal"]),
        Paragraph("Fakturanr: 2026-0099", s["Normal"]),
        Paragraph("Fakturadato: 12-01-2026", s["Normal"]),
    ])
    return buf.getvalue()


def _register(client, email="a@b.com", password="password12"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_build_fingerprint_uses_only_recognised_labels():
    pdf = _invoice_with_vtal()
    document = loader.load(pdf)
    suggestions = templater.field_suggestions(document, load_config())
    fp = fingerprint.build_fingerprint(document, suggestions)

    assert fp["label_set"]
    assert fp["layout_fingerprint"]  # a real hash, not empty
    for pos in fp["positions"]:
        assert 0 <= pos["x_frac"] <= 1 and 0 <= pos["y_frac"] <= 1
        assert pos["label"] in fp["label_set"]
        assert "suggested_key" in pos
    # Never a raw value like the invoice number leaks in as a "label".
    assert not any("2026-0099" in lbl for lbl in fp["label_set"])


def test_fingerprint_is_stable_for_the_same_shape_and_differs_for_another():
    same_a = fingerprint.build_fingerprint(
        loader.load(_invoice_with_vtal("111111")),
        templater.field_suggestions(loader.load(_invoice_with_vtal("111111")), load_config()),
    )
    same_b = fingerprint.build_fingerprint(
        loader.load(_invoice_with_vtal("222222")),  # different V-tal, same layout
        templater.field_suggestions(loader.load(_invoice_with_vtal("222222")), load_config()),
    )
    assert same_a["layout_fingerprint"] == same_b["layout_fingerprint"]


def test_every_read_queues_a_label_observation(client):
    h = _register(client)
    assert client.post(
        "/api/me/export", headers=h,
        files={"file": ("inv.pdf", _invoice_with_vtal(), "application/pdf")},
    ).status_code == 200

    with SessionLocal() as session:
        observations = repo.list_label_observations(session)
        assert len(observations) == 1
        assert observations[0].label_set
        assert observations[0].identifier == "314188"  # known this time


# --- app.brain: push bundle only carries confirmed knowledge ----------------

def test_push_bundle_excludes_unconfirmed_auto_learned_vendors(client):
    h = _register(client)
    client.post(
        "/api/me/export", headers=h,
        files={"file": ("inv.pdf", _invoice_with_vtal(), "application/pdf")},
    )
    with SessionLocal() as session:
        vendors = repo.list_vendors(session)
        assert len(vendors) == 1  # auto-learned locally...
        assert not repo.confirmed_mappings_of(vendors[0])  # ...but unconfirmed

        bundle = brain.export_push_bundle(session)
        assert bundle["confirmed_templates"] == []  # nothing crosses the wire yet
        assert bundle["label_observations"]  # but the harvest does


def test_studio_saved_mappings_are_confirmed_and_pushable(client):
    with SessionLocal() as session:
        repo.create_vendor(
            session, identifier="900100", name="Confirmed Co",
            mappings=[{"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr",
                       "confirmed": True}],
        )
        bundle = brain.export_push_bundle(session)
        assert len(bundle["confirmed_templates"]) == 1
        tpl = bundle["confirmed_templates"][0]
        assert tpl["identifier"] == "900100"
        assert tpl["layout_fingerprint"]
        assert tpl["mappings"][0]["output"] == "InvoiceNo"


def test_merge_pull_bundle_adds_new_template_as_confirmed():
    incoming = {
        "bundle_version": brain.BRAIN_BUNDLE_VERSION,
        "templates": [{
            "identifier": "700200", "identifier_kind": "vtal", "name": "Central Vendor",
            "match_keywords": [],
            "mappings": [{"output": "InvoiceNo", "strategy": "label", "label": "Fakturanr",
                          "relation": "right", "value_type": "string", "page": None, "bbox": None}],
        }],
        "vocabulary": [],
    }
    with SessionLocal() as session:
        stats = brain.merge_pull_bundle(session, incoming)
        assert stats["templates_added"] == 1

        vendor = repo.get_vendor_by_identifier(session, "700200")
        assert vendor is not None
        mappings = repo.mappings_of(vendor)
        assert mappings[0]["confirmed"] is True  # published centrally = confirmed


def test_merge_pull_bundle_widens_local_aliases():
    with SessionLocal() as session:
        repo.upsert_output_field(session, "InvoiceNo", value_type="string", aliases=["Fakturanr"])
        stats = brain.merge_pull_bundle(session, {
            "bundle_version": brain.BRAIN_BUNDLE_VERSION,
            "templates": [],
            "vocabulary": [{"key": "InvoiceNo", "aliases": ["Invoice No", "Bilagsnr"]}],
        })
        assert stats["vocabulary_updated"] == 1
        field = next(f for f in repo.list_output_fields(session) if f.key == "InvoiceNo")
        assert set(field.aliases) == {"Fakturanr", "Invoice No", "Bilagsnr"}
