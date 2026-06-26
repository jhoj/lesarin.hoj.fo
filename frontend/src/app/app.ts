import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { Api } from './api';
import { PdfViewer, ViewBox } from './pdf-viewer';
import {
  DocumentInfo,
  FieldSuggestion,
  LineItem,
  Mapping,
  OutputField,
  ReadField,
  Strategy,
  Suggestion,
  ValueType,
  Vendor,
} from './models';

/** An editable row in the "Suggested fields" confirmation table. */
interface SuggestRow {
  include: boolean;
  exportKey: string; // becomes the OutputField key/display_name
  readLabels: string; // comma-separated → aliases
  value: string;
  category: string;
  valueType: ValueType;
}

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
  readonly lines = signal<LineItem[]>([]);
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

  // True when the template has manual edits not yet saved — used to warn before
  // a full re-read (Auto-read) throws them away.
  readonly dirty = signal(false);

  // Wizard state: the field currently being mapped, and whether we've switched
  // to the "show every box at once" review.
  readonly activeField = signal<string | null>(null);
  readonly summaryMode = signal(false);

  // First-time setup: fields detected on the document, awaiting the user's
  // confirmation (edit export key / read-labels) before becoming output fields.
  readonly suggestRows = signal<SuggestRow[]>([]);

  readonly fileUrl = computed(() => {
    const info = this.docInfo();
    return info ? this.api.fileUrl(info.doc_id) : null;
  });

  // Nothing but the dropzone is usable until a PDF is loaded.
  readonly hasDoc = computed(() => !!this.docInfo());

  readonly detectedVendorId = computed(() => this.docInfo()?.detected_vendor?.id ?? null);

  readonly readByOutput = computed(() => {
    const map = new Map<string, ReadField>();
    for (const f of this.readFields()) map.set(f.output, f);
    return map;
  });

  readonly boxes = computed<ViewBox[]>(() => {
    const out: ViewBox[] = [];
    const read = this.readByOutput();
    const regionOutputs = new Set<string>();
    // Region-mapped fields render where the USER put the box (the mapping's own
    // bbox), not where the read landed — so a box you drag stays put even when
    // the region currently captures no text, instead of snapping back/vanishing.
    for (const m of this.mappings()) {
      if (m.strategy === 'region' && m.bbox && m.page) {
        regionOutputs.add(m.output);
        const rf = read.get(m.output);
        out.push({ id: m.output, page: m.page, bbox: m.bbox, kind: 'value', label: `${m.output}: ${rf?.value ?? '—'}` });
      }
    }
    // Label-located fields render at the found value's position.
    for (const f of this.readFields()) {
      if (f.bbox && f.page && !regionOutputs.has(f.output)) {
        out.push({ id: f.output, page: f.page, bbox: f.bbox, kind: 'value', label: `${f.output}: ${f.value ?? '—'}` });
      }
    }
    // Heuristic suggestions are offered in the side panel only — drawing them on
    // the page overlaid them on top of the real field boxes and, being inert,
    // swallowed drags meant for the value box underneath.

    // Wizard: show only the active/selected field's box at a time; the Summary
    // toggle reveals every box for a final review.
    if (this.summaryMode()) return out;
    const show = new Set([this.activeField(), this.selectedOutput()].filter(Boolean) as string[]);
    return out.filter((b) => show.has(b.id));
  });

  // Arm draw mode when the active field has neither a region box nor a value yet.
  readonly activeFieldNeedsBox = computed(() => {
    const f = this.activeField();
    if (!f || this.summaryMode()) return false;
    const m = this.mappings().find((mm) => mm.output === f);
    const hasRegion = m?.strategy === 'region' && !!m?.bbox && m.bbox.length === 4;
    const hasValue = !!this.readByOutput().get(f)?.value;
    return !hasRegion && !hasValue;
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
      this.lines.set([]);
      if (info.detected_vendor) {
        await this.selectVendor(info.detected_vendor.id); // reads + inits the wizard
        this.status.set(`Detected vendor: ${info.detected_vendor.name}.`);
      } else if (this.outputFields().length === 0) {
        // First-time setup: propose output fields from the document.
        await this.loadSuggestions(info.doc_id);
        this.status.set('Suggested fields from the document — review and confirm.');
      } else {
        this.startBlankTemplate();
        await this.autoRead();
        this.initWizard();
        this.status.set('No known vendor matched — map the fields below.');
      }
    } catch (err) {
      this.status.set(`Upload failed: ${String(err)}`);
    } finally {
      this.busy.set(false);
    }
  }

  // Button handler: a full re-detect overwrites every read, so warn first if the
  // user has unsaved manual edits (dragged boxes, typed labels, applied suggestions).
  async requestAutoRead(): Promise<void> {
    if (this.dirty() && !confirm('Re-detecting all fields will discard your current changes. Continue?')) {
      return;
    }
    this.dirty.set(false);
    await this.autoRead();
  }

  async autoRead(): Promise<void> {
    const info = this.docInfo();
    if (!info) return;
    this.busy.set(true);
    try {
      const res = await this.api.read(info.doc_id, this.mappings());
      this.readFields.set(res.fields);
      this.suggestions.set(res.suggestions);
      this.lines.set(res.lines);
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
    this.dirty.set(false);
    if (this.docInfo()) {
      await this.autoRead();
      this.initWizard();
    }
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
        // Seed the label with the field's own name: on a never-seen vendor most
        // invoices print the field name verbatim ("Gjaldsdagur", "Í alt við MVG"),
        // so this auto-locates many values on the first read instead of starting
        // with an empty template the user has to fill in by hand.
        label: (f.display_name || f.key).trim(),
        relation: 'right' as const,
        value_type: f.value_type,
        page: null,
        bbox: null,
      })),
    );
    this.dirty.set(false);
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
      this.dirty.set(false);
      this.status.set(`Saved template for ${saved.name}.`);
    } catch (err) {
      this.status.set(`Save failed: ${String(err)}`);
    } finally {
      this.busy.set(false);
    }
  }

  // ---- Mapping edits ------------------------------------------------------

  updateMapping(index: number, patch: Partial<Mapping>): void {
    this.mappings.update((list) =>
      list.map((m, i) => {
        if (i !== index) return m;
        const next = { ...m, ...patch };
        // Switching a field to "by box" with no region yet: drop a starter box
        // in the middle of page 1 so an unmatched field (vendor name, address…)
        // has something to grab and drag onto its value. Without this, "by box"
        // showed no box at all and the field stayed unmappable.
        if (next.strategy === 'region' && (!next.bbox || next.bbox.length !== 4)) {
          const seeded = this.centeredBbox();
          if (seeded) {
            next.page = next.page ?? 1;
            next.bbox = seeded;
          }
        }
        return next;
      }),
    );
    this.dirty.set(true);
  }

  removeMapping(index: number): void {
    this.mappings.update((list) => list.filter((_, i) => i !== index));
    this.dirty.set(true);
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
    this.dirty.set(true);
  }

  selectOutput(output: string): void {
    this.selectedOutput.set(output);
  }

  // ---- Wizard ------------------------------------------------------------

  // A starter box in the middle of page 1, in PDF points.
  private centeredBbox(): number[] | null {
    const page = this.docInfo()?.pages?.[0];
    if (!page) return null;
    return [page.width * 0.32, page.height * 0.45, page.width * 0.62, page.height * 0.47];
  }

  // The next field with neither a region box nor a found value — i.e. one that
  // still needs the user to draw its box. Returns null when all are mapped.
  private nextFieldNeedingBox(): string | null {
    const read = this.readByOutput();
    for (const m of this.mappings()) {
      const hasRegion = m.strategy === 'region' && !!m.bbox && m.bbox.length === 4;
      const hasValue = !!read.get(m.output)?.value;
      if (!hasRegion && !hasValue) return m.output;
    }
    return null;
  }

  // Point the wizard at the first field that still needs mapping (after a read).
  private initWizard(): void {
    this.summaryMode.set(false);
    this.activeField.set(this.nextFieldNeedingBox() ?? this.mappings()[0]?.output ?? null);
  }

  // Single click on a field row: make it active (its box, if any, is shown to
  // move/resize; otherwise draw mode arms so the user can draw it).
  selectField(output: string): void {
    this.selectedOutput.set(output);
    this.activeField.set(output);
    this.summaryMode.set(false);
  }

  // Double click on a field row: drop a centered box for it straight away.
  async dropCenteredBox(output: string): Promise<void> {
    const index = this.mappings().findIndex((m) => m.output === output);
    if (index < 0) return;
    this.selectField(output);
    const bbox = this.centeredBbox();
    if (!bbox) return;
    this.updateMapping(index, { strategy: 'region', page: 1, bbox });
    await this.autoRead();
  }

  // Finished drawing a box for the active field: map it, read it, advance.
  async onBoxDraw(ev: { page: number; bbox: number[] }): Promise<void> {
    const field = this.activeField();
    if (!field) return;
    const index = this.mappings().findIndex((m) => m.output === field);
    if (index < 0) return;
    this.updateMapping(index, { strategy: 'region', page: ev.page, bbox: ev.bbox });
    await this.autoRead();
    const next = this.nextFieldNeedingBox();
    if (next) {
      this.activeField.set(next);
      this.selectedOutput.set(next);
    } else {
      this.activeField.set(null);
      this.summaryMode.set(true);
      this.status.set('All fields mapped — review the summary, then Save.');
    }
  }

  toggleSummary(): void {
    this.summaryMode.update((v) => !v);
  }

  // ---- First-time setup: suggested output fields -------------------------

  private async loadSuggestions(docId: string): Promise<void> {
    const res = await this.api.suggestFields(docId);
    this.suggestRows.set(
      res.suggestions.map((s: FieldSuggestion) => ({
        include: true,
        exportKey: s.suggested_key,
        readLabels: (s.read_labels ?? []).join(', '),
        value: s.value ?? '',
        category: s.category,
        valueType: s.value_type,
      })),
    );
  }

  updateSuggestRow(index: number, patch: Partial<SuggestRow>): void {
    this.suggestRows.update((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  // Confirm the chosen suggestions → create the output fields (with read-labels
  // as aliases), then enter the normal blank-template wizard.
  async acceptSuggestions(): Promise<void> {
    const chosen = this.suggestRows().filter((r) => r.include && r.exportKey.trim());
    this.busy.set(true);
    try {
      for (let i = 0; i < chosen.length; i++) {
        const r = chosen[i];
        await this.api.upsertOutputField({
          key: r.exportKey.trim(),
          display_name: r.exportKey.trim(),
          value_type: r.valueType,
          sort_order: i,
          aliases: r.readLabels.split(',').map((s) => s.trim()).filter(Boolean),
        });
      }
      this.suggestRows.set([]);
      await this.reloadOutputFields();
      this.startBlankTemplate();
      await this.autoRead();
      this.initWizard();
      this.status.set(`Created ${chosen.length} output fields — now map them.`);
    } finally {
      this.busy.set(false);
    }
  }

  async dismissSuggestions(): Promise<void> {
    this.suggestRows.set([]);
    this.startBlankTemplate();
    await this.autoRead();
    this.initWizard();
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
