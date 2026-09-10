import { Component, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

interface Site {
  id: number;
  name: string;
  fingerprint: string;
  status: string;
  created_at: string;
  activated_at: string | null;
}

interface CentralTemplate {
  id: number;
  identifier: string;
  identifier_kind: string;
  name: string;
  layout_fingerprint: string;
  version: number;
  field_count: number;
  withdrawn: boolean;
  published_at: string;
  updated_at: string;
  valid_count: number;
  invalid_count: number;
  reporting_sites: number;
}

interface VocabularyRow {
  label: string;
  suggested_key: string | null;
  sites: number;
  revealed: boolean;
}

const URL_KEY = 'lesarin.central.url';
const TOKEN_KEY = 'lesarin.central.token';

/**
 * The central back-office: central's own team-account surface (not the
 * per-tenant SaaS login). A central deployment is a separate service at its
 * own origin, so this page keeps its own "which central, which token" state
 * rather than reusing app.ts's Auth service.
 *
 * Sites push already-confirmed templates (docs/brain-sync.md) — there's no
 * approve/publish step here, only monitoring: which templates are live,
 * which labels have earned their way into the shared vocabulary, and a
 * manual withdraw for a template that turns out to be wrong.
 */
@Component({
  selector: 'app-backoffice',
  imports: [FormsModule],
  templateUrl: './backoffice.html',
  styleUrl: './backoffice.css',
})
export class BackOffice {
  private readonly http = inject(HttpClient);

  readonly centralUrl = signal(localStorage.getItem(URL_KEY) ?? '');
  readonly token = signal(localStorage.getItem(TOKEN_KEY) ?? '');
  readonly authed = () => !!this.token();

  readonly mode = signal<'login' | 'bootstrap'>('login');
  email = '';
  password = '';
  totp = '';
  bootstrapToken = '';

  readonly busy = signal(false);
  readonly error = signal('');
  readonly notice = signal('');

  readonly sites = signal<Site[]>([]);
  readonly templates = signal<CentralTemplate[]>([]);
  readonly vocabulary = signal<VocabularyRow[]>([]);
  enrollmentNote = '';

  constructor() {
    if (this.authed()) void this.refresh();
  }

  private base(): string {
    return this.centralUrl().replace(/\/$/, '');
  }

  private authHeaders() {
    return { Authorization: `Bearer ${this.token()}` };
  }

  saveCentralUrl(): void {
    localStorage.setItem(URL_KEY, this.centralUrl());
  }

  async signIn(): Promise<void> {
    this.error.set('');
    this.busy.set(true);
    try {
      this.saveCentralUrl();
      const path = this.mode() === 'login' ? '/admin/login' : '/admin/bootstrap';
      const body: Record<string, string> = { email: this.email, password: this.password };
      if (this.mode() === 'login' && this.totp) body['totp'] = this.totp;
      if (this.mode() === 'bootstrap') body['bootstrap_token'] = this.bootstrapToken;
      const res = await firstValueFrom(
        this.http.post<{ token: string }>(`${this.base()}${path}`, body),
      );
      this.token.set(res.token);
      localStorage.setItem(TOKEN_KEY, res.token);
      await this.refresh();
    } catch (err: unknown) {
      this.error.set(detail(err) ?? 'Sign-in failed.');
    } finally {
      this.busy.set(false);
    }
  }

  signOut(): void {
    this.token.set('');
    localStorage.removeItem(TOKEN_KEY);
    this.sites.set([]);
    this.templates.set([]);
    this.vocabulary.set([]);
  }

  async refresh(): Promise<void> {
    this.error.set('');
    try {
      const [sites, templates, vocabulary] = await Promise.all([
        firstValueFrom(
          this.http.get<Site[]>(`${this.base()}/admin/sites`, { headers: this.authHeaders() }),
        ),
        firstValueFrom(
          this.http.get<CentralTemplate[]>(`${this.base()}/admin/templates`, {
            headers: this.authHeaders(),
          }),
        ),
        firstValueFrom(
          this.http.get<VocabularyRow[]>(`${this.base()}/admin/vocabulary`, {
            headers: this.authHeaders(),
          }),
        ),
      ]);
      this.sites.set(sites);
      this.templates.set(templates);
      this.vocabulary.set(vocabulary);
    } catch (err: unknown) {
      this.error.set(detail(err) ?? 'Could not reach the central service.');
    }
  }

  // ---- Sites ------------------------------------------------------------------

  async mintEnrollmentToken(): Promise<void> {
    this.notice.set('');
    try {
      const res = await firstValueFrom(
        this.http.post<{ token: string }>(
          `${this.base()}/admin/enrollment-tokens`,
          { note: this.enrollmentNote },
          { headers: this.authHeaders() },
        ),
      );
      this.notice.set(`One-time enrollment token (shown once): ${res.token}`);
      this.enrollmentNote = '';
    } catch (err: unknown) {
      this.error.set(detail(err) ?? 'Could not mint a token.');
    }
  }

  async activateSite(site: Site): Promise<void> {
    await firstValueFrom(
      this.http.post(`${this.base()}/admin/sites/${site.id}/activate`, {}, { headers: this.authHeaders() }),
    );
    await this.refresh();
  }

  async revokeSite(site: Site): Promise<void> {
    await firstValueFrom(
      this.http.post(`${this.base()}/admin/sites/${site.id}/revoke`, {}, { headers: this.authHeaders() }),
    );
    await this.refresh();
  }

  // ---- Templates ----------------------------------------------------------------

  async withdrawTemplate(t: CentralTemplate): Promise<void> {
    if (!confirm(`Withdraw the template for ${t.name} (${t.identifier})? Sites will stop pulling it.`)) {
      return;
    }
    await firstValueFrom(
      this.http.post(`${this.base()}/admin/templates/${t.id}/withdraw`, {}, { headers: this.authHeaders() }),
    );
    await this.refresh();
  }
}

function detail(err: unknown): string | null {
  const e = err as { error?: { detail?: string | { msg?: string }[] } };
  const d = e?.error?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  return null;
}
