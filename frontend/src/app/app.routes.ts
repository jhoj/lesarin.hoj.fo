import { Routes } from '@angular/router';

import { authGuard, staffGuard } from './auth.guard';
import { BackOffice } from './backoffice';
import { Customer } from './customer';
import { Login } from './login';
import { ResetPassword } from './reset-password';
import { Studio } from './studio';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'app' },
  { path: 'login', component: Login },
  { path: 'reset-password', component: ResetPassword },
  { path: 'app', component: Customer, canActivate: [authGuard] },
  { path: 'studio', component: Studio, canActivate: [staffGuard] },
  // Central's own team accounts, not the per-tenant SaaS login — no guard.
  { path: 'backoffice', component: BackOffice },
  { path: '**', redirectTo: 'app' },
];
