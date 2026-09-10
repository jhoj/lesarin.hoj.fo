import { TestBed } from '@angular/core/testing';

import { Auth } from './auth';

describe('Auth', () => {
  beforeEach(() => {
    localStorage.clear();
    TestBed.resetTestingModule();
  });

  it('starts signed out when nothing is stored', () => {
    const auth = TestBed.inject(Auth);
    expect(auth.isAuthed()).toBe(false);
    expect(auth.token).toBeNull();
    expect(auth.email()).toBeNull();
  });

  it('keeps the session across a page reload', () => {
    TestBed.inject(Auth).setSession('tok-123', 'me@firm.fo');

    // A fresh injector is what a reload looks like: state must come back from
    // localStorage, not from the previous instance.
    TestBed.resetTestingModule();
    const reloaded = TestBed.inject(Auth);

    expect(reloaded.isAuthed()).toBe(true);
    expect(reloaded.token).toBe('tok-123');
    expect(reloaded.email()).toBe('me@firm.fo');
  });

  it('clear() removes the stored session, not just the in-memory one', () => {
    const auth = TestBed.inject(Auth);
    auth.setSession('tok-123', 'me@firm.fo');

    auth.clear();

    expect(auth.isAuthed()).toBe(false);
    expect(auth.token).toBeNull();
    TestBed.resetTestingModule();
    expect(TestBed.inject(Auth).isAuthed()).toBe(false);
  });
});
