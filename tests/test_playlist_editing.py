# SPDX-License-Identifier: GPL-2.0
import tempfile
from types import SimpleNamespace
import unittest

from library import Library
from haltia_video.core.application import Application
from haltia_video.core.bookmarks import key

PLAYLIST = {"source": "vk", "owner_id": "-7", "id": "7", "title": "Курс"}


class EditingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Library(self.tmp.name, self.tmp.name)
        a = {"source": "vk", "id": "a", "title": "Урок 1 А", "duration": 600, "thumb": ""}
        self.videos = [a, dict(a, id="b", title="Урок 1 Б"), dict(a, id="c", title="Урок 2 В"),
                       a, dict(a, id="d", title="Урок 3 Г")]
        self.jobs = []
        self.runtime = SimpleNamespace(work=lambda fn, *args, done: self.jobs.append((fn, args, done)),
                                       later=lambda *args: None)
        self.source = SimpleNamespace(id="vk", label="VK", playlist_videos=lambda p, offset=0: {
            "videos": self.videos[offset:offset + 30],
            "next": offset + 30 if offset + 30 < len(self.videos) else None})
        self.core = Application({"vk": self.source}, self.library, self.runtime)

    def pump(self):
        while self.jobs:
            fn, args, done = self.jobs.pop(0)
            try:
                result, error = fn(*args), None
            except Exception as exc:
                result, error = None, exc
            done(result, error)

    def view(self, mode="custom", playlist=PLAYLIST):
        p = {"entity": playlist, "order": mode}
        self.core.query("playlist", p)
        self.pump()
        return self.core.query("playlist", p)

    def editor(self, mode="custom", device="gtk"):
        self.view(mode)
        return self.core.query("playlist_editor", {"entity": PLAYLIST, "order": mode}, device)

    def command(self, name, **params):
        return self.core.command(name, dict(entity=PLAYLIST, **params))

    def task(self, video, on=True):
        self.command("playlist_task", video=video, on=on)

    def reset(self, what):
        preview = self.core.query("reset_preview", {"entity": PLAYLIST, "what": what})
        self.core.command("playlist_reset", {"token": preview["token"]})

    def test_draft_cancel_save_restart_and_occurrences(self):
        draft = self.editor()
        ids = [v["entry_id"] for v in draft["items"]]
        self.assertEqual(len(set(ids)), 5)
        self.assertNotIn("entry_id", self.videos[0], "source answers must not be mutated")
        self.command("playlist_move", token=draft["token"], entry_id=ids[3], position=0)
        self.assertEqual([v["entry_id"] for v in self.view()["items"]], ids, "draft is not published")
        self.command("playlist_cancel", token=draft["token"])
        draft = self.editor()
        self.command("playlist_move", token=draft["token"], entry_id=ids[3], position=0)
        self.command("playlist_save", token=draft["token"])
        expected = [ids[3], ids[0], ids[1], ids[2], ids[4]]
        self.assertEqual([v["entry_id"] for v in self.view()["items"]], expected)
        again = Library(self.tmp.name, self.tmp.name)
        self.assertEqual(again.manual_order(PLAYLIST, "custom"), expected)
        self.library.played(self.view()["items"][0])
        self.library.note_playlist(PLAYLIST, self.view()["items"][0])
        self.library.note_position(self.view()["items"][0], 100, 600)
        plan = self.core.playback_plan(playlist=PLAYLIST, order="custom", resume=True)
        self.assertEqual(plan["video"]["entry_id"], ids[3])

    def test_lesson_boundaries_and_cross_page_moves(self):
        draft = self.editor("lesson")
        ids = [v["entry_id"] for v in draft["items"]]
        with self.assertRaises(ValueError):
            self.command("playlist_move", token=draft["token"], entry_id=ids[0], position=3)
        self.command("playlist_move", token=draft["token"], entry_id=ids[0], position=2)
        self.command("playlist_save", token=draft["token"])
        self.assertEqual([v["entry_id"] for v in self.view("lesson")["items"]], ids[1:3] + [ids[0]] + ids[3:])
        self.videos = [dict(self.videos[0], id=str(i), title="Урок 1 %d" % i) for i in range(65)]
        self.core.reset_channel_data()
        draft = self.editor()
        self.command("playlist_move", token=draft["token"], entry_id=draft["items"][0]["entry_id"], position=60)
        result = self.core.query("playlist_editor", {"entity": PLAYLIST, "order": "custom",
                                                    "token": draft["token"], "offset": 60})
        self.assertEqual(result["items"][0]["id"], "0")
        self.command("playlist_save", token=draft["token"])
        self.assertEqual(self.core.query("playlist", {"entity": PLAYLIST, "order": "custom", "offset": 60})["items"][0]["id"], "0")

    def test_device_ownership_conflicts_and_independent_tasks(self):
        first = self.editor()
        second = self.editor(device="tablet")
        with self.assertRaises(ValueError):
            self.core.command("playlist_save", {"entity": PLAYLIST, "token": first["token"]}, "tablet")
        self.task(first["items"][0])
        self.command("playlist_save", token=first["token"])
        with self.assertRaisesRegex(ValueError, "другом устройстве"):
            self.core.command("playlist_save", {"entity": PLAYLIST, "token": second["token"]}, "tablet")
        self.core.command("playlist_cancel", {"token": second["token"]}, "tablet")
        self.assertNotIn(second["token"], self.core.editor.drafts)

    def test_source_updates_append_new_keep_missing_and_detect_old_draft(self):
        draft = self.editor()
        old = draft["items"]
        self.command("playlist_move", token=draft["token"], entry_id=old[-1]["entry_id"], position=0)
        self.command("playlist_save", token=draft["token"])
        stale = self.editor()
        self.videos = [dict(self.videos[0], id="new")] + [v for v in self.videos if v["id"] != "b"]
        self.core.playlist_all.invalidate()
        self.view()
        with self.assertRaises(ValueError):
            self.command("playlist_save", token=stale["token"])
        self.assertEqual([v["id"] for v in self.view()["items"]], ["d", "a", "c", "a", "new"])
        self.videos.append(old[1])
        self.core.playlist_all.invalidate()
        self.view()
        self.assertEqual([v["id"] for v in self.view()["items"]], ["d", "a", "b", "c", "a", "new"])

    def test_completion_is_playlist_scoped_and_does_not_change_playback(self):
        items = self.view("lesson")["items"]
        a = next(v for v in items if v["id"] == "a")
        self.task(a)
        result = self.view("lesson")
        self.assertTrue(all(v["task_done"] for v in result["items"] if v["id"] == "a"))
        self.assertEqual(result["tasks"], {"done": 1, "total": 4})
        self.assertEqual(self.library.history(), [])
        self.assertIsNone(self.library.position(a))
        self.assertFalse(self.library.completed(a))
        self.assertEqual(self.core.playback_plan(playlist=PLAYLIST, order="lesson", resume=True)["video"]["id"], "b")
        self.assertNotIn("task_done", self.view("custom")["items"][0])
        other = dict(PLAYLIST, id="other")
        self.assertEqual(self.view("lesson", other)["tasks"]["done"], 0)
        again = Library(self.tmp.name, self.tmp.name)
        self.assertEqual(again.task_done(PLAYLIST), {key("video", a)})

    def test_task_continuation_wraps_and_all_done_keeps_marks(self):
        items = self.view("lesson")["items"]
        last = items[-1]
        self.library.played(last)
        self.library.note_playlist(PLAYLIST, last)
        self.library.note_position(last, 200, 600)
        self.assertEqual(self.core.playback_plan(playlist=PLAYLIST, order="lesson", resume=True)["video"]["id"], last["id"])
        self.library.note_position(last, 595, 600)
        self.assertTrue(self.library.completed(last))
        self.assertEqual(self.core.playback_plan(playlist=PLAYLIST, order="lesson", resume=True)["start"], 595)
        self.assertIsNone(self.core.playback_plan(video=last, playlist=PLAYLIST, order="lesson")["start"],
                          "ordinary entry transitions must restart watched repeats")
        self.library.note_position(last, 200, 600)
        self.task(last)
        self.assertEqual(self.core.playback_plan(playlist=PLAYLIST, order="lesson", resume=True)["video"]["entry_id"], items[0]["entry_id"])
        for video in items:
            self.task(video)
        self.assertEqual(self.view("lesson")["continuation"]["kind"], "tasks_done")
        plan = self.core.playback_plan(playlist=PLAYLIST, order="lesson", resume=True, start_over=True)
        self.assertTrue(plan["start_over"])
        self.assertEqual(plan["index"], 0)
        self.assertEqual(self.library.resume_at(last), 200)
        self.assertEqual(self.view("lesson")["tasks"]["done"], 4)

    def test_three_resets_are_independent_and_queue_update_does_not_steal_progress(self):
        draft = self.editor()
        video = draft["items"][1]
        session = self.core.sessions.start("tablet", video, PLAYLIST, draft["items"])
        state = self.core.sessions.get(session["id"])
        state["order"] = "custom"
        self.core.sessions.report(session["id"], 150, 600, paused=True)
        self.task(video)
        self.command("playlist_move", token=draft["token"], entry_id=video["entry_id"], position=0)
        self.command("playlist_save", token=draft["token"])
        self.assertEqual(state["queue"][0]["entry_id"], video["entry_id"])
        self.assertEqual((state["pos"], state["generation"], state["paused"]), (150, 0, True))
        self.assertEqual(self.core.sessions.video_owner[key("video", video)], session["id"])
        self.reset("tasks")
        self.assertFalse(self.library.task_done(PLAYLIST))
        self.assertEqual(self.library.resume_at(video), 150)
        self.assertTrue(self.library.manual_order(PLAYLIST, "custom"))
        self.task(video)
        self.reset("order")
        self.assertTrue(self.library.task_done(PLAYLIST))
        self.assertEqual(self.library.resume_at(video), 150)
        self.assertFalse(self.library.manual_order(PLAYLIST, "custom"))
        self.reset("progress")
        self.assertTrue(self.library.task_done(PLAYLIST))
        self.assertIsNone(self.library.resume_at(video))
        self.assertEqual(self.core.sessions.sessions, {})

    def test_missing_last_and_empty_playlist_preserve_cursor(self):
        last = self.view("lesson")["items"][-1]
        self.library.played(last)
        self.library.note_playlist(PLAYLIST, last)
        self.videos = [v for v in self.videos if v["id"] != last["id"]]
        self.core.playlist_all.invalidate()
        result = self.view("lesson")
        self.assertEqual(result["continuation"]["video"]["entry_id"], result["items"][0]["entry_id"])
        self.videos = []
        self.core.playlist_all.invalidate()
        result = self.view("lesson")
        self.assertEqual(result["continuation"]["kind"], "empty")
        self.assertEqual(self.library.playlist_resume(PLAYLIST)["video"]["id"], last["id"])
        with self.assertRaises(ValueError):
            self.core.playback_plan(playlist=PLAYLIST, order="lesson", resume=True)

    def test_drafts_are_bounded_expire_and_are_dropped_on_account_epoch_change(self):
        self.view()
        self.core.editor.clock = lambda: 0
        for i in range(16):
            self.core.query("playlist_editor", {"entity": PLAYLIST, "order": "custom"}, "device%d" % i)
        with self.assertRaises(ValueError):
            self.core.query("playlist_editor", {"entity": PLAYLIST, "order": "custom"}, "extra")
        self.core.editor.clock = lambda: 3601
        self.core.query("playlist_editor", {"entity": PLAYLIST, "order": "custom"}, "extra")
        self.assertEqual(len(self.core.editor.drafts), 1)
        self.core.reset_channel_data()
        self.assertEqual(self.core.editor.drafts, {})
