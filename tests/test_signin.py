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


class OTPSource(CodeSource):
    def __init__(self, polls=(), submissions=()):
        super().__init__(polls)
        self.submissions = list(submissions)
        self.submitted = []

    def login_poll(self):
        answer = super().login_poll()
        if answer and answer.get("need_code"):
            self.user = None
        return answer

    def login_submit(self, code):
        self.submitted.append(code)
        answer = self.submissions.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


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


class LockedSource(CodeSource):
    """A keyring the person will not unlock: locked until the test opens it."""

    def __init__(self, polls=()):
        super().__init__(polls)
        self.locked = True
        self.opens = False
        self.kept = {"name": "Анна"}

    def account(self):
        return None if self.locked else self.user

    def unlock(self):
        self.locked = not self.opens
        if self.opens:
            self.user = self.kept


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

    def test_code_from_phone_is_submitted_and_the_same_qr_finishes(self):
        src = OTPSource(polls=[{"need_code": True}, None, {"name": "Анна"}],
                        submissions=["accepted"])
        s = self.sign(src)
        s.begin()
        self.drv.tick()
        self.assertEqual(s.state["kind"], "code")
        self.assertEqual(self.drv.timers, [], "no redraw while the person types")
        s.submit(" 123 456 ")
        self.assertEqual(src.submitted, ["123456"])
        self.assertEqual(s.state["kind"], "confirming")
        self.drv.tick()
        self.assertEqual(s.state, {"kind": "in", "name": "Анна"})
        self.assertEqual(src.codes, 1)

    def test_bad_code_or_network_error_can_be_retried_without_a_new_qr(self):
        src = OTPSource(polls=[{"need_code": True}, {"name": "Анна"}],
                        submissions=["incorrect", OSError("down"), "accepted"])
        s = self.sign(src)
        s.begin()
        self.drv.tick()
        s.submit(" ")
        self.assertEqual(src.submitted, [])
        s.submit("111111")
        self.assertEqual(s.state["kind"], "code")
        self.assertIn("Неверный", s.state["note"])
        s.submit("222222")
        self.assertEqual(s.state["kind"], "code")
        self.assertIn("связь", s.state["note"])
        s.submit("333333")
        self.assertEqual(s.state["kind"], "in")
        self.assertEqual(src.codes, 1)

    def test_expired_code_submission_offers_a_new_qr(self):
        src = OTPSource(polls=[{"need_code": True}], submissions=["expired"])
        s = self.sign(src)
        s.begin()
        self.drv.tick()
        s.submit("123456")
        self.assertEqual(s.state["kind"], "waiting")
        self.assertEqual(src.codes, 2)

    def test_manual_code_entry_cancels_the_old_poll_timer(self):
        src = OTPSource(polls=[{"name": "Анна"}], submissions=["accepted"])
        s = self.sign(src)
        s.begin()
        s.enter_code()
        self.drv.tick()
        self.assertEqual(s.state["kind"], "code")
        self.assertEqual(len(src.polls), 1)
        s.submit("123456")
        self.assertEqual(s.state["kind"], "in")

    def test_cancel_drops_a_late_submission_callback(self):
        src = OTPSource(polls=[{"need_code": True}])
        s = self.sign(src)
        s.begin()
        self.drv.tick()
        pending = []
        s.run = lambda func, *args, done: pending.append(done)
        s.submit("123456")
        self.assertEqual(s.state["kind"], "submitting")
        s.cancel()
        pending[0]("accepted", None)
        self.assertEqual(s.state["kind"], "out")
        self.assertEqual(self.drv.timers, [])

    def test_a_locked_keyring_is_said_and_asked_again(self):
        src = LockedSource()
        s = self.sign(src)
        self.assertEqual(s.state["kind"], "locked")
        self.assertIn("Связка ключей закрыта", s.state["note"])
        s.begin()  # refused once more
        self.assertEqual(self.seen, ["checking", "locked"])
        src.opens = True
        s.begin()
        self.assertEqual(s.state, {"kind": "in", "name": "Анна"})
        self.assertEqual(src.codes, 0, "no new sign-in for a kept one")

    def test_refused_while_keeping_a_new_sign_in(self):
        src = LockedSource(polls=[OSError("locked")])
        src.locked = False
        s = self.sign(src)
        s.begin()

        def refused():
            src.locked = True
            raise OSError("locked")

        src.login_poll = refused
        self.drv.tick()
        self.assertEqual(s.state["kind"], "locked", "not «the code expired»")
        self.assertEqual(src.codes, 1)

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
