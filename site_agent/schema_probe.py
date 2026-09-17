"""Find a target application's field contract without its documentation.

Onboarding asks the customer one question — *where do these invoices need to
go?* — and everything downstream (the drag-and-drop mapping, the exporter)
needs the answer as a **field list**. This module gets that list the cheap way:
many applications serve a machine-readable schema behind their login without
ever advertising it, so with a session cookie we can simply ask.

It probes the well-known locations for four contract formats:

* **OpenAPI 3** / **Swagger 2** — ``/openapi.json``, ``/swagger/v1/swagger.json``,
  ``/v3/api-docs`` and friends. Operations become candidates; the request body
  schema becomes the fields.
* **OData** — ``/$metadata`` (an XML EDMX document). Common wherever Dynamics /
  Business Central sits underneath. Entity types become candidates.
* **GraphQL** — an introspection POST to ``/graphql``. Input object types
  become candidates.

Nothing here writes to the target: every probe is a GET, except the GraphQL
introspection POST, which is a read by definition.

This lives in ``site_agent`` on purpose. Reaching the target needs the
customer's credentials, and those stay on the customer's own machine — the
*schema* is what travels, never the cookie that found it.

    python -m site_agent.schema_probe https://target.example --cookie "session=..."

Exit codes match the rest of the toolchain: ``0`` a schema was found, ``2``
the target answered but serves no machine-readable contract (fall back to
observing the app's own traffic), ``1`` it could not be reached or the
credentials were rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

_DEFAULT_TIMEOUT = 20.0
# A schema document is text. Anything past this is not one, and we would rather
# stop reading than pull a GB of something else through a customer's uplink.
_MAX_BYTES = 8 * 1024 * 1024
# How deep to walk a nested request body before deciding the rest is noise.
_MAX_DEPTH = 6
_MAX_REDIRECTS = 3

# Probed in order; the first document that parses as a contract wins. GETs.
OPENAPI_PATHS: Tuple[str, ...] = (
    "/openapi.json",
    "/swagger/v1/swagger.json",
    "/swagger/v2/swagger.json",
    "/swagger.json",
    "/v3/api-docs",
    "/v2/api-docs",
    "/api-docs",
    "/api/openapi.json",
    "/api/swagger.json",
    "/api/v1/swagger.json",
    "/v1/swagger.json",
    "/swagger/docs/v1",
    "/docs/openapi.json",
    "/.well-known/openapi.json",
    "/openapi.yaml",
    "/openapi.yml",
)

ODATA_PATHS: Tuple[str, ...] = (
    "/$metadata",
    "/odata/$metadata",
    "/api/$metadata",
    "/api/odata/$metadata",
)

# POSTed, not GETed — introspection is a query.
GRAPHQL_PATHS: Tuple[str, ...] = ("/graphql", "/api/graphql")

_INTROSPECTION_QUERY = (
    "{__schema{mutationType{name}"
    "types{kind name description "
    "inputFields{name description type{kind name ofType{kind name "
    "ofType{kind name ofType{kind name}}}}}}}}"
)

# What an invoice-shaped operation tends to be called, in the three languages
# this product already reads (fo / da / en). Mirrors app/config/labels.yaml's
# spirit: vocabulary lives in a list, not in branching code.
_POSITIVE_TERMS: Dict[str, float] = {
    "invoice": 3.0, "invoices": 3.0, "faktura": 3.0, "fakturaer": 3.0,
    "rokning": 3.0, "rokningar": 3.0, "bill": 2.0, "billing": 2.0,
    "purchase": 2.0, "purchaseinvoice": 4.0, "supplierinvoice": 4.0,
    "creditor": 2.0, "kreditor": 2.0, "voucher": 2.0, "bilag": 2.0,
    "payable": 2.0, "payables": 2.0, "expense": 1.5, "vendor": 1.0,
    "veitari": 1.0, "leverandor": 1.0, "leverandør": 1.0, "document": 0.5,
}
# Endpoints that exist in every application and are never the one we want.
_NEGATIVE_TERMS: Dict[str, float] = {
    "login": 4.0, "logout": 4.0, "auth": 3.0, "token": 3.0, "password": 3.0,
    "health": 3.0, "healthz": 3.0, "metrics": 3.0, "ping": 3.0, "status": 1.0,
    "user": 1.5, "users": 1.5, "admin": 1.5, "setting": 1.5, "settings": 1.5,
    "search": 1.0, "report": 1.0, "log": 1.0, "logs": 1.0, "notification": 1.5,
}
# Canonical-ish field names that make a body look like an invoice regardless of
# what the endpoint is called. Scored once each, not per occurrence.
_FIELD_TERMS: Tuple[str, ...] = (
    "invoiceno", "invoicenumber", "invoicedate", "duedate", "fakturanr",
    "vendorno", "vendorname", "supplierno", "suppliername", "totalamount",
    "amount", "vat", "currency", "net", "gross",
)

_JSON_TYPES = {
    "string": "string", "integer": "integer", "number": "number",
    "boolean": "boolean", "object": "object", "array": "array",
}
_STRING_FORMATS = {"date": "date", "date-time": "datetime"}

# EDM primitive → our vocabulary. Anything unlisted stays as-is, lowercased.
_EDM_TYPES = {
    "Edm.String": "string", "Edm.Boolean": "boolean", "Edm.Date": "date",
    "Edm.DateTime": "datetime", "Edm.DateTimeOffset": "datetime",
    "Edm.Decimal": "number", "Edm.Double": "number", "Edm.Single": "number",
    "Edm.Int16": "integer", "Edm.Int32": "integer", "Edm.Int64": "integer",
    "Edm.Byte": "integer", "Edm.Guid": "string",
}


@dataclass
class TargetField:
    """One field the target expects, named the way the target names it."""

    path: str                       # dotted, arrays marked: "lines[].amount"
    name: str                       # the leaf, for matching against canonical
    type: str = "unknown"
    required: bool = False
    label: Optional[str] = None     # title/description — the human-readable name
    enum: Optional[List[str]] = None
    max_length: Optional[int] = None

    def as_dict(self) -> dict:
        out = {"path": self.path, "name": self.name, "type": self.type,
               "required": self.required}
        if self.label:
            out["label"] = self.label
        if self.enum:
            out["enum"] = self.enum
        if self.max_length is not None:
            out["max_length"] = self.max_length
        return out


@dataclass
class TargetSchema:
    """A field contract discovered at a target, with where it came from.

    ``score`` ranks candidates within one document — how invoice-shaped this
    operation looks. It is a sorting aid for the operator's pick list, not a
    confidence that the schema itself is correct: a parsed contract is exact.
    """

    target: str
    kind: str                       # openapi3 | swagger2 | odata | graphql
    source_url: str
    operation: str                  # "POST /api/invoices", or an entity name
    direction: str                  # write | read
    fields: List[TargetField] = field(default_factory=list)
    title: Optional[str] = None
    score: float = 0.0
    evidence_sha256: str = ""
    discovered_at: str = ""

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "kind": self.kind,
            "source": "schema-endpoint",
            "source_url": self.source_url,
            "operation": self.operation,
            "direction": self.direction,
            "title": self.title,
            "score": round(self.score, 2),
            "evidence_sha256": self.evidence_sha256,
            "discovered_at": self.discovered_at,
            "fields": [f.as_dict() for f in self.fields],
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_headers(
    cookie: Optional[str] = None,
    bearer: Optional[str] = None,
    extra: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    """Assemble request headers from the credential flags.

    Raises on a malformed ``--header`` rather than silently sending a header
    the caller did not mean to send.
    """
    headers: Dict[str, str] = {"Accept": "application/json, text/yaml, application/xml;q=0.8, */*;q=0.5"}
    if cookie:
        headers["Cookie"] = cookie
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    for item in extra or ():
        name, sep, value = item.partition(":")
        if not sep:
            name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"malformed header (expected 'Name: value'): {item!r}")
        headers[name.strip()] = value.strip()
    return headers


# Sent by every client; listing them would bury the ones that carry credentials.
_BORING_HEADERS = frozenset({
    "accept", "accept-encoding", "connection", "user-agent", "host",
    "content-type", "content-length",
})


def _redacted(headers: Dict[str, str]) -> List[str]:
    """Header *names* only. A discovery report is meant to be shareable, and a
    session cookie in it would be a credential leaked into a bug thread.

    Lower-cased so the report reads the same whether the names came from a flag
    or from an httpx client, which normalises them.
    """
    return sorted({k.lower() for k in headers} - _BORING_HEADERS)


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _load_document(body: str, content_type: str, url: str) -> Optional[object]:
    """Parse a probe response into JSON/YAML data or an XML element.

    Returns ``None`` when the body is not a structured document at all — which
    is the common case, because an unknown path in a single-page app usually
    answers 200 with the HTML shell.
    """
    text = body.strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return None
    if text[0] == "<":
        if "html" in content_type.lower() or text[:200].lower().lstrip().startswith("<!doctype html"):
            return None
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return None
    if url.endswith((".yaml", ".yml")) or "yaml" in content_type.lower():
        try:
            import yaml  # PyYAML is already a dependency (app/config/labels.yaml)
        except ImportError:  # pragma: no cover - yaml ships with the app
            return None
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
        return loaded if isinstance(loaded, (dict, list)) else None
    return None


def classify(doc: object) -> Optional[str]:
    """Name the contract format, or ``None`` if this isn't one."""
    if isinstance(doc, ET.Element):
        tag = doc.tag.rsplit("}", 1)[-1]
        return "odata" if tag in ("Edmx", "edmx") else None
    if not isinstance(doc, dict):
        return None
    if isinstance(doc.get("openapi"), str) and doc["openapi"].startswith("3"):
        return "openapi3"
    if isinstance(doc.get("swagger"), str) and doc["swagger"].startswith("2"):
        return "swagger2"
    if isinstance(doc.get("data"), dict) and "__schema" in doc["data"]:
        return "graphql"
    return None


class _Resolver:
    """Follows ``$ref`` pointers inside one OpenAPI/Swagger document."""

    def __init__(self, doc: dict):
        self.doc = doc

    def resolve(self, schema: object, seen: Tuple[str, ...] = ()) -> Tuple[dict, Tuple[str, ...]]:
        """Return a concrete schema plus the ref chain used to reach it.

        The chain is what stops a self-referential model (an invoice whose line
        references an invoice) from recursing forever.
        """
        if not isinstance(schema, dict):
            return {}, seen
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            return schema, seen
        if ref in seen or not ref.startswith("#/"):
            return {}, seen
        node: object = self.doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return {}, seen
            node = node[part]
        return self.resolve(node, seen + (ref,))


def _merge_all_of(schema: dict, resolver: _Resolver, seen: Tuple[str, ...]) -> dict:
    """Flatten ``allOf`` composition into a single schema.

    Composition is how most generators express inheritance, so without this a
    model built from a base type looks like it has no fields at all.
    """
    parts = schema.get("allOf")
    if not isinstance(parts, list):
        return schema
    merged: dict = {k: v for k, v in schema.items() if k != "allOf"}
    props: dict = dict(merged.get("properties") or {})
    required: List[str] = list(merged.get("required") or [])
    for part in parts:
        concrete, chain = resolver.resolve(part, seen)
        concrete = _merge_all_of(concrete, resolver, chain)
        props.update(concrete.get("properties") or {})
        required.extend(concrete.get("required") or [])
        for key, value in concrete.items():
            if key not in ("properties", "required", "allOf"):
                merged.setdefault(key, value)
    if props:
        merged["properties"] = props
    if required:
        merged["required"] = sorted(set(required))
    return merged


def _scalar_type(schema: dict) -> str:
    declared = schema.get("type")
    if isinstance(declared, list):  # OpenAPI 3.1 nullable unions
        declared = next((t for t in declared if t != "null"), None)
    if declared == "string":
        return _STRING_FORMATS.get(str(schema.get("format") or ""), "string")
    return _JSON_TYPES.get(str(declared), "unknown")


def flatten_schema(
    schema: object,
    resolver: _Resolver,
    prefix: str = "",
    required: bool = False,
    depth: int = 0,
    seen: Tuple[str, ...] = (),
) -> List[TargetField]:
    """Walk a JSON Schema into a flat, dotted field list.

    Arrays of objects are emitted as ``lines[].amount`` — one entry per *shape*,
    not per element, because the mapping UI maps a repeating group once.
    """
    concrete, chain = resolver.resolve(schema, seen)
    if not concrete or depth > _MAX_DEPTH:
        return []
    concrete = _merge_all_of(concrete, resolver, chain)
    for key in ("oneOf", "anyOf"):
        options = concrete.get(key)
        if isinstance(options, list) and options and not concrete.get("properties"):
            # Can't map to two shapes at once; the first branch is the one
            # generators emit for the primary variant.
            resolved, chain = resolver.resolve(options[0], chain)
            concrete = _merge_all_of(resolved, resolver, chain)

    props = concrete.get("properties")
    if isinstance(props, dict) and props:
        required_names = set(concrete.get("required") or [])
        fields: List[TargetField] = []
        for name, child in props.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            fields.extend(
                flatten_schema(child, resolver, path, name in required_names, depth + 1, chain)
            )
        return fields

    if _scalar_type(concrete) == "array" or "items" in concrete:
        items = concrete.get("items")
        if items is not None:
            return flatten_schema(items, resolver, f"{prefix}[]", required, depth + 1, chain)
        return []

    if not prefix:
        return []

    enum = concrete.get("enum")
    max_length = concrete.get("maxLength")
    label = concrete.get("title") or concrete.get("description")
    return [
        TargetField(
            path=prefix,
            name=prefix.rsplit(".", 1)[-1].replace("[]", ""),
            type=_scalar_type(concrete),
            required=required,
            label=str(label).strip() if isinstance(label, str) and label.strip() else None,
            enum=[str(v) for v in enum] if isinstance(enum, list) and enum else None,
            max_length=max_length if isinstance(max_length, int) else None,
        )
    ]


def score_candidate(text: str, fields: Iterable[TargetField], direction: str) -> float:
    """How invoice-shaped does this operation look?

    Two signals: what it is *called* (path, summary, tags) and what it *carries*
    (field names). The second matters because a generic ``POST /documents`` with
    an ``invoiceNumber`` in the body is the endpoint we want, and its name says
    nothing at all.
    """
    haystack = text.lower()
    score = 0.0
    for term, weight in _POSITIVE_TERMS.items():
        if term in haystack:
            score += weight
    for term, weight in _NEGATIVE_TERMS.items():
        if term in haystack:
            score -= weight
    names = {f.name.lower() for f in fields}
    flat = {n.replace("_", "").replace("-", "") for n in names}
    score += 1.5 * len(flat & set(_FIELD_TERMS))
    if direction == "write":
        score += 2.0  # we are pushing data in, not reading it back
    return score


def _operation_candidates(doc: dict, kind: str, source_url: str, target: str,
                          evidence: str) -> List[TargetSchema]:
    resolver = _Resolver(doc)
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return []
    out: List[TargetSchema] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in ("post", "put", "patch", "get") or not isinstance(op, dict):
                continue
            direction = "read" if method.lower() == "get" else "write"
            body = _request_body_schema(op, kind, resolver)
            if body is None and direction == "read":
                body = _response_schema(op, kind, resolver)
            if body is None:
                continue
            fields = flatten_schema(body, resolver)
            if not fields:
                continue
            text = " ".join(str(x) for x in (
                path, op.get("summary") or "", op.get("operationId") or "",
                " ".join(op.get("tags") or []),
            ))
            out.append(TargetSchema(
                target=target, kind=kind, source_url=source_url,
                operation=f"{method.upper()} {path}", direction=direction,
                fields=fields,
                title=op.get("summary") or op.get("operationId") or None,
                score=score_candidate(text, fields, direction),
                evidence_sha256=evidence, discovered_at=_now(),
            ))
    return out


def _request_body_schema(op: dict, kind: str, resolver: _Resolver) -> Optional[object]:
    if kind == "swagger2":
        for param in op.get("parameters") or []:
            if isinstance(param, dict) and param.get("in") == "body":
                return param.get("schema")
        return None
    body, _ = resolver.resolve(op.get("requestBody") or {})
    content = body.get("content")
    if not isinstance(content, dict):
        return None
    for media, spec in content.items():
        if "json" in str(media).lower() and isinstance(spec, dict):
            return spec.get("schema")
    return None


def _response_schema(op: dict, kind: str, resolver: _Resolver) -> Optional[object]:
    responses = op.get("responses")
    if not isinstance(responses, dict):
        return None
    for code in ("200", "201", 200, 201):
        spec = responses.get(code)
        if not isinstance(spec, dict):
            continue
        if kind == "swagger2":
            return spec.get("schema")
        content = spec.get("content")
        if isinstance(content, dict):
            for media, media_spec in content.items():
                if "json" in str(media).lower() and isinstance(media_spec, dict):
                    return media_spec.get("schema")
    return None


def _odata_candidates(root: ET.Element, source_url: str, target: str,
                      evidence: str) -> List[TargetSchema]:
    out: List[TargetSchema] = []
    for entity in root.iter():
        if entity.tag.rsplit("}", 1)[-1] != "EntityType":
            continue
        name = entity.get("Name") or "(unnamed)"
        keys = {
            ref.get("Name")
            for key in entity.iter()
            if key.tag.rsplit("}", 1)[-1] == "Key"
            for ref in key
            if ref.tag.rsplit("}", 1)[-1] == "PropertyRef"
        }
        fields: List[TargetField] = []
        for prop in entity:
            if prop.tag.rsplit("}", 1)[-1] != "Property":
                continue
            prop_name = prop.get("Name")
            if not prop_name:
                continue
            edm = prop.get("Type") or ""
            max_length = prop.get("MaxLength")
            fields.append(TargetField(
                path=prop_name,
                name=prop_name,
                type=_EDM_TYPES.get(edm, edm.replace("Edm.", "").lower() or "unknown"),
                # An OData key is server-assigned; "required" here means the
                # caller must supply it, which a key is not.
                required=(prop.get("Nullable") == "false" and prop_name not in keys),
                max_length=int(max_length) if str(max_length).isdigit() else None,
            ))
        if not fields:
            continue
        out.append(TargetSchema(
            target=target, kind="odata", source_url=source_url,
            operation=name, direction="write", fields=fields, title=name,
            score=score_candidate(name, fields, "write"),
            evidence_sha256=evidence, discovered_at=_now(),
        ))
    return out


def _graphql_type_name(node: object) -> Tuple[str, bool]:
    """Unwrap NON_NULL/LIST wrappers → (leaf name, required)."""
    required = False
    depth = 0
    while isinstance(node, dict) and depth < 8:
        if node.get("kind") == "NON_NULL":
            required = True
        name = node.get("name")
        if name and node.get("kind") not in ("NON_NULL", "LIST"):
            return str(name), required
        node = node.get("ofType")
        depth += 1
    return "unknown", required


_GRAPHQL_SCALARS = {"String": "string", "Int": "integer", "Float": "number",
                    "Boolean": "boolean", "ID": "string", "Date": "date",
                    "DateTime": "datetime", "Decimal": "number"}


def _graphql_candidates(doc: dict, source_url: str, target: str,
                        evidence: str) -> List[TargetSchema]:
    types = (((doc.get("data") or {}).get("__schema") or {}).get("types")) or []
    out: List[TargetSchema] = []
    for entry in types:
        if not isinstance(entry, dict) or entry.get("kind") != "INPUT_OBJECT":
            continue
        name = str(entry.get("name") or "")
        if name.startswith("__"):
            continue
        fields: List[TargetField] = []
        for item in entry.get("inputFields") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            leaf, required = _graphql_type_name(item.get("type"))
            description = item.get("description")
            fields.append(TargetField(
                path=str(item["name"]),
                name=str(item["name"]),
                type=_GRAPHQL_SCALARS.get(leaf, "unknown" if leaf == "unknown" else "object"),
                required=required,
                label=str(description).strip() if isinstance(description, str) and description.strip() else None,
            ))
        if not fields:
            continue
        text = f"{name} {entry.get('description') or ''}"
        out.append(TargetSchema(
            target=target, kind="graphql", source_url=source_url,
            operation=name, direction="write", fields=fields, title=name,
            score=score_candidate(text, fields, "write"),
            evidence_sha256=evidence, discovered_at=_now(),
        ))
    return out


def candidates_from_document(doc: object, kind: str, source_url: str, target: str,
                             evidence: str) -> List[TargetSchema]:
    """Turn one parsed contract into ranked candidate schemas."""
    if kind in ("openapi3", "swagger2") and isinstance(doc, dict):
        found = _operation_candidates(doc, kind, source_url, target, evidence)
    elif kind == "odata" and isinstance(doc, ET.Element):
        found = _odata_candidates(doc, source_url, target, evidence)
    elif kind == "graphql" and isinstance(doc, dict):
        found = _graphql_candidates(doc, source_url, target, evidence)
    else:
        found = []
    found.sort(key=lambda c: (-c.score, c.operation))
    return found


def _probe(client: httpx.Client, url: str, base: str, method: str = "GET",
           json_body: Optional[dict] = None) -> dict:
    """One request, following same-origin redirects only.

    Never raises — a probe that fails is a result, not an error. A redirect to
    another host is refused rather than followed, because following it would
    replay the customer's session cookie at an origin they did not name.
    """
    entry: dict = {"url": url, "method": method}
    try:
        request = client.build_request(method, url, json=json_body)
        for _ in range(_MAX_REDIRECTS + 1):
            response = client.send(request, stream=True)
            try:
                following = response.next_request
                if following is not None:
                    if not _same_origin(str(following.url), base):
                        entry.update(status=response.status_code,
                                     skipped="cross-origin redirect")
                        return entry
                    request = following
                    continue
                body = _read_capped(response)
                if body is None:
                    entry.update(status=response.status_code, skipped="response too large")
                    return entry
            finally:
                response.close()
            entry["status"] = response.status_code
            entry["bytes"] = len(body)
            if response.status_code >= 400:
                return entry
            content_type = response.headers.get("content-type", "")
            doc = _load_document(body.decode("utf-8", "replace"), content_type, str(response.url))
            kind = classify(doc)
            if kind:
                entry["kind"] = kind
                entry["_doc"] = doc
                entry["_evidence"] = hashlib.sha256(body).hexdigest()
            return entry
        entry["skipped"] = "too many redirects"
    except httpx.HTTPError as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def _read_capped(response: httpx.Response) -> Optional[bytes]:
    """Read a streamed body, giving up past the size cap."""
    chunks: List[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > _MAX_BYTES:
            return None
    return b"".join(chunks)


def discover(
    base_url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    extra_paths: Iterable[str] = (),
    client: Optional[httpx.Client] = None,
) -> dict:
    """Probe ``base_url`` for a published field contract.

    Returns a report: which paths were tried and what they answered, the best
    candidate schema, and the runners-up. ``status`` is one of ``found``,
    ``not_found``, ``unauthorized`` or ``unreachable`` — the caller (or the
    operator reading the CLI output) uses it to decide whether to move on to
    observing the application's own network traffic instead.
    """
    if not base_url.lower().startswith(("http://", "https://")):
        raise ValueError("base_url must be an http(s) URL")
    base = base_url.rstrip("/") + "/"
    headers = dict(headers or {})
    report: dict = {
        "target": base_url.rstrip("/"),
        "discovered_at": _now(),
        "auth_headers_sent": [],
        "probes": [],
        "status": "not_found",
        "schema": None,
        "other_candidates": [],
    }

    probes: List[dict] = []
    best: List[TargetSchema] = []
    owned = client is None
    if owned:
        # Redirects are followed manually so an off-origin hop can be refused
        # before the credentials are replayed to it.
        client = httpx.Client(headers=headers, timeout=timeout, follow_redirects=False)
    report["auth_headers_sent"] = _redacted(dict(client.headers))
    try:
        get_paths = list(OPENAPI_PATHS) + list(ODATA_PATHS) + [p for p in extra_paths]
        for path in get_paths:
            entry = _probe(client, urljoin(base, path.lstrip("/")), base)
            probes.append(entry)
            if entry.get("kind"):
                best = candidates_from_document(
                    entry.pop("_doc"), entry["kind"], entry["url"],
                    report["target"], entry.pop("_evidence"),
                )
                if best:
                    break
        if not best:
            for path in GRAPHQL_PATHS:
                entry = _probe(
                    client, urljoin(base, path.lstrip("/")), base,
                    method="POST", json_body={"query": _INTROSPECTION_QUERY},
                )
                probes.append(entry)
                if entry.get("kind") == "graphql":
                    best = candidates_from_document(
                        entry.pop("_doc"), "graphql", entry["url"],
                        report["target"], entry.pop("_evidence"),
                    )
                    if best:
                        break
    finally:
        if owned:
            client.close()

    for entry in probes:
        entry.pop("_doc", None)
        entry.pop("_evidence", None)
    report["probes"] = probes

    if best:
        report["status"] = "found"
        report["schema"] = best[0].as_dict()
        report["other_candidates"] = [
            {"operation": c.operation, "direction": c.direction,
             "score": round(c.score, 2), "fields": len(c.fields)}
            for c in best[1:21]
        ]
        return report

    statuses = [p.get("status") for p in probes if "status" in p]
    if statuses and all(s in (401, 403) for s in statuses):
        report["status"] = "unauthorized"
        report["next_step"] = (
            "Every probe was rejected. The session credentials are missing or "
            "expired — refresh them and retry before concluding anything."
        )
    elif not statuses:
        report["status"] = "unreachable"
        report["next_step"] = "No probe got a response. Check the URL and network path."
    else:
        report["next_step"] = (
            "No published contract at the well-known paths. Next: observe the "
            "application's own requests while a record is entered by hand."
        )
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m site_agent.schema_probe",
        description="Probe a target application for a published field contract "
                    "(OpenAPI, Swagger, OData or GraphQL) using a logged-in session.",
    )
    parser.add_argument("base_url", help="the target's base URL, e.g. https://app.example")
    parser.add_argument("--cookie", help="session cookie header value, e.g. 'session=abc'")
    parser.add_argument("--bearer", help="bearer token (sent as Authorization)")
    parser.add_argument("--header", action="append", metavar="NAME:VALUE",
                        help="extra request header; repeatable")
    parser.add_argument("--cookie-env", metavar="VAR", default="LESARIN_TARGET_COOKIE",
                        help="environment variable holding the cookie, so it stays "
                             "out of the shell history (default: %(default)s)")
    parser.add_argument("--path", action="append", default=[], metavar="PATH",
                        help="an extra path to probe; repeatable")
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT)
    parser.add_argument("--out", metavar="FILE", help="write the report here instead of stdout")
    parser.add_argument("--fields-only", action="store_true",
                        help="print just the winning schema's field list")
    args = parser.parse_args(argv)

    cookie = args.cookie or os.environ.get(args.cookie_env)
    try:
        headers = build_headers(cookie, args.bearer, args.header)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        report = discover(args.base_url, headers, args.timeout, args.path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.fields_only and report.get("schema"):
        payload: object = report["schema"]["fields"]
    else:
        payload = report
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"{report['status']}: wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    if report["status"] == "found":
        schema = report["schema"]
        print(
            f"found {len(schema['fields'])} fields in {schema['operation']} "
            f"({schema['kind']}) at {schema['source_url']}",
            file=sys.stderr,
        )
        return 0
    if report["status"] in ("unauthorized", "unreachable"):
        print(f"{report['status']}: {report.get('next_step', '')}", file=sys.stderr)
        return 1
    print(f"not_found: {report.get('next_step', '')}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
