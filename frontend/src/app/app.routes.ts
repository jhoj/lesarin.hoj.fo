import { Routes } from '@angular/router';

import { authGuard, staffGuard } from './auth.guard';
import { Customer } from './customer';
import { Login } from './login';
import { Studio } from './studio';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'app' },
  { path: 'login', component: Login },
  { path: 'app', component: Customer, canActivate: [authGuard] },
  { path: 'studio', component: Studio, canActivate: [staffGuard] },
  { path: '**', redirectTo: 'app' },
];
