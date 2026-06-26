import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Auth } from './auth';

/** Root shell: a top nav (shown once signed in) over the routed views. */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    @if (auth.isAuthed()) {
      <nav class="nav">
        <span class="brand">Lesarin</span>
        <a routerLink="/app" routerLinkActive="active">Export</a>
        <a routerLink="/studio" routerLinkActive="active">Studio</a>
        <span class="spacer"></span>
        <span class="who muted">{{ auth.email() }}</span>
        <button class="ghost" (click)="logout()">Log out</button>
      </nav>
    }
    <router-outlet />
  `,
  styles: [
    `
      .nav {
        display: flex;
        align-items: center;
        gap: 1rem;
        padding: 0.6rem 1rem;
        background: var(--panel);
        border-bottom: 1px solid var(--line);
      }
      .nav .brand {
        font-weight: 700;
        font-size: 1.05rem;
      }
      .nav a {
        color: var(--muted);
        text-decoration: none;
        padding: 0.2rem 0.1rem;
        border-bottom: 2px solid transparent;
      }
      .nav a.active {
        color: var(--ink);
        border-bottom-color: var(--accent);
      }
      .nav .spacer {
        flex: 1;
      }
      .nav .who {
        font-size: 0.85rem;
      }
    `,
  ],
})
export class App {
  readonly auth = inject(Auth);
  private readonly router = inject(Router);

  logout(): void {
    this.auth.clear();
    void this.router.navigate(['/login']);
  }
}
