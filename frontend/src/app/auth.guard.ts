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
