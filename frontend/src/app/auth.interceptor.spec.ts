import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';

import { Auth } from './auth';
import { authInterceptor } from './auth.interceptor';

describe('authInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let auth: Auth;
  let navigated: unknown[][];

  beforeEach(() => {
    localStorage.clear();
    navigated = [];
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: { navigate: (c: unknown[]) => navigated.push(c) } },
      ],
    });
    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(Auth);
  });

  afterEach(() => httpMock.verify());

  it('sends no Authorization header when signed out', () => {
    http.get('/api/me').subscribe({ error: () => {} });
    const req = httpMock.expectOne('/api/me');
    expect(req.request.headers.has('Authorization')).toBe(false);
    req.flush({});
  });

  it('attaches the bearer token once signed in', () => {
    auth.setSession('tok-123', 'me@firm.fo');
    http.get('/api/me').subscribe({ error: () => {} });
    const req = httpMock.expectOne('/api/me');
    expect(req.request.headers.get('Authorization')).toBe('Bearer tok-123');
    req.flush({});
  });

  it('clears the session and bounces to /login on a 401 from /api/me', () => {
    auth.setSession('expired', 'me@firm.fo');
    http.get('/api/me/profiles').subscribe({ error: () => {} });

    httpMock
      .expectOne('/api/me/profiles')
      .flush({ detail: 'Not authenticated.' }, { status: 401, statusText: 'Unauthorized' });

    expect(auth.isAuthed()).toBe(false);
    expect(navigated).toEqual([['/login']]);
  });

  it('leaves the session alone on a 401 from the public studio endpoints', () => {
    auth.setSession('tok-123', 'me@firm.fo');
    http.get('/api/vendors').subscribe({ error: () => {} });

    httpMock
      .expectOne('/api/vendors')
      .flush({ detail: 'nope' }, { status: 401, statusText: 'Unauthorized' });

    // Signing a studio user out over an unrelated 401 would be a nasty surprise.
    expect(auth.isAuthed()).toBe(true);
    expect(navigated).toEqual([]);
  });
});
