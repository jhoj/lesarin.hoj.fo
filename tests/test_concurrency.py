"""Uploads must not stall the rest of the service.

Parsing a PDF (and OCR'ing a scan) is CPU-bound and can run for seconds, while
production runs a single uvicorn worker. Done on the event loop, one upload
freezes every other request in that worker; done in a worker thread, uploads
overlap. So: send several uploads at once and assert they don't serialise.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.extraction import loader
from app.main import app

_PARSE_SECONDS = 1.0
_CONCURRENT_UPLOADS = 3


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_uploads_do_not_serialise_on_the_event_loop(monkeypatch, sample_invoice_pdf):
    parsed = loader.load(sample_invoice_pdf)

    def slow_load(_data: bytes):
        time.sleep(_PARSE_SECONDS)  # genuinely blocking, like pdfplumber/Tesseract
        return parsed

    monkeypatch.setattr(loader, "load", slow_load)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        started = time.perf_counter()
        responses = await asyncio.gather(
            *[
                client.post(
                    "/extract",
                    files={"file": ("invoice.pdf", sample_invoice_pdf, "application/pdf")},
                )
                for _ in range(_CONCURRENT_UPLOADS)
            ]
        )
        elapsed = time.perf_counter() - started

    assert all(r.status_code == 200 for r in responses)
    # Overlapped they take ~_PARSE_SECONDS; serialised on a blocked loop they'd
    # take ~_CONCURRENT_UPLOADS x that.
    assert elapsed < _PARSE_SECONDS * 2, (
        f"{_CONCURRENT_UPLOADS} uploads took {elapsed:.2f}s — extraction is blocking the event loop"
    )
