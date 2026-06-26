"""Exporters: a canonical record → json / xml / ubl / oioubl."""

from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from app import exporters
from app.exporters import CanonicalInvoice, CanonicalLine


@pytest.fixture()
def invoice() -> CanonicalInvoice:
    return CanonicalInvoice(
        values={
            "VendorName": "Effo P/F",
            "VendorNo": "314188",
            "InvoiceNo": "2026-0014",
            "InvoiceDate": "2026-01-12",
            "DueDate": "2026-01-26",
            "Currency": "DKK",
            "TotalExclVat": "132.00",
            "Vat": "33.00",
            "TotalInclVat": "165.00",
            "AccountNo": "FO12 3456",
        },
        lines=[CanonicalLine(description="Kaffi", quantity="2", unit="STK",
                             unit_price="45.00", amount="90.00")],
    )


def test_json_uses_custom_output_names(invoice):
    profile = [
        {"canonical": "InvoiceNo", "output_name": "invoice_id"},
        {"canonical": "TotalInclVat", "output_name": "gross"},
    ]
    out = json.loads(exporters.to_json(invoice, profile))
    assert out["invoice_id"] == "2026-0014"
    assert out["gross"] == "165.00"
    assert "VendorName" not in out  # not selected by the profile
    assert out["lines"][0]["description"] == "Kaffi"


def test_json_without_profile_emits_all_present(invoice):
    out = json.loads(exporters.to_json(invoice))
    assert out["VendorName"] == "Effo P/F"
    assert out["Currency"] == "DKK"


def test_xml_sanitises_field_names(invoice):
    profile = [{"canonical": "InvoiceNo", "output_name": "Invoice Id!"}]
    root = ET.fromstring(exporters.to_xml(invoice, profile))
    # The space/'!' are not valid in an XML tag → sanitised to underscores.
    tag = root.find("Invoice_Id_")
    assert tag is not None and tag.text == "2026-0014"


def test_ubl_structure(invoice):
    xml = exporters.to_ubl(invoice)
    ns = {
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    }
    root = ET.fromstring(xml)
    assert root.find("cbc:ID", ns).text == "2026-0014"
    assert root.find("cbc:DocumentCurrencyCode", ns).text == "DKK"
    name = root.find("cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name", ns)
    assert name.text == "Effo P/F"
    payable = root.find("cac:LegalMonetaryTotal/cbc:PayableAmount", ns)
    assert payable.text == "165.00" and payable.get("currencyID") == "DKK"
    assert root.find("cac:InvoiceLine/cac:Item/cbc:Description", ns).text == "Kaffi"


def test_oioubl_adds_customisation(invoice):
    xml = exporters.to_ubl(invoice, oioubl=True)
    ns = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}
    root = ET.fromstring(xml)
    assert root.find("cbc:CustomizationID", ns).text == "OIOUBL-2.02"
    assert root.find("cbc:ProfileID", ns) is not None


def test_ubl_profile_omits_unselected_fields(invoice):
    # A profile that excludes the bank account must not emit PaymentMeans.
    profile = [{"canonical": "InvoiceNo", "output_name": "InvoiceNo"}]
    ns = {"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}
    root = ET.fromstring(exporters.to_ubl(invoice, profile_fields=profile))
    assert root.find("cac:PaymentMeans", ns) is None


def test_render_dispatch(invoice):
    assert exporters.render(invoice, "json").media_type == "application/json"
    assert exporters.render(invoice, "oioubl").extension == "xml"
    with pytest.raises(ValueError):
        exporters.render(invoice, "csv")
