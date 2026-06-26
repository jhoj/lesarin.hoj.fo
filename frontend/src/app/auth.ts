import { Injectable, computed, signal } from '@angular/core';

const TOKEN_KEY = 'lesarin.token';
const EMAIL_KEY = 'lesarin.email';

/** Holds the bearer token (persisted in localStorage) and the signed-in email. */
@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly _token = signal<string | null>(localStorage.getItem(TOKEN_KEY));
  readonly email = signal<string | null>(localStorage.getItem(EMAIL_KEY));
  readonly isAuthed = computed(() => !!this._token());

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
    this._token.set(null);
    this.email.set(null);
  }
}
