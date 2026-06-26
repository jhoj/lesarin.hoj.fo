import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from './api';
import { CanonicalField, ExportFormat, OutputProfile, ProfilePayload } from './models';

/** One editable row in the profile editor: a canonical field + whether it's
 *  included and what the customer wants it called in their output. */
interface FieldRow {
  canonical: string;
  display_name: string;
  on: boolean;
  output_name: string;
}

@Component({
  selector: 'app-customer',
  imports: [FormsModule],
  templateUrl: './customer.html',
  styleUrl: './customer.css',
})
export class Customer implements OnInit {
  private readonly api = inject(Api);

  readonly canonical = signal<CanonicalField[]>([]);
  readonly profiles = signal<OutputProfile[]>([]);

  // Export panel state.
  readonly exportProfileId = signal<number | null>(null);
  readonly exportFormat = signal<string>('');
  readonly file = signal<File | null>(null);
  readonly output = signal<string | null>(null);
  readonly status = signal('');
  readonly busy = signal(false);
  private lastBlob: { body: string; filename: string; contentType: string } | null = null;

  // Profile editor state.
  readonly editing = signal<OutputProfile | 'new' | null>(null);
  readonly editName = signal('');
  readonly editFormat = signal<ExportFormat>('json');
  readonly editDefault = signal(false);
  readonly editRows = signal<FieldRow[]>([]);
  readonly editError = signal('');

  readonly formats: ExportFormat[] = ['json', 'xml', 'ubl', 'oioubl'];

  async ngOnInit(): Promise<void> {
    this.canonical.set(await this.api.canonicalFields());
    await this.reloadProfiles();
  }

  private async reloadProfiles(): Promise<void> {
    const profiles = await this.api.listProfiles();
    this.profiles.set(profiles);
    const current = this.exportProfileId();
    if (current == null || !profiles.some((p) => p.id === current)) {
      this.exportProfileId.set((profiles.find((p) => p.is_default) ?? profiles[0])?.id ?? null);
    }
  }

  // ---- Export -------------------------------------------------------------

  onFileInput(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.setFile(input.files?.[0] ?? null);
    input.value = '';
  }

  onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.setFile(ev.dataTransfer?.files?.[0] ?? null);
  }

  onDragOver(ev: DragEvent): void {
    ev.preventDefault();
  }

  private setFile(file: File | null): void {
    this.file.set(file);
    this.output.set(null);
    this.lastBlob = null;
    this.status.set(file ? `Ready: ${file.name}` : '');
  }

  async runExport(): Promise<void> {
    const file = this.file();
    if (!file) return;
    this.busy.set(true);
    this.status.set('Reading…');
    try {
      const res = await this.api.exportInvoice(file, this.exportProfileId(), this.exportFormat() || null);
      this.lastBlob = res;
      this.output.set(res.body);
      this.status.set('Done.');
    } catch (err: unknown) {
      this.status.set(detail(err) ?? 'Export failed.');
    } finally {
      this.busy.set(false);
    }
  }

  download(): void {
    if (!this.lastBlob) return;
    const blob = new Blob([this.lastBlob.body], { type: this.lastBlob.contentType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = this.lastBlob.filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ---- Profile editor -----------------------------------------------------

  newProfile(): void {
    this.openEditor('new');
  }

  editProfile(p: OutputProfile): void {
    this.openEditor(p);
  }

  private openEditor(target: OutputProfile | 'new'): void {
    this.editing.set(target);
    this.editError.set('');
    const isNew = target === 'new';
    const profile = isNew ? null : target;
    this.editName.set(profile ? profile.name : '');
    this.editFormat.set(profile ? profile.fmt : 'json');
    this.editDefault.set(profile ? profile.is_default : this.profiles().length === 0);
    const chosen = new Map((profile?.fields ?? []).map((f) => [f.canonical, f.output_name]));
    this.editRows.set(
      this.canonical().map((f) => ({
        canonical: f.key,
        display_name: f.display_name,
        on: isNew ? true : chosen.has(f.key),
        output_name: isNew ? f.key : chosen.get(f.key) ?? f.key,
      })),
    );
  }

  cancelEdit(): void {
    this.editing.set(null);
  }

  async saveProfile(): Promise<void> {
    const fields = this.editRows()
      .filter((r) => r.on)
      .map((r) => ({ canonical: r.canonical, output_name: r.output_name.trim() || r.canonical }));
    if (!fields.length) {
      this.editError.set('Pick at least one field.');
      return;
    }
    const payload: ProfilePayload = {
      name: this.editName().trim() || 'Untitled',
      fmt: this.editFormat(),
      is_default: this.editDefault(),
      fields,
    };
    this.busy.set(true);
    try {
      const target = this.editing();
      if (target && target !== 'new') {
        await this.api.updateProfile(target.id, payload);
      } else {
        await this.api.createProfile(payload);
      }
      this.editing.set(null);
      await this.reloadProfiles();
    } catch (err: unknown) {
      this.editError.set(detail(err) ?? 'Could not save the profile.');
    } finally {
      this.busy.set(false);
    }
  }

  async deleteProfile(): Promise<void> {
    const target = this.editing();
    if (!target || target === 'new') return;
    this.busy.set(true);
    try {
      await this.api.deleteProfile(target.id);
      this.editing.set(null);
      await this.reloadProfiles();
    } finally {
      this.busy.set(false);
    }
  }
}

function detail(err: unknown): string | null {
  const e = err as { error?: { detail?: string | { msg?: string }[] } };
  const d = e?.error?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  return null;
}
