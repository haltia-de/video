# SPDX-License-Identifier: GPL-2.0
"""Opt-in real Chromium regression; no user's browser/profile/accounts.

HALTIA_WEB_UI_TESTS=1 python3 -m unittest tests.test_web_ui
Needs aiohttp, Playwright, Chrome/Chromium and FFmpeg with libx264.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from tests import test_web as fixture_module


@unittest.skipUnless(os.environ.get("HALTIA_WEB_UI_TESTS"), "requires an explicit browser test session")
class WebUITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from playwright.async_api import async_playwright
        self.fixture = fixture_module.WebTest("test_private_api_requires_pairing_and_rejects_other_origins")
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        movie = Path(self.fixture.tmp.name) / "movie.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=black:s=160x90:r=10", "-t", "10", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(movie)], check=True)
        async def media(request):
            return web.FileResponse(movie)
        app = web.Application()
        app.router.add_get("/movie", media)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        movie_url = "http://127.0.0.1:%d/movie" % runner.addresses[0][1]
        videos = [dict(fixture_module.VIDEO, id=str(i), title="Урок %d" % i, thumb="", duration=10) for i in range(65)]
        self.playlist = {"source": "vk", "owner_id": "-7", "id": "7", "title": "Курс"}
        source = self.fixture.source
        source.playlist_videos = lambda p, offset=0: {"videos": videos[offset:offset + 30],
            "next": offset + 30 if offset + 30 < len(videos) else None}
        source.browser_stream = lambda *args: {"tracks": [{"url": movie_url, "container": "mp4"}], "duration": 10}
        self.fixture.core.library.bookmarks.add("playlist", self.playlist)
        self.pw = await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        executable = os.environ.get("HALTIA_BROWSER") or shutil.which("google-chrome") or shutil.which("chromium")
        self.browser = await self.pw.chromium.launch(executable_path=executable, headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(viewport={"width": 800, "height": 1280}, bypass_csp=True)
        self.page = await self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.url = str(self.fixture.client.make_url("/"))
        await self.page.goto(self.url)
        await self.page.locator("#pair-code").fill(self.fixture.pairing.issue()["code"])
        await self.page.get_by_role("button", name="Подключиться", exact=True).click()
        await self.page.locator("#panes").wait_for(state="visible")
        await self.open_playlist(self.page)

    async def open_playlist(self, page):
        await page.locator('#tabs button[data-page="bookmarks"]').click()
        await page.locator(".pane:not([hidden]) .card h3", has_text="Курс").click()
        await page.locator(".pane:not([hidden]) .card").first.wait_for()

    def pane(self, page):
        return page.locator(".pane:not([hidden])")

    async def menu(self, page, title):
        await self.pane(page).get_by_label("Ещё").click()
        await page.locator("#modal").get_by_role("button", name=title, exact=True).click()

    async def test_live_queue_task_controls_and_stale_editor(self):
        page = self.page
        await self.pane(page).get_by_label("Порядок плейлиста").select_option("custom")
        await page.locator('.pane:not([hidden]) [data-video="0"] h3').click()
        await page.wait_for_function("document.getElementById('video').currentTime>.5")
        await page.locator("#toggle-play").click()
        before = await page.locator("#video").evaluate("v=>({src:v.src,pos:v.currentTime})")
        # The editor as the window's: a tap picks, a tap places before; saved at once.
        await self.menu(page, "Изменить порядок")
        rows = page.locator(".pane:not([hidden]) .row[data-entry]")
        await rows.nth(0).locator(".open").click()
        await page.get_by_role("button", name="В начало", exact=True).wait_for()
        await rows.nth(3).locator(".open").click()
        await page.wait_for_function("""() => Array.from(document.querySelectorAll('.pane:not([hidden]) .row .name'))
            .slice(0, 4).map(n => n.textContent).join('|') === 'Урок 1|Урок 2|Урок 0|Урок 3'""")
        await page.get_by_role("button", name="↶ Отменить", exact=True).wait_for()
        after = await page.locator("#video").evaluate("v=>({src:v.src,pos:v.currentTime})")
        self.assertEqual(before, after)
        await page.locator("#back").click()
        await self.pane(page).get_by_label("Порядок плейлиста").wait_for()
        await page.locator("#next").click()
        await page.wait_for_function("document.getElementById('playing-title').textContent==='Урок 3' && document.getElementById('video').currentTime>.5")
        await page.locator("#toggle-play").click()
        # A lesson's mark on the picture's corner, and the line over the cards.
        await self.pane(page).get_by_label("Порядок плейлиста").select_option("lesson")
        await self.pane(page).get_by_label("Отметить выполненным").first.click()
        await page.get_by_text("Выполнено 1 из 65", exact=True).wait_for()
        await self.menu(page, "Сбросить выполнение")
        await page.locator("#modal").get_by_role("button", name="Сбросить выполнение", exact=True).click()
        await page.get_by_text("Выполнено 0 из 65", exact=True).wait_for()
        # Two browsers on one order: the one whose draft is old is told.
        await self.pane(page).get_by_label("Порядок плейлиста").select_option("custom")
        await self.menu(page, "Изменить порядок")
        other = await self.context.new_page()
        await other.goto(self.url)
        await self.open_playlist(other)
        await self.pane(other).get_by_label("Порядок плейлиста").select_option("custom")
        await self.menu(other, "Изменить порядок")
        await other.locator(".pane:not([hidden]) .row[data-entry]").first.wait_for()
        await page.locator(".pane:not([hidden]) .row[data-entry]").nth(0).get_by_role("button", name="Ниже").click()
        await page.get_by_role("button", name="↶ Отменить", exact=True).wait_for()
        # Told as the change arrives, before it moves anything.
        await other.locator(".pane:not([hidden]) .note").filter(has_text="Порядок уже изменён").wait_for()
        self.assertEqual(self.errors, [])
        await page.locator("#close-player").click()

    async def test_unfollow_offers_it_back_and_tabs_are_kept(self):
        page = self.page
        core = self.fixture.core
        for i in range(3):
            core.command("follow", {"entity": {"source": "vk", "id": "-%d" % (i + 1), "title": "Канал %d" % i}})
        core.command("confirm", {"on": False})
        await page.locator("#back").click()  # from the playlist the set-up opened
        await page.locator('#tabs button[data-page="channels"]').click()
        rows = page.locator(".pane:not([hidden]) .card")
        await rows.nth(2).wait_for()
        # Unfollowing is in ⋮, as everything else a channel offers.
        await rows.nth(1).get_by_role("button", name="Действия").click()
        await page.locator("#modal").get_by_role("button", name="Отписаться", exact=True).click()
        await page.locator("#toast").filter(has_text="Вы отписались от «Канал 1»").wait_for()
        await page.locator("#toast-button").click()
        await page.wait_for_function("""() => Array.from(document.querySelectorAll('.pane:not([hidden]) .card h3'))
            .map(n => n.textContent).join('|') === 'Канал 0|Канал 1|Канал 2'""")  # back at its place
        kept = await page.locator(".pane:not([hidden])").evaluate("p => (p.dataset.mark = 'kept')")
        await page.locator('#tabs button[data-page="history"]').click()
        await page.locator('#tabs button[data-page="channels"]').click()
        self.assertEqual(await page.locator(".pane:not([hidden])").get_attribute("data-mark"), kept)
        self.assertEqual(self.errors, [])

    async def test_each_screen_keeps_cards_or_rows(self):
        page = self.page
        pane = self.pane(page)
        await pane.locator(".card").first.wait_for()
        self.assertEqual(await pane.locator(".grid .card").count() > 0, True)  # a playlist: cards first
        await pane.get_by_role("button", name="Списком").click()
        await pane.locator(".rows .rowcard").first.wait_for()
        await page.reload()
        await self.open_playlist(page)
        await self.pane(page).locator(".rows .rowcard").first.wait_for()  # kept in this browser
        await page.locator("#back").click()
        await page.locator('#tabs button[data-page="history"]').click()
        self.assertEqual(await self.pane(page).get_by_role("button", name="Списком").count(), 1)  # its own choice
        self.assertEqual(self.errors, [])

