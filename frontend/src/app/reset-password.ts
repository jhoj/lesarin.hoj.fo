import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { Api } from './api';
import { Auth } from './auth';

/** Landing page for the link in a password-reset email. */
@Component({
  selector: 'app-reset-password',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="auth-wrap">
      <form class="panel auth-card" (ngSubmit)="submit()">
        <div class="brand">Lesarin</div>
        <h2>Choose a new password</h2>

        @if (!token) {
          <p class="err" role="alert">
            This link is missing its token. Request a new one from the login page.
          </p>
        } @else {
          <label>
            New password
            <input
              type="password"
              name="password"
              autocomplete="new-password"
              minlength="8"
              [(ngModel)]="password"
              required
            />
          </label>
          <button class="primary" type="submit" [disabled]="busy() || password.length < 8">
            Set password
          </button>
        }

        @if (error()) {
          <p class="err" role="alert">{{ error() }}</p>
        }
        <p class="muted small"><a routerLink="/login">Back to login</a></p>
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
      .auth-card a {
        color: var(--accent);
      }
    `,
  ],
})
export class ResetPassword {
  private readonly api = inject(Api);
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  readonly token = inject(ActivatedRoute).snapshot.queryParamMap.get('token') ?? '';
  readonly busy = signal(false);
  readonly error = signal('');
  password = '';

  async submit(): Promise<void> {
    if (!this.token) return;
    this.error.set('');
    this.busy.set(true);
    try {
      // A successful reset signs you straight in — there's no sense asking for
      // the password again on the very next screen.
      const res = await this.api.resetPassword(this.token, this.password);
      this.auth.setSession(res.token, res.email);
      this.auth.setStaff((await this.api.me()).is_staff);
      await this.router.navigate(['/app']);
    } catch (err: unknown) {
      const e = err as { error?: { detail?: string } };
      this.error.set(e?.error?.detail ?? 'Could not reset the password. Request a new link.');
    } finally {
      this.busy.set(false);
    }
  }
}
