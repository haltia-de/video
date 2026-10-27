# SPDX-License-Identifier: GPL-2.0
"""Real GTK regressions. Run with HALTIA_GTK_TESTS=1 under a display;
ordinary source/Library tests need no window or desktop session.
"""
import os
from pathlib import Path
import runpy
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from library import Library


@unittest.skipUnless(os.environ.get("HALTIA_GTK_TESTS"), "requires an explicit GTK test session")
class PlaylistUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("HALTIA_TEST_APP") or str(Path(__file__).resolve().parents[1] / "haltia-video")
        cls.app = runpy.run_path(path, run_name="playlist_ui_test")
        cls.Gtk = cls.app["Gtk"]
        if not cls.Gtk.init_check()[0]:
            raise unittest.SkipTest("no GTK display")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chan = {"source": "vk", "id": "-7", "title": "Channel"}
        self.playlist = dict(self.chan, owner_id="-7", id="7", title="Playlist")
        self.videos = [{"source": "vk", "id": "-7_%d" % i, "title": "Урок %d" % i,
                        "channel": "Channel", "thumb": "", "duration": 600} for i in range(65)]
        videos = self.videos
        src = SimpleNamespace(id="vk", label="VK", home=lambda: None,
                              playlist_videos=lambda _p, offset=0: {
                                  "videos": videos[offset:offset + 30],
                                  "next": offset + 30 if offset + 30 < len(videos) else None})
        Video = self.app["Video"]
        namespace = Video.__init__.__globals__
        with patch.object(self.app["sources"], "load", return_value={"vk": src}), \
                patch.dict(namespace, {"Library": lambda: Library(self.tmp.name, self.tmp.name),
                                       "FEED_CACHE": self.tmp.name + "/feed.json",
                                       "HOME_CACHE": self.tmp.name + "/home.json"}):
            self.view = Video()
        view = self.view
        view.set_application_id("io.github.haltia_de.Video.Test" + uuid.uuid4().hex)
        view.set_flags(self.app["Gio"].ApplicationFlags.NON_UNIQUE)
        view.register(None)
        self.jobs = []
        view.work = lambda fn, *args, done: self.jobs.append((fn, args, done))
        view.reset_channel_data()
        view.thumbs.get = lambda *args: None
        view.do_activate()
        view.player = SimpleNamespace(state=None, stop=lambda: None)

    def tearDown(self):
        self.view.bookmark_sync.close()
        self.view.pool.shutdown(wait=True)
        self.view.win.destroy()
        self.pump()

    def pump(self):
        for _ in range(100):
            if self.jobs:
                fn, args, done = self.jobs.pop(0)
                try:
                    value, error = fn(*args), None
                except Exception as exc:
                    value, error = None, exc
                if error:
                    raise error
                done(value, error)
            if self.Gtk.events_pending():
                self.Gtk.main_iteration()
            elif not self.jobs:
                break

    def widgets(self, widget=None):
        widget = widget or self.view.win
        yield widget
        if isinstance(widget, self.Gtk.Container):
            for child in widget.get_children():
                yield from self.widgets(child)

    def button(self, title):
        return next(w for w in self.widgets() if isinstance(w, self.Gtk.Button) and w.get_label() == title)

    def test_editor_moves_across_pages_and_task_toggle_does_not_start_player(self):
        self.open(order="lesson")
        self.view.show_playlist_editor(self.playlist, self.chan, "custom", return_order="lesson")
        self.pump()
        self.assertEqual(self.view.sub["kind"], "playlist_editor")
        token = self.view.sub["token"]
        first = self.view.core.query("playlist_editor", {"entity": self.playlist, "order": "custom", "token": token})["items"][0]
        self.view.move_playlist_entry(first["entry_id"], 60)
        self.pump()
        self.assertEqual(self.view.sub["offset"], 60)
        self.assertTrue(any(getattr(w, "_entry_id", None) == first["entry_id"] for w in self.widgets()))
        self.button("Сохранить").clicked()
        self.pump()
        self.assertEqual(self.view.sub["order"], "custom")
        self.assertNotEqual(self.view.sub["result"]["items"][0]["id"], first["id"])
        k = (self.playlist["source"], self.playlist["owner_id"], self.playlist["id"])
        self.view.playlist_order[k] = "lesson"
        self.view.show_playlist(self.playlist, self.chan)
        self.pump()
        check = next(w for w in self.widgets() if isinstance(w, self.Gtk.CheckButton) and w.get_label() == "Выполнено")
        check.set_active(True)
        self.pump()
        self.assertEqual(self.view.sub["result"]["tasks"]["done"], 1)
        self.assertIsNone(self.view.player.state)

    def open(self, video=None, order="vk"):
        if video:
            self.view.library.played(video)
            self.view.library.note_position(video, 123, 600)
            self.view.library.note_playlist(self.playlist, video)
        self.view.library.set_playlist_order(self.playlist, order)
        self.view.show_playlist(self.playlist, self.chan)
        self.pump()

    def test_choices_redraw_after_the_combo_box_returns(self):
        # GTK notifies "active" on the combo box after "changed": a page
        # rebuilt inside the handler freed it first (a crash on the P11).
        self.open()
        sorting = next(w for w in self.widgets() if isinstance(w, self.Gtk.ComboBoxText))
        sorting.set_active_id("reverse")
        self.assertIsNotNone(sorting.get_parent())
        self.pump()
        self.assertEqual(self.view.sub["order"], "reverse")
        self.assertIsNone(sorting.get_parent())

    def action(self, title):
        return dict(self.view.playlist_actions(self.view.sub))[title]

    def test_one_row_over_the_cards_and_the_rest_in_the_menu(self):
        self.open()
        with self.assertRaises(StopIteration):
            self.button("Сбросить прогресс")
        self.assertNotIn("Сбросить прогресс", dict(self.view.playlist_actions(self.view.sub)))
        self.open(self.videos[58])
        resume = next(w for w in self.widgets() if getattr(w, "_caption", None) == "Продолжить")
        sorting = next(w for w in self.widgets() if isinstance(w, self.Gtk.ComboBoxText))
        self.assertIs(resume.get_parent(), sorting.get_parent())
        with self.assertRaises(StopIteration):
            self.button("Сбросить прогресс")
        titles = [t for t, _fn in self.view.playlist_actions(self.view.sub)]
        self.assertEqual(titles[:2], ["Изменить порядок", "Показать в списке"])
        self.assertIn("Сбросить прогресс", titles)

    def assert_locates(self, order):
        self.open(self.videos[58], order)
        self.action("Показать в списке")()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.pump()
            focus = self.view.win.get_focus()
            if getattr(focus, "_video_key", None) == ("vk", self.videos[58]["id"]):
                break
            time.sleep(.01)
        focus = self.view.win.get_focus()
        self.assertEqual(getattr(focus, "_video_key", None), ("vk", self.videos[58]["id"]))
        adjustment = self.view.scroll.get_vadjustment()
        _x, top = focus.translate_coordinates(self.view.body, 0, 0)
        self.assertGreater(adjustment.get_value(), 0)
        self.assertGreaterEqual(top, adjustment.get_value() - 1)
        self.assertLessEqual(top + focus.get_allocated_height(),
                             adjustment.get_value() + adjustment.get_page_size() + 1)

    def test_locate_focuses_and_reveals_lazily_built_card(self):
        self.assert_locates("vk")

    def test_locate_focuses_card_in_lesson_order(self):
        self.assert_locates("lesson")
