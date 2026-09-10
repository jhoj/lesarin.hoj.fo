import { Component, OnInit, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { Api } from './api';
import { Auth } from './auth';
import {
  ApiKeyCreated,
  ApiKeyOut,
  CanonicalField,
  ExportFormat,
  ExportRecord,
  ExportQuality,
  Me,
  MfaEnrollOut,
  OutputProfile,
  ProfilePayload,
} from './models';

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
  imports: [DatePipe, FormsModule],
  templateUrl: './customer.html',
  styleUrl: './customer.css',
})
export class Customer implements OnInit {
  private readonly api = inject(Api);
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  readonly canonical = signal<CanonicalField[]>([]);
  readonly profiles = signal<OutputProfile[]>([]);
  readonly history = signal<ExportRecord[]>([]);

  // Security panel state.
  readonly me = signal<Me | null>(null);
  readonly apiKeys = signal<ApiKeyOut[]>([]);
  readonly newKeyName = signal('');
  readonly justCreatedKey = signal<ApiKeyCreated | null>(null);
  readonly mfaEnrollment = signal<MfaEnrollOut | null>(null);
  readonly mfaVerifyCode = signal('');
  readonly mfaDisablePassword = signal('');
  readonly securityError = signal('');
  readonly securityBusy = signal(false);

  // Export panel state.
  readonly exportProfileId = signal<number | null>(null);
  readonly exportFormat = signal<string>('');
  readonly file = signal<File | null>(null);
  readonly output = signal<string | null>(null);
  readonly quality = signal<ExportQuality | null>(null);
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
    await this.reloadHistory();
    await this.reloadSecurity();
  }

  private async reloadProfiles(): Promise<void> {
    const profiles = await this.api.listProfiles();
    this.profiles.set(profiles);
    const current = this.exportProfileId();
    if (current == null || !profiles.some((p) => p.id === current)) {
      this.exportProfileId.set((profiles.find((p) => p.is_default) ?? profiles[0])?.id ?? null);
    }
  }

  private async reloadHistory(): Promise<void> {
    this.history.set(await this.api.listExports());
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
    this.quality.set(null);
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
      this.quality.set(res.quality);
      this.status.set('Done.');
      await this.reloadHistory();
    } catch (err: unknown) {
      this.status.set(detail(err) ?? 'Export failed.');
    } finally {
      this.busy.set(false);
    }
  }

  /** Plain-language summary of how the read went, for the status strip. */
  qualityHeadline(q: ExportQuality): string {
    if (q.source === 'template') {
      return q.vendor ? `Read using the saved mapping for ${q.vendor}.` : 'Read using a saved mapping.';
    }
    if (q.source === 'heuristic') {
      return 'Best-effort read — no saved mapping for this supplier yet.';
    }
    return "Nothing could be read from this document.";
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

  // ---- Security: API keys + MFA --------------------------------------------

  private async reloadSecurity(): Promise<void> {
    const [me, keys] = await Promise.all([this.api.me(), this.api.listApiKeys()]);
    this.me.set(me);
    this.apiKeys.set(keys);
  }

  async createApiKey(): Promise<void> {
    const name = this.newKeyName().trim();
    if (!name) return;
    this.securityError.set('');
    this.securityBusy.set(true);
    try {
      const created = await this.api.createApiKey(name);
      this.justCreatedKey.set(created);
      this.newKeyName.set('');
      this.apiKeys.set(await this.api.listApiKeys());
    } catch (err: unknown) {
      this.securityError.set(detail(err) ?? 'Could not create the key.');
    } finally {
      this.securityBusy.set(false);
    }
  }

  async revokeApiKey(key: ApiKeyOut): Promise<void> {
    this.securityBusy.set(true);
    try {
      await this.api.revokeApiKey(key.id);
      this.apiKeys.set(await this.api.listApiKeys());
    } finally {
      this.securityBusy.set(false);
    }
  }

  dismissCreatedKey(): void {
    this.justCreatedKey.set(null);
  }

  async startMfaEnroll(): Promise<void> {
    this.securityError.set('');
    this.securityBusy.set(true);
    try {
      this.mfaEnrollment.set(await this.api.mfaEnroll());
      this.mfaVerifyCode.set('');
    } catch (err: unknown) {
      this.securityError.set(detail(err) ?? 'Could not start 2FA enrollment.');
    } finally {
      this.securityBusy.set(false);
    }
  }

  cancelMfaEnroll(): void {
    this.mfaEnrollment.set(null);
  }

  async confirmMfaEnroll(): Promise<void> {
    this.securityError.set('');
    this.securityBusy.set(true);
    try {
      await this.api.mfaVerify(this.mfaVerifyCode().trim());
      this.mfaEnrollment.set(null);
      this.me.set(await this.api.me());
    } catch (err: unknown) {
      this.securityError.set(detail(err) ?? 'Invalid code.');
    } finally {
      this.securityBusy.set(false);
    }
  }

  async disableMfa(): Promise<void> {
    this.securityError.set('');
    this.securityBusy.set(true);
    try {
      await this.api.mfaDisable(this.mfaDisablePassword());
      this.mfaDisablePassword.set('');
      this.me.set(await this.api.me());
    } catch (err: unknown) {
      this.securityError.set(detail(err) ?? 'Wrong password.');
    } finally {
      this.securityBusy.set(false);
    }
  }

  async logoutEverywhere(): Promise<void> {
    this.securityBusy.set(true);
    try {
      await this.api.logoutAll();
    } finally {
      this.auth.clear();
      await this.router.navigate(['/login']);
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
