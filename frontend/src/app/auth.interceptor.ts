import { inject } from '@angular/core';
import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { Auth } from './auth';

/** Attach the bearer token to API calls and bounce to /login on a 401. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  const router = inject(Router);

  const token = auth.token;
  const authed = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;

  return next(authed).pipe(
    catchError((err: HttpErrorResponse) => {
      // Only the authenticated SaaS surface should force a re-login; the studio
      // endpoints are public, so a 401 there (shouldn't happen) is left alone.
      if (err.status === 401 && req.url.includes('/api/me')) {
        auth.clear();
        void router.navigate(['/login']);
      }
      return throwError(() => err);
    }),
  );
};
