import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

import {
  DocumentInfo,
  Mapping,
  OutputField,
  ReadResult,
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

  read(docId: string, mappings: Mapping[]): Promise<ReadResult> {
    return firstValueFrom(
      this.http.post<ReadResult>(`${BASE}/documents/${docId}/read`, { fields: mappings }),
    );
  }
}
