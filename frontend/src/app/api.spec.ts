import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { Api } from './api';

describe('Api', () => {
  let api: Api;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(Api);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('escapes output-field keys in the delete URL', () => {
    // Keys are user-chosen, so one containing a slash or space must not
    // silently address a different route.
    api.deleteOutputField('Vendor Number/2').catch(() => {});
    httpMock.expectOne('/api/output-fields/Vendor%20Number%2F2').flush({});
  });

  it('uploads a document as multipart form data', () => {
    const file = new File(['%PDF-1.4'], 'faktura.pdf', { type: 'application/pdf' });
    api.uploadDocument(file).catch(() => {});

    const req = httpMock.expectOne('/api/documents');
    expect(req.request.method).toBe('POST');
    expect(req.request.body instanceof FormData).toBe(true);
    expect((req.request.body as FormData).get('file')).toBe(file);
    req.flush({ doc_id: 'abc', n_pages: 1, pages: [], ocr_used: false, detected_vendor: null });
  });

  it('passes the chosen profile and format through as query parameters', () => {
    const file = new File(['%PDF-1.4'], 'faktura.pdf', { type: 'application/pdf' });
    api.exportInvoice(file, 7, 'oioubl').catch(() => {});

    const req = httpMock.expectOne('/api/me/export?profile_id=7&fmt=oioubl');
    expect(req.request.method).toBe('POST');
    req.flush('<Invoice/>');
  });

  it('omits the query string entirely when neither is chosen', () => {
    const file = new File(['%PDF-1.4'], 'faktura.pdf', { type: 'application/pdf' });
    api.exportInvoice(file, null, null).catch(() => {});

    httpMock.expectOne('/api/me/export').flush('{}');
  });

  it('reads the download filename out of Content-Disposition', async () => {
    const file = new File(['%PDF-1.4'], 'faktura.pdf', { type: 'application/pdf' });
    const pending = api.exportInvoice(file, null, null);

    httpMock.expectOne('/api/me/export').flush('{"InvoiceNo":"2026-0014"}', {
      headers: { 'Content-Disposition': 'attachment; filename="faktura.json"' },
    });

    const res = await pending;
    expect(res.filename).toBe('faktura.json');
    expect(res.body).toBe('{"InvoiceNo":"2026-0014"}');
  });

  it('falls back to a default filename when the header is absent', async () => {
    const file = new File(['%PDF-1.4'], 'faktura.pdf', { type: 'application/pdf' });
    const pending = api.exportInvoice(file, null, null);
    httpMock.expectOne('/api/me/export').flush('{}');

    expect((await pending).filename).toBe('invoice.txt');
  });
});
