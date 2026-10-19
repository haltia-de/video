# SPDX-License-Identifier: GPL-2.0
import unittest

from signin import SignIn


class CodeSource:
    """A source signing in by a code; answers queued by the test."""
    login = "code"

    def __init__(self, polls=(), signed_in=None):
        self.polls = list(polls)  # None (waiting), an account, or an exception
        self.user = signed_in
        self.codes = 0

    def account(self):
        return self.user

    def login_start(self):
        self.codes += 1
        return {"url": "https://qr/%d" % self.codes, "code": "C%d" % self.codes}

    def login_poll(self):
        answer = self.polls.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if answer:
            self.user = answer
        return answer

    def logout(self):
        self.user = None


class CookiesSource:
    login = "cookies"
    login_hint = "put the file there"

    def __init__(self, check=None):
        self.check = check
        self.user = None
        self.asked_with = []

    def account(self):
        return self.user

    def login_check(self, browser=None):
        self.asked_with.append(browser)
        if isinstance(self.check, Exception):
            raise self.check
        self.user = self.check
        return self.check

    def logout(self):
        self.user = None


class Driver:
    """run() and later() that the test steps by hand."""

    def __init__(self):
        self.timers = []

    def run(self, func, *args, done):
        try:
            value, error = func(*args), None
        except Exception as exc:
            value, error = None, exc
        done(value, error)

    def later(self, ms, fn):
        self.timers.append((ms, fn))

    def tick(self):
        ms, fn = self.timers.pop(0)
        fn()
        return ms


class SignInTest(unittest.TestCase):
    def sign(self, source):
        self.drv = Driver()
        s = SignIn(source, run=self.drv.run, later=self.drv.later, poll_ms=3000, retries=3)
        self.seen = []
        s.watch(lambda state: self.seen.append(state["kind"]))
        return s

    def test_starts_from_what_the_source_keeps(self):
        self.assertEqual(self.sign(CodeSource()).state, {"kind": "out"})
        self.assertEqual(self.sign(CodeSource(signed_in={"name": "Анна"})).state,
                         {"kind": "in", "name": "Анна"})
        self.assertEqual(self.sign(CookiesSource()).state,
                         {"kind": "out", "hint": "put the file there"})

    def test_a_code_shown_then_confirmed(self):
        src = CodeSource(polls=[None, None, {"name": "Анна"}])
        s = self.sign(src)
        s.begin()
        self.assertEqual(s.state, {"kind": "waiting", "url": "https://qr/1", "code": "C1"})
        self.assertEqual(self.drv.tick(), 3000)
        self.drv.tick()
        self.assertEqual(s.state["kind"], "waiting")
        self.drv.tick()
        self.assertEqual(s.state, {"kind": "in", "name": "Анна"})
        self.assertEqual(self.seen, ["getting", "waiting", "in"])
        self.assertEqual(self.drv.timers, [], "no more asking")

    def test_an_expired_code_is_replaced_until_the_retries_run_out(self):
        src = CodeSource(polls=[OSError("expired")] * 3)
        s = self.sign(src)
        s.begin()
        self.drv.tick()
        self.assertEqual(s.state["code"], "C2")
        self.drv.tick()
        self.assertEqual(s.state["code"], "C3")
        self.drv.tick()
        self.assertEqual(s.state, {"kind": "failed", "note": "Код устарел."})
        s.begin()
        self.assertEqual(s.state["code"], "C4", "tried again from the start")

    def test_no_code_without_network(self):
        src = CodeSource()
        src.login_start = lambda: (_ for _ in ()).throw(OSError("down"))
        s = self.sign(src)
        s.begin()
        self.assertEqual(s.state["kind"], "failed")
        self.assertIn("нет связи", s.state["note"])

    def test_cancel_stops_asking_and_returns_to_out(self):
        src = CodeSource(polls=[{"name": "Анна"}])
        s = self.sign(src)
        s.begin()
        s.cancel()
        self.assertEqual(s.state, {"kind": "out"})
        self.drv.tick()  # the timer set before cancel: does nothing
        self.assertEqual(s.state, {"kind": "out"})
        self.assertEqual(src.polls, [{"name": "Анна"}], "not asked")

    def test_cancel_keeps_a_settled_state(self):
        s = self.sign(CodeSource(signed_in={"name": "Анна"}))
        s.cancel()
        self.assertEqual(s.state["kind"], "in")
        self.assertEqual(self.seen, [])

    def test_begin_again_drops_the_older_code(self):
        src = CodeSource(polls=[None, {"name": "Анна"}])
        s = self.sign(src)
        s.begin()
        s.begin()
        self.assertEqual(s.state["code"], "C2")
        self.drv.tick()  # the first code's timer: moot
        self.assertEqual(src.polls, [None, {"name": "Анна"}])
        self.drv.tick()
        self.drv.tick()
        self.assertEqual(s.state["kind"], "in")

    def test_logout(self):
        src = CodeSource(signed_in={"name": "Анна"})
        s = self.sign(src)
        s.logout()
        self.assertEqual((s.state, src.user), ({"kind": "out"}, None))

    def test_cookies_checked(self):
        s = self.sign(CookiesSource(check={"name": "аккаунт Google"}))
        s.begin()
        self.assertEqual(s.state, {"kind": "in", "name": "аккаунт Google"})
        self.assertEqual(self.seen, ["checking", "in"])

    def test_cookies_refused(self):
        for check in (None, OSError("403")):
            s = self.sign(CookiesSource(check=check))
            s.begin()
            self.assertEqual(s.state["kind"], "failed")
            self.assertIn("файла нет", s.state["note"])

    def test_cookies_from_a_browser(self):
        src = CookiesSource(check={"name": "аккаунт Google (Firefox)"})
        s = self.sign(src)
        s.begin("firefox")
        self.assertEqual(src.asked_with, ["firefox"])
        self.assertEqual(s.state, {"kind": "in", "name": "аккаунт Google (Firefox)"})
        s = self.sign(CookiesSource(check=None))
        s.begin("chromium")
        self.assertIn("в браузере нет входа", s.state["note"])

    def test_refresh_sees_a_revoked_token(self):
        src = CodeSource(signed_in={"name": "Анна"})
        s = self.sign(src)
        src.user = None  # the source signed out by itself (token revoked)
        s.refresh()
        self.assertEqual(s.state, {"kind": "out"})
        s.refresh()
        self.assertEqual(self.seen, ["out"], "told once")


if __name__ == "__main__":
    unittest.main()
