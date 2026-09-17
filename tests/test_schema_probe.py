"""Finding a target application's field contract from a logged-in session.

Every target here is a fake served by an httpx MockTransport — the probe never
touches a network, and the fixtures double as a record of the shapes real
generators emit (ASP.NET Swagger, springdoc, OData EDMX, GraphQL).
"""

from __future__ import annotations

import json

import httpx
import pytest

from site_agent import schema_probe


OPENAPI_DOC = {
    "openapi": "3.0.1",
    "info": {"title": "Vatta-like workflow API"},
    "paths": {
        "/api/auth/login": {
            "post": {
                "summary": "Log in",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                }}}},
            }
        },
        "/api/purchase-invoices": {
            "post": {
                "summary": "Create a purchase invoice for approval",
                "tags": ["Invoices"],
                "requestBody": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/PurchaseInvoice"}
                }}},
            }
        },
    },
    "components": {
        "schemas": {
            "DocumentBase": {
                "type": "object",
                "required": ["documentDate"],
                "properties": {
                    "documentDate": {"type": "string", "format": "date"},
                    "reference": {"type": "string", "maxLength": 35},
                },
            },
            "PurchaseInvoice": {
                "allOf": [
                    {"$ref": "#/components/schemas/DocumentBase"},
                    {
                        "type": "object",
                        "required": ["invoiceNumber", "vendorNo"],
                        "properties": {
                            "invoiceNumber": {"type": "string", "title": "Invoice number"},
                            "vendorNo": {"type": "string"},
                            "dueDate": {"type": "string", "format": "date"},
                            "currency": {"type": "string", "enum": ["DKK", "EUR"]},
                            "totalInclVat": {"type": "number"},
                            "approver": {
                                "type": "object",
                                "properties": {"email": {"type": "string"}},
                            },
                            "lines": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/InvoiceLine"},
                            },
                        },
                    },
                ]
            },
            "InvoiceLine": {
                "type": "object",
                "required": ["amount"],
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "amount": {"type": "number"},
                },
            },
        }
    },
}

SWAGGER2_DOC = {
    "swagger": "2.0",
    "paths": {
        "/faktura": {
            "post": {
                "summary": "Opret faktura",
                "parameters": [
                    {"in": "query", "name": "dryRun", "type": "boolean"},
                    {"in": "body", "name": "body", "schema": {"$ref": "#/definitions/Faktura"}},
                ],
            }
        }
    },
    "definitions": {
        "Faktura": {
            "type": "object",
            "required": ["fakturaNr"],
            "properties": {
                "fakturaNr": {"type": "string"},
                "forfaldsdato": {"type": "string", "format": "date"},
                "beloeb": {"type": "number"},
            },
        }
    },
}

ODATA_METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
  <edmx:DataServices>
    <Schema Namespace="ERP" xmlns="http://docs.oasis-open.org/odata/ns/edm">
      <EntityType Name="PurchaseInvoice">
        <Key><PropertyRef Name="systemId"/></Key>
        <Property Name="systemId" Type="Edm.Guid" Nullable="false"/>
        <Property Name="invoiceNumber" Type="Edm.String" MaxLength="20" Nullable="false"/>
        <Property Name="invoiceDate" Type="Edm.Date"/>
        <Property Name="vendorNumber" Type="Edm.String" MaxLength="20"/>
        <Property Name="amountInclVat" Type="Edm.Decimal"/>
      </EntityType>
      <EntityType Name="UserSetting">
        <Property Name="theme" Type="Edm.String"/>
      </EntityType>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

GRAPHQL_INTROSPECTION = {
    "data": {"__schema": {
        "mutationType": {"name": "Mutation"},
        "types": [
            {"kind": "OBJECT", "name": "Query", "inputFields": None},
            {"kind": "INPUT_OBJECT", "name": "LoginInput", "inputFields": [
                {"name": "username", "description": None,
                 "type": {"kind": "NON_NULL", "name": None,
                          "ofType": {"kind": "SCALAR", "name": "String", "ofType": None}}},
            ]},
            {"kind": "INPUT_OBJECT", "name": "InvoiceInput", "inputFields": [
                {"name": "invoiceNo", "description": "Supplier's invoice number",
                 "type": {"kind": "NON_NULL", "name": None,
                          "ofType": {"kind": "SCALAR", "name": "String", "ofType": None}}},
                {"name": "dueDate", "description": None,
                 "type": {"kind": "SCALAR", "name": "Date", "ofType": None}},
                {"name": "totalAmount", "description": None,
                 "type": {"kind": "SCALAR", "name": "Decimal", "ofType": None}},
            ]},
        ],
    }}
}

SPA_SHELL = "<!doctype html><html><head><title>Vatta</title></head><body><app-root></app-root></body></html>"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler),
                        base_url="https://target.test", follow_redirects=False)


def _serve(routes: dict, fallback_status: int = 404, fallback_body: str = "not found"):
    """Serve `routes` (path → (status, body, content-type)); everything else 404s."""
    def handler(request: httpx.Request) -> httpx.Response:
        spec = routes.get(request.url.path)
        if spec is None:
            return httpx.Response(fallback_status, text=fallback_body,
                                  headers={"content-type": "text/html"})
        status, body, ctype = spec
        if not isinstance(body, str):
            body = json.dumps(body)
        return httpx.Response(status, text=body, headers={"content-type": ctype})
    return handler


def _fields(report) -> dict:
    return {f["path"]: f for f in report["schema"]["fields"]}


# --- OpenAPI 3 -------------------------------------------------------------

def test_openapi_found_and_invoice_operation_wins_over_login():
    handler = _serve({"/openapi.json": (200, OPENAPI_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "found"
    assert report["schema"]["kind"] == "openapi3"
    assert report["schema"]["operation"] == "POST /api/purchase-invoices"
    assert report["schema"]["source_url"] == "https://target.test/openapi.json"
    # The login endpoint is still reported, just ranked below.
    assert any(c["operation"] == "POST /api/auth/login" for c in report["other_candidates"])


def test_openapi_flattens_composition_nesting_and_arrays():
    handler = _serve({"/openapi.json": (200, OPENAPI_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)
    fields = _fields(report)

    # allOf: the base type's properties are merged in, not dropped.
    assert fields["documentDate"]["type"] == "date"
    assert fields["documentDate"]["required"] is True
    assert fields["reference"]["max_length"] == 35
    # Nested object → dotted path. Array of objects → one entry per shape.
    assert fields["approver.email"]["type"] == "string"
    assert fields["lines[].amount"]["type"] == "number"
    assert fields["lines[].amount"]["required"] is True
    assert fields["lines[].description"]["required"] is False
    # The leaf name is what the canonical matcher will key on.
    assert fields["lines[].amount"]["name"] == "amount"
    assert fields["invoiceNumber"]["label"] == "Invoice number"
    assert fields["currency"]["enum"] == ["DKK", "EUR"]


def test_openapi_probes_stop_at_the_first_hit():
    handler = _serve({"/openapi.json": (200, OPENAPI_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)
    # /openapi.json is probed first, so nothing after it is requested.
    assert [p["url"] for p in report["probes"]] == ["https://target.test/openapi.json"]


def test_openapi_served_at_a_later_well_known_path():
    handler = _serve({"/swagger/v1/swagger.json": (200, OPENAPI_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)
    assert report["status"] == "found"
    assert report["schema"]["source_url"].endswith("/swagger/v1/swagger.json")


# --- Swagger 2 / OData / GraphQL ------------------------------------------

def test_swagger2_body_parameter():
    handler = _serve({"/v2/api-docs": (200, SWAGGER2_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["schema"]["kind"] == "swagger2"
    assert report["schema"]["operation"] == "POST /faktura"
    fields = _fields(report)
    assert fields["fakturaNr"]["required"] is True
    assert fields["forfaldsdato"]["type"] == "date"
    # A query parameter is not part of the body contract.
    assert "dryRun" not in fields


def test_odata_metadata_entity_types():
    handler = _serve({"/$metadata": (200, ODATA_METADATA, "application/xml")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["schema"]["kind"] == "odata"
    assert report["schema"]["operation"] == "PurchaseInvoice"
    fields = _fields(report)
    assert fields["invoiceNumber"]["required"] is True
    assert fields["invoiceNumber"]["max_length"] == 20
    assert fields["invoiceDate"]["type"] == "date"
    assert fields["amountInclVat"]["type"] == "number"
    # A server-assigned key is not something the caller has to supply.
    assert fields["systemId"]["required"] is False
    assert any(c["operation"] == "UserSetting" for c in report["other_candidates"])


def test_graphql_introspection_is_the_last_resort():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql" and request.method == "POST":
            return httpx.Response(200, json=GRAPHQL_INTROSPECTION,
                                  headers={"content-type": "application/json"})
        return httpx.Response(404, text=SPA_SHELL, headers={"content-type": "text/html"})

    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["schema"]["kind"] == "graphql"
    assert report["schema"]["operation"] == "InvoiceInput"
    fields = _fields(report)
    assert fields["invoiceNo"]["required"] is True
    assert fields["invoiceNo"]["label"] == "Supplier's invoice number"
    assert fields["dueDate"]["type"] == "date"
    # GraphQL is only tried once every GET path has come back empty.
    graphql_probes = [p for p in report["probes"] if p["method"] == "POST"]
    assert len(report["probes"]) > len(graphql_probes)


# --- Nothing to find -------------------------------------------------------

def test_spa_shell_on_every_path_is_not_a_contract():
    """The common failure: an SPA answers 200 with its HTML for any path."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SPA_SHELL, headers={"content-type": "text/html"})

    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "not_found"
    assert report["schema"] is None
    assert "observe" in report["next_step"]


def test_json_that_is_not_a_contract_is_ignored():
    handler = _serve({"/openapi.json": (200, {"message": "nope"}, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)
    assert report["status"] == "not_found"


def test_all_probes_rejected_reports_unauthorized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "unauthorized"
    assert "expired" in report["next_step"]


def test_connection_failure_reports_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "unreachable"
    assert all("error" in p for p in report["probes"])


# --- Credential safety -----------------------------------------------------

def test_cross_origin_redirect_is_refused_not_followed():
    """Following it would replay the customer's session cookie at another host."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "target.test":
            return httpx.Response(302, headers={"location": "https://evil.test/openapi.json"})
        return httpx.Response(200, json=OPENAPI_DOC, headers={"content-type": "application/json"})

    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "not_found"
    assert all("evil.test" not in url for url in seen)
    assert report["probes"][0]["skipped"] == "cross-origin redirect"


def test_same_origin_redirect_is_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(301, headers={"location": "https://target.test/v3/api-docs"})
        if request.url.path == "/v3/api-docs":
            return httpx.Response(200, json=OPENAPI_DOC, headers={"content-type": "application/json"})
        return httpx.Response(404, text="")

    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "found"


def test_report_names_auth_headers_but_never_their_values():
    headers = schema_probe.build_headers(cookie="session=super-secret", bearer="tok-secret")
    handler = _serve({"/openapi.json": (200, OPENAPI_DOC, "application/json")})
    with httpx.Client(transport=httpx.MockTransport(handler), headers=headers,
                      follow_redirects=False) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["auth_headers_sent"] == ["authorization", "cookie"]
    assert "super-secret" not in json.dumps(report)
    assert "tok-secret" not in json.dumps(report)


def test_oversized_response_is_abandoned(monkeypatch):
    monkeypatch.setattr(schema_probe, "_MAX_BYTES", 64)
    handler = _serve({"/openapi.json": (200, OPENAPI_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)

    assert report["status"] == "not_found"
    assert report["probes"][0]["skipped"] == "response too large"


def test_probes_are_reads_only():
    """Discovery must never create anything in the customer's target system."""
    methods: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        return httpx.Response(404, text="")

    with _client(handler) as c:
        schema_probe.discover("https://target.test", client=c)

    assert {m for m, _ in methods} <= {"GET", "POST"}
    assert all(path in schema_probe.GRAPHQL_PATHS for m, path in methods if m == "POST")


# --- Inputs ----------------------------------------------------------------

def test_build_headers_accepts_both_separators_and_rejects_junk():
    headers = schema_probe.build_headers(extra=["X-Tenant: acme", "X-Api-Version=3"])
    assert headers["X-Tenant"] == "acme"
    assert headers["X-Api-Version"] == "3"
    with pytest.raises(ValueError):
        schema_probe.build_headers(extra=["not-a-header"])


def test_non_http_url_is_rejected():
    with pytest.raises(ValueError):
        schema_probe.discover("file:///etc/passwd")


def test_extra_paths_are_probed():
    handler = _serve({"/internal/schema.json": (200, OPENAPI_DOC, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c,
                                       extra_paths=["/internal/schema.json"])
    assert report["status"] == "found"


def test_cyclic_ref_terminates():
    doc = {
        "openapi": "3.0.0",
        "paths": {"/invoices": {"post": {"requestBody": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Node"}}}}}}},
        "components": {"schemas": {"Node": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "parent": {"$ref": "#/components/schemas/Node"}},
        }}},
    }
    handler = _serve({"/openapi.json": (200, doc, "application/json")})
    with _client(handler) as c:
        report = schema_probe.discover("https://target.test", client=c)
    assert "name" in _fields(report)


# --- CLI -------------------------------------------------------------------

def test_cli_exit_codes_and_output(tmp_path, monkeypatch, capsys):
    handler = _serve({"/openapi.json": (200, OPENAPI_DOC, "application/json")})

    real_discover = schema_probe.discover

    def fake_discover(base_url, headers=None, timeout=None, extra_paths=(), client=None):
        with _client(handler) as c:
            return real_discover(base_url, client=c)

    monkeypatch.setattr(schema_probe, "discover", fake_discover)
    out = tmp_path / "schema.json"
    assert schema_probe.main(["https://target.test", "--out", str(out)]) == 0
    saved = json.loads(out.read_text())
    assert saved["schema"]["operation"] == "POST /api/purchase-invoices"

    assert schema_probe.main(["https://target.test", "--fields-only"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert isinstance(printed, list) and printed[0]["path"]


def test_cli_reports_not_found_as_exit_2(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SPA_SHELL, headers={"content-type": "text/html"})

    real_discover = schema_probe.discover

    def fake_discover(base_url, headers=None, timeout=None, extra_paths=(), client=None):
        with _client(handler) as c:
            return real_discover(base_url, client=c)

    monkeypatch.setattr(schema_probe, "discover", fake_discover)
    assert schema_probe.main(["https://target.test"]) == 2


def test_cli_takes_the_cookie_from_the_environment(monkeypatch):
    """So a session cookie never has to be typed into shell history."""
    captured = {}

    def fake_discover(base_url, headers=None, timeout=None, extra_paths=(), client=None):
        captured.update(headers or {})
        return {"status": "unreachable", "probes": [], "schema": None, "next_step": "x"}

    monkeypatch.setattr(schema_probe, "discover", fake_discover)
    monkeypatch.setenv("LESARIN_TARGET_COOKIE", "session=from-env")
    assert schema_probe.main(["https://target.test"]) == 1
    assert captured["Cookie"] == "session=from-env"
