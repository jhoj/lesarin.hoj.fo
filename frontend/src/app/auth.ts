import { Injectable, computed, signal } from '@angular/core';

const TOKEN_KEY = 'lesarin.token';
const EMAIL_KEY = 'lesarin.email';
const STAFF_KEY = 'lesarin.staff';

/** Holds the bearer token (persisted in localStorage) and the signed-in email. */
@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly _token = signal<string | null>(localStorage.getItem(TOKEN_KEY));
  readonly email = signal<string | null>(localStorage.getItem(EMAIL_KEY));
  readonly isAuthed = computed(() => !!this._token());
  /** Only for deciding what to show. The API enforces this independently —
   *  flipping it here gains a customer nothing but a 403. */
  readonly isStaff = signal(localStorage.getItem(STAFF_KEY) === 'true');

  setStaff(isStaff: boolean): void {
    localStorage.setItem(STAFF_KEY, String(isStaff));
    this.isStaff.set(isStaff);
  }

  get token(): string | null {
    return this._token();
  }

  setSession(token: string, email: string): void {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(EMAIL_KEY, email);
    this._token.set(token);
    this.email.set(email);
  }

  clear(): void {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
    localStorage.removeItem(STAFF_KEY);
    this._token.set(null);
    this.email.set(null);
    this.isStaff.set(false);
  }
}
