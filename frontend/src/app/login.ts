import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Api } from './api';
import { Auth } from './auth';

@Component({
  selector: 'app-login',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="auth-wrap">
      <form class="panel auth-card" (ngSubmit)="submit()">
        <div class="brand">Lesarin</div>
        <p class="muted">Upload an invoice. Get your data, your way.</p>

        <h2>{{ mode() === 'login' ? 'Log in' : 'Create your account' }}</h2>

        <label>
          Email
          <input type="email" name="email" autocomplete="username" [(ngModel)]="email" required />
        </label>
        <label>
          Password
          <input
            type="password"
            name="password"
            [autocomplete]="mode() === 'login' ? 'current-password' : 'new-password'"
            minlength="8"
            [(ngModel)]="password"
            required
          />
        </label>

        @if (mfaRequired()) {
          <label>
            Authenticator code
            <input
              type="text"
              name="totp"
              inputmode="numeric"
              autocomplete="one-time-code"
              placeholder="6-digit code, or a recovery code"
              [(ngModel)]="totp"
              autofocus
            />
          </label>
        }

        <button class="primary" type="submit" [disabled]="busy()">
          {{ mode() === 'login' ? 'Log in' : 'Sign up' }}
        </button>

        @if (error()) {
          <p class="err" role="alert">{{ error() }}</p>
        }

        <p class="muted small switch">
          {{ mode() === 'login' ? 'No account yet?' : 'Already have an account?' }}
          <a href="#" (click)="toggle($event)">{{ mode() === 'login' ? 'Create one' : 'Log in' }}</a>
        </p>
        <p class="muted small"><a routerLink="/studio">Open the vendor template studio →</a></p>
      </form>
    </div>
  `,
  styles: [
    `
      .auth-wrap {
        display: grid;
        place-items: center;
        min-height: 100vh;
        padding: 1rem;
      }
      .auth-card {
        width: min(380px, 100%);
        display: flex;
        flex-direction: column;
        gap: 0.7rem;
        padding: 1.4rem 1.5rem;
      }
      .auth-card .brand {
        font-size: 1.3rem;
      }
      .auth-card h2 {
        margin: 0.3rem 0 0;
        font-size: 1.05rem;
      }
      .auth-card label {
        display: flex;
        flex-direction: column;
        gap: 0.2rem;
        font-size: 0.85rem;
        color: var(--muted);
      }
      .auth-card .primary {
        margin-top: 0.3rem;
        padding: 0.5rem;
      }
      .err {
        color: #c0392b;
        margin: 0;
      }
      .switch a,
      .auth-card a {
        color: var(--accent);
      }
    `,
  ],
})
export class Login {
  private readonly api = inject(Api);
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  readonly mode = signal<'login' | 'register'>('login');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly mfaRequired = signal(false);
  email = '';
  password = '';
  totp = '';

  toggle(ev: Event): void {
    ev.preventDefault();
    this.mode.update((m) => (m === 'login' ? 'register' : 'login'));
    this.error.set('');
    this.mfaRequired.set(false);
  }

  async submit(): Promise<void> {
    this.error.set('');
    this.busy.set(true);
    try {
      const res =
        this.mode() === 'login'
          ? await this.api.login(this.email, this.password, this.mfaChallenge())
          : await this.api.register(this.email, this.password);
      this.auth.setSession(res.token, res.email);
      await this.router.navigate(['/app']);
    } catch (err: unknown) {
      const msg = detail(err) ?? 'Something went wrong. Try again.';
      if (/MFA code/i.test(msg)) {
        this.mfaRequired.set(true);
      }
      this.error.set(msg);
    } finally {
      this.busy.set(false);
    }
  }

  /** A recovery code is longer and carries a dash; anything else is a TOTP code. */
  private mfaChallenge(): { totp?: string; recovery_code?: string } | undefined {
    const code = this.totp.trim();
    if (!code) return undefined;
    return code.includes('-') ? { recovery_code: code } : { totp: code };
  }
}

/** Pull FastAPI's `{detail}` message out of an HttpErrorResponse-shaped error. */
function detail(err: unknown): string | null {
  const e = err as { error?: { detail?: string | { msg?: string }[] } };
  const d = e?.error?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  return null;
}
