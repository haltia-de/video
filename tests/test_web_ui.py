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
        await self.page.locator("#app").wait_for(state="visible")
        await self.open_playlist(self.page)

    async def open_playlist(self, page):
        await page.get_by_role("button", name="Закладки", exact=True).click()
        await page.get_by_role("heading", name="Курс", exact=True).click()

    async def test_live_queue_task_controls_and_stale_editor(self):
        page = self.page
        await page.get_by_label("Сортировка плейлиста").select_option("custom")
        await page.locator('[data-video="0"] h3').click()
        await page.wait_for_function("document.getElementById('video').currentTime>.5")
        await page.locator("#toggle-play").click()
        before = await page.locator("#video").evaluate("v=>({src:v.src,pos:v.currentTime})")
        await page.get_by_role("button", name="Изменить порядок", exact=True).click()
        row = page.locator("[data-entry]").first
        await row.locator("input").fill("61")
        await row.get_by_role("button", name="На позицию…", exact=True).click()
        await page.get_by_role("heading", name="61. Урок 0", exact=True).wait_for()
        await page.get_by_role("button", name="Сохранить", exact=True).click()
        await page.get_by_label("Сортировка плейлиста").wait_for()
        after = await page.locator("#video").evaluate("v=>({src:v.src,pos:v.currentTime})")
        self.assertEqual(before, after)
        await page.locator("#next").click()
        await page.wait_for_function("document.getElementById('playing-title').textContent==='Урок 61' && document.getElementById('video').currentTime>.5")
        await page.locator("#toggle-play").click()
        await page.get_by_label("Сортировка плейлиста").select_option("lesson")
        await page.locator(".card label input[type=checkbox]").first.check()
        await page.get_by_text("Выполнено 1 из 65", exact=True).wait_for()
        await page.get_by_role("button", name="Сбросить выполнение", exact=True).click()
        await page.get_by_role("button", name="Подтвердить", exact=True).click()
        await page.get_by_text("Выполнено 0 из 65", exact=True).wait_for()
        await page.get_by_role("button", name="Изменить порядок", exact=True).click()
        await page.get_by_role("button", name="Сохранить", exact=True).wait_for()
        other = await self.context.new_page()
        await other.goto(self.url)
        await self.open_playlist(other)
        await other.get_by_role("button", name="Изменить порядок", exact=True).click()
        await other.get_by_role("button", name="Сохранить", exact=True).wait_for()
        await page.get_by_role("button", name="Сохранить", exact=True).click()
        await other.get_by_role("button", name="Сохранить", exact=True).click()
        await other.locator("#note").filter(has_text="Порядок уже изменён").wait_for()
        self.assertLessEqual(await other.locator("[data-entry]").count(), 30)
        self.assertEqual(self.errors, [])
        await page.locator("#close-player").click()
