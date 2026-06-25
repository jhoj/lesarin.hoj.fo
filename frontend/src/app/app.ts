import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { Api } from './api';
import { PdfViewer, ViewBox } from './pdf-viewer';
import {
  DocumentInfo,
  Mapping,
  OutputField,
  ReadField,
  Strategy,
  Suggestion,
  ValueType,
  Vendor,
} from './models';

@Component({
  selector: 'app-root',
  imports: [FormsModule, DecimalPipe, PdfViewer],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit {
  private readonly api = inject(Api);

  readonly outputFields = signal<OutputField[]>([]);
  readonly vendors = signal<Vendor[]>([]);
  readonly selectedVendorId = signal<number | null>(null);

  readonly docInfo = signal<DocumentInfo | null>(null);
  readonly mappings = signal<Mapping[]>([]);
  readonly readFields = signal<ReadField[]>([]);
  readonly suggestions = signal<Suggestion[]>([]);
  readonly selectedOutput = signal<string | null>(null);

  // Editable vendor header.
  readonly vendorName = signal('');
  readonly vendorIdentifier = signal('');
  readonly vendorKeywords = signal('');

  // New-output-field inputs.
  readonly newOutputKey = signal('');
  readonly newOutputType = signal<ValueType>('string');

  readonly busy = signal(false);
  readonly status = signal('Upload an invoice to start.');

  readonly fileUrl = computed(() => {
    const info = this.docInfo();
    return info ? this.api.fileUrl(info.doc_id) : null;
  });

  readonly detectedVendorId = computed(() => this.docInfo()?.detected_vendor?.id ?? null);

  readonly readByOutput = computed(() => {
    const map = new Map<string, ReadField>();
    for (const f of this.readFields()) map.set(f.output, f);
    return map;
  });

  readonly boxes = computed<ViewBox[]>(() => {
    const out: ViewBox[] = [];
    for (const f of this.readFields()) {
      if (f.bbox && f.page) {
        out.push({ id: f.output, page: f.page, bbox: f.bbox, kind: 'value', label: `${f.output}: ${f.value ?? '—'}` });
      }
    }
    this.suggestions().forEach((s, i) => {
      if (s.field.bbox && s.field.page) {
        out.push({ id: `sugg:${i}`, page: s.field.page, bbox: s.field.bbox, kind: 'suggestion', label: `${s.kind}: ${s.field.value ?? ''}` });
      }
    });
    return out;
  });

  readonly unmappedOutputs = computed(() => {
    const used = new Set(this.mappings().map((m) => m.output));
    return this.outputFields().filter((f) => !used.has(f.key));
  });

  async ngOnInit(): Promise<void> {
    await Promise.all([this.reloadOutputFields(), this.reloadVendors()]);
  }

  private async reloadOutputFields(): Promise<void> {
    this.outputFields.set(await this.api.listOutputFields());
  }

  private async reloadVendors(): Promise<void> {
    this.vendors.set(await this.api.listVendors());
  }

  // ---- Upload + read ------------------------------------------------------

  onFileInput(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) void this.upload(file);
    input.value = '';
  }

  onDrop(ev: DragEvent): void {
    ev.preventDefault();
    const file = ev.dataTransfer?.files?.[0];
    if (file) void this.upload(file);
  }

  onDragOver(ev: DragEvent): void {
    ev.preventDefault();
  }

  private async upload(file: File): Promise<void> {
    this.busy.set(true);
    this.status.set(`Reading ${file.name}…`);
    try {
      const info = await this.api.uploadDocument(file);
      this.docInfo.set(info);
      this.readFields.set([]);
      this.suggestions.set([]);
      if (info.detected_vendor) {
        await this.selectVendor(info.detected_vendor.id);
        this.status.set(`Detected vendor: ${info.detected_vendor.name}.`);
      } else {
        this.startBlankTemplate();
        this.status.set('No known vendor matched — create one below.');
      }
      await this.autoRead();
    } catch (err) {
      this.status.set(`Upload failed: ${String(err)}`);
    } finally {
      this.busy.set(false);
    }
  }

  async autoRead(): Promise<void> {
    const info = this.docInfo();
    if (!info) return;
    this.busy.set(true);
    try {
      const res = await this.api.read(info.doc_id, this.mappings());
      this.readFields.set(res.fields);
      this.suggestions.set(res.suggestions);
      this.status.set(`Found ${res.meta.fields_found} / ${res.meta.fields_total} mapped fields.`);
    } catch (err) {
      this.status.set(`Read failed: ${String(err)}`);
    } finally {
      this.busy.set(false);
    }
  }

  // ---- Vendors ------------------------------------------------------------

  async selectVendor(id: number): Promise<void> {
    const vendor = await this.api.getVendor(id);
    this.selectedVendorId.set(vendor.id);
    this.vendorName.set(vendor.name);
    this.vendorIdentifier.set(vendor.identifier);
    this.vendorKeywords.set((vendor.match_keywords ?? []).join(', '));
    this.mappings.set(vendor.mappings.map((m) => ({ ...m })));
    this.selectedOutput.set(null);
    if (this.docInfo()) await this.autoRead();
  }

  newVendor(): void {
    this.selectedVendorId.set(null);
    this.vendorName.set('');
    this.vendorIdentifier.set('');
    this.vendorKeywords.set('');
    this.startBlankTemplate();
  }

  private startBlankTemplate(): void {
    this.mappings.set(
      this.outputFields().map((f) => ({
        output: f.key,
        strategy: 'label' as Strategy,
        label: '',
        relation: 'right' as const,
        value_type: f.value_type,
        page: null,
        bbox: null,
      })),
    );
  }

  async save(): Promise<void> {
    if (!this.vendorIdentifier().trim() || !this.vendorName().trim()) {
      this.status.set('Vendor needs an identifier (V-tal) and a name.');
      return;
    }
    const payload = {
      identifier: this.vendorIdentifier().trim(),
      name: this.vendorName().trim(),
      identifier_kind: 'vtal',
      match_keywords: this.vendorKeywords()
        .split(',')
        .map((k) => k.trim())
        .filter(Boolean),
      mappings: this.mappings(),
    };
    this.busy.set(true);
    try {
      const id = this.selectedVendorId();
      const saved = id ? await this.api.updateVendor(id, payload) : await this.api.createVendor(payload);
      await this.reloadVendors();
      this.selectedVendorId.set(saved.id);
      this.status.set(`Saved template for ${saved.name}.`);
    } catch (err) {
      this.status.set(`Save failed: ${String(err)}`);
    } finally {
      this.busy.set(false);
    }
  }

  // ---- Mapping edits ------------------------------------------------------

  updateMapping(index: number, patch: Partial<Mapping>): void {
    this.mappings.update((list) => list.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  }

  removeMapping(index: number): void {
    this.mappings.update((list) => list.filter((_, i) => i !== index));
  }

  addMappingFor(outputKey: string): void {
    if (!outputKey) return;
    const field = this.outputFields().find((f) => f.key === outputKey);
    this.mappings.update((list) => [
      ...list,
      {
        output: outputKey,
        strategy: 'label',
        label: '',
        relation: 'right',
        value_type: field?.value_type ?? 'string',
        page: null,
        bbox: null,
      },
    ]);
  }

  selectOutput(output: string): void {
    this.selectedOutput.set(output);
  }

  // Dragging a box turns that field into a fixed-region read at the new box.
  onBoxMove(ev: { id: string; page: number; bbox: number[] }): void {
    if (ev.id.startsWith('sugg:')) return;
    const index = this.mappings().findIndex((m) => m.output === ev.id);
    if (index < 0) return;
    this.updateMapping(index, { strategy: 'region', page: ev.page, bbox: ev.bbox });
    void this.autoRead();
  }

  onBoxSelect(id: string): void {
    if (!id.startsWith('sugg:')) this.selectedOutput.set(id);
  }

  // Assign a heuristic suggestion to the currently selected field.
  applySuggestion(s: Suggestion): void {
    const output = this.selectedOutput();
    if (!output) {
      this.status.set('Select a field row first, then apply a suggestion to it.');
      return;
    }
    const index = this.mappings().findIndex((m) => m.output === output);
    if (index < 0) return;
    this.updateMapping(index, { strategy: 'region', page: s.field.page, bbox: s.field.bbox });
    void this.autoRead();
  }

  // ---- Output-field setup table ------------------------------------------

  async addOutputField(): Promise<void> {
    const key = this.newOutputKey().trim();
    if (!key) return;
    await this.api.upsertOutputField({
      key,
      display_name: key,
      value_type: this.newOutputType(),
      sort_order: this.outputFields().length,
    });
    this.newOutputKey.set('');
    await this.reloadOutputFields();
  }

  async deleteOutputField(key: string): Promise<void> {
    await this.api.deleteOutputField(key);
    await this.reloadOutputFields();
    this.mappings.update((list) => list.filter((m) => m.output !== key));
  }
}
