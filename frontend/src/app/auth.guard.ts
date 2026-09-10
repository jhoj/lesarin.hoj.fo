import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { Auth } from './auth';

/** Gate routes behind a token; send anonymous visitors to /login. */
export const authGuard: CanActivateFn = () => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (auth.isAuthed()) return true;
  return router.createUrlTree(['/login']);
};

/** The studio edits shared vendor knowledge, so it's staff-only. This just
 *  keeps customers from wandering in — the API refuses them regardless. */
export const staffGuard: CanActivateFn = () => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (!auth.isAuthed()) return router.createUrlTree(['/login']);
  return auth.isStaff() ? true : router.createUrlTree(['/app']);
};
