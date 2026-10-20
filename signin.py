# SPDX-License-Identifier: GPL-2.0
"""Signing in to a Source's Account, as one state the window draws.

    sign = SignIn(source, run=app.work, later=lambda ms, fn: ...)
    sign.state        {"kind": ..., ...}, see below
    sign.begin()      start signing in; cancel(); logout()
    sign.begin(how)   a "cookies" source: with a browser's cookies (an id
                      of its browsers()) instead of the file
                      (locked: asks the keyring again instead)
    sign.watch(fn)    fn(state) on every change

States:
  out       {"hint"} (a "cookies" source: where to put the file)
  getting   asking the source for a code
  waiting   {"url", "code"}: shown until confirmed on a phone
  checking  asking the source whether the file signs in
  in        {"name"}
  failed    {"note"}; begin() tries again
  locked    {"note"}: the person refused to unlock the keyring, so the
            Account is not read (nor dropped); begin() asks it again

It drives the source's own steps (login_start, login_poll, login_check,
logout; see the sources package): run(func, done=...) runs one on a
worker, later(ms, fn) calls back on the caller's thread. A code is asked
about every POLL_MS; when it expires a new one is shown, up to RETRIES
codes. cancel() (Настройки left) stops all that. Not thread-safe: every
call comes from the caller's (GTK) thread.
"""
POLL_MS = 3000
RETRIES = 5
LOCKED_NOTE = "Связка ключей закрыта: вход в аккаунт хранится в ней."


class SignIn:
    def __init__(self, source, run, later, poll_ms=POLL_MS, retries=RETRIES):
        self.source = source
        self.run = run
        self.later = later
        self.poll_ms = poll_ms
        self.retries = retries
        self.watchers = []
        self.attempt = 0  # a new begin() or cancel() makes older callbacks moot
        self.state = self._settled()

    def watch(self, fn):
        self.watchers.append(fn)

    def _settled(self):
        account = self.source.account()
        if getattr(self.source, "locked", False):
            return {"kind": "locked", "note": LOCKED_NOTE}
        if account:
            return {"kind": "in", "name": account["name"]}
        if self.source.login == "cookies":
            return {"kind": "out", "hint": self.source.login_hint}
        return {"kind": "out"}

    def _set(self, state):
        self.state = state
        for fn in list(self.watchers):
            fn(state)

    def busy(self):
        return self.state["kind"] in ("getting", "waiting", "checking")

    def begin(self, how=None):
        self.attempt += 1
        if self.state["kind"] == "locked":
            self._set({"kind": "checking"})
            attempt = self.attempt
            self.run(self.source.unlock, done=lambda _r, _e: self._unlocked(attempt))
        elif self.source.login == "code":
            self._new_code(self.attempt, 0)
        else:
            self._set({"kind": "checking"})
            attempt = self.attempt
            self.run(self.source.login_check, *([how] if how else []),
                     done=lambda account, error: self._checked(attempt, account, error,
                                                               how))

    def _unlocked(self, attempt):
        if attempt == self.attempt:
            self._set(self._settled())

    def cancel(self):
        """Stop waiting (Настройки left); what is settled stays."""
        self.attempt += 1
        if self.busy():
            self._set(self._settled())

    def logout(self):
        self.attempt += 1
        self.source.logout()
        self._set(self._settled())

    def refresh(self):
        """Look again at what the source keeps (a token revoked meanwhile)."""
        if not self.busy() and self.state["kind"] != "failed":
            settled = self._settled()
            if settled != self.state:
                self._set(settled)

    # --- a code confirmed on a phone ---------------------------------------
    def _new_code(self, attempt, tries):
        self._set({"kind": "getting"})
        self.run(self.source.login_start,
                 done=lambda got, error: self._got_code(attempt, tries, got, error))

    def _got_code(self, attempt, tries, got, error):
        if attempt != self.attempt:
            return
        if error is not None:
            self._set({"kind": "failed", "note": "Не удалось получить код: нет связи?"})
            return
        self._set({"kind": "waiting", "url": got["url"], "code": got["code"]})
        self.later(self.poll_ms, lambda: self._ask(attempt, tries))

    def _ask(self, attempt, tries):
        if attempt != self.attempt:
            return
        self.run(self.source.login_poll,
                 done=lambda account, error: self._answered(attempt, tries, account, error))

    def _answered(self, attempt, tries, account, error):
        if attempt != self.attempt:
            return
        if error is not None and getattr(self.source, "locked", False):
            self._set(self._settled())
        elif error is not None:
            # The code lives a few minutes: a new one, as a TV shows.
            if tries + 1 < self.retries:
                self._new_code(attempt, tries + 1)
            else:
                self._set({"kind": "failed", "note": "Код устарел."})
        elif account is None:
            self.later(self.poll_ms, lambda: self._ask(attempt, tries))
        else:
            self._set({"kind": "in", "name": account["name"]})

    # --- a cookies file ------------------------------------------------------
    def _checked(self, attempt, account, error, how=None):
        if attempt != self.attempt:
            return
        if error is not None and getattr(self.source, "locked", False):
            self._set(self._settled())
        elif error is not None or not account:
            self._set({"kind": "failed", "note": (
                "Войти не удалось: в браузере нет входа в YouTube, он не дал свои cookies "
                "или нет связи." if how else
                "Войти не удалось: файла нет, он устарел или нет связи.")})
        else:
            self._set({"kind": "in", "name": account["name"]})
