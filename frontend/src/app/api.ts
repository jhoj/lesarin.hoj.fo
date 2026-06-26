import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import {
  CanonicalField,
  DocumentInfo,
  FieldSuggestion,
  Mapping,
  Me,
  OutputField,
  OutputProfile,
  ProfilePayload,
  ReadResult,
  TokenResponse,
  Vendor,
  VendorPayload,
} from './models';

const BASE = '/api';

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  listOutputFields(): Promise<OutputField[]> {
    return firstValueFrom(this.http.get<OutputField[]>(`${BASE}/output-fields`));
  }

  upsertOutputField(field: Partial<OutputField>): Promise<OutputField> {
    return firstValueFrom(this.http.post<OutputField>(`${BASE}/output-fields`, field));
  }

  deleteOutputField(key: string): Promise<unknown> {
    return firstValueFrom(this.http.delete(`${BASE}/output-fields/${encodeURIComponent(key)}`));
  }

  listVendors(): Promise<Vendor[]> {
    return firstValueFrom(this.http.get<Vendor[]>(`${BASE}/vendors`));
  }

  getVendor(id: number): Promise<Vendor> {
    return firstValueFrom(this.http.get<Vendor>(`${BASE}/vendors/${id}`));
  }

  createVendor(payload: VendorPayload): Promise<Vendor> {
    return firstValueFrom(this.http.post<Vendor>(`${BASE}/vendors`, payload));
  }

  updateVendor(id: number, payload: VendorPayload): Promise<Vendor> {
    return firstValueFrom(this.http.put<Vendor>(`${BASE}/vendors/${id}`, payload));
  }

  uploadDocument(file: File): Promise<DocumentInfo> {
    const fd = new FormData();
    fd.append('file', file);
    return firstValueFrom(this.http.post<DocumentInfo>(`${BASE}/documents`, fd));
  }

  fileUrl(docId: string): string {
    return `${BASE}/documents/${docId}/file`;
  }

  suggestFields(docId: string): Promise<{ suggestions: FieldSuggestion[] }> {
    return firstValueFrom(
      this.http.get<{ suggestions: FieldSuggestion[] }>(`${BASE}/documents/${docId}/suggest-fields`),
    );
  }

  read(docId: string, mappings: Mapping[]): Promise<ReadResult> {
    return firstValueFrom(
      this.http.post<ReadResult>(`${BASE}/documents/${docId}/read`, { fields: mappings }),
    );
  }

  // ---- SaaS: auth ---------------------------------------------------------

  register(email: string, password: string): Promise<TokenResponse> {
    return firstValueFrom(this.http.post<TokenResponse>(`${BASE}/auth/register`, { email, password }));
  }

  login(email: string, password: string): Promise<TokenResponse> {
    return firstValueFrom(this.http.post<TokenResponse>(`${BASE}/auth/login`, { email, password }));
  }

  me(): Promise<Me> {
    return firstValueFrom(this.http.get<Me>(`${BASE}/me`));
  }

  // ---- SaaS: profiles + export -------------------------------------------

  canonicalFields(): Promise<CanonicalField[]> {
    return firstValueFrom(this.http.get<CanonicalField[]>(`${BASE}/canonical-fields`));
  }

  listProfiles(): Promise<OutputProfile[]> {
    return firstValueFrom(this.http.get<OutputProfile[]>(`${BASE}/me/profiles`));
  }

  createProfile(payload: ProfilePayload): Promise<OutputProfile> {
    return firstValueFrom(this.http.post<OutputProfile>(`${BASE}/me/profiles`, payload));
  }

  updateProfile(id: number, payload: ProfilePayload): Promise<OutputProfile> {
    return firstValueFrom(this.http.put<OutputProfile>(`${BASE}/me/profiles/${id}`, payload));
  }

  deleteProfile(id: number): Promise<unknown> {
    return firstValueFrom(this.http.delete(`${BASE}/me/profiles/${id}`));
  }

  /** Upload a PDF and get the exported body back as text, with its filename. */
  async exportInvoice(
    file: File,
    profileId: number | null,
    fmt: string | null,
  ): Promise<{ body: string; filename: string; contentType: string }> {
    const fd = new FormData();
    fd.append('file', file);
    const params: string[] = [];
    if (profileId != null) params.push(`profile_id=${profileId}`);
    if (fmt) params.push(`fmt=${fmt}`);
    const qs = params.length ? `?${params.join('&')}` : '';
    const res = await firstValueFrom(
      this.http.post(`${BASE}/me/export${qs}`, fd, { observe: 'response', responseType: 'text' }),
    );
    const cd = res.headers.get('Content-Disposition') ?? '';
    const m = cd.match(/filename="?([^"]+)"?/);
    return {
      body: res.body ?? '',
      filename: m ? m[1] : 'invoice.txt',
      contentType: res.headers.get('Content-Type') ?? 'text/plain',
    };
  }
}
