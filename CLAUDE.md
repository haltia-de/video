# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

«Видео» — a GTK 3 (PyGObject) video app for the Lenovo YOGA Tablet 10 (B8000-H) running the
[lenovo-b8000h-linux](https://github.com/trudenboy/lenovo-b8000h-linux) shell: large touch cards,
search, followed channels, history. Playback is handed off to the shell's `b8k-vplay` (libmpv),
falling back to `mpv` on other machines. Target CPU is a slow ARMv7 tablet with an on-screen keyboard.

## Commands

There is no build step, test suite, or linter config.

- Run locally: `./b8k-video` (needs Python 3 + PyGObject/GTK 3, `yt-dlp` with a JS runtime such as QuickJS, and `mpv` if `b8k-vplay` is absent).
- Install into a root file system: `sh install.sh [ROOT]` (empty or `/` = this system). The tablet shell's image build calls this script, so keep its paths stable:
  `/usr/local/bin/b8k-video`, `/usr/local/lib/b8k-video/sources/`, `/usr/local/share/b8k/video.svg`, `/usr/local/share/b8k/apps.d/video.conf`.
- Exercise a source without the UI: `python3 -c 'import sources; s = sources.load()["vk"]; print(s.search("test")[:2])'` from the repo root (`youtube` needs `yt_dlp` importable).
- Try the UI against a throwaway profile: `HOME=/some/dir ./b8k-video` (config, history, cache and accounts all live under `$HOME`).

## Architecture

- `b8k-video` — the whole app in one file: a `Gtk.Application` (`b8k.Video`) with a search row (scope button, entry), four tabs (Главная = default, Подписки, Каналы, История), a gear for Настройки, and a scrolled body holding a note label plus the current content. Search results, channel search, a single channel and Настройки are "sub-pages" over the tabs: `self.sub` is a dict with `kind` (`search`, `channels`, `channel`, `settings`), the back button returns to `self.page`, and async callbacks check `self.sub is sub` / `on_sub()` to drop stale results.
- `sources/` — pluggable video sources. Any module defining a module-level `SOURCE` object is picked up by `sources.load()`; a source that fails to import is logged and skipped. The full interface (required search/channel methods, optional `warm_up`, `owns`, `home`, `ytdl_options`, and the account methods for `login = "code"` or `"cookies"`) and the video/channel dict shapes are documented in `sources/__init__.py`, which also holds `CONFIG_DIR`/`CACHE_DIR` and the shared `load_json`/`save_json` (atomic, optional 0600).
  - `net.py` is not a source: HTTP with one kept-alive connection per host and thread (TLS handshakes are expensive on the tablet's CPU) plus gzip. Use it rather than `urllib` for anything fetched repeatedly.
  - `youtube.py`: search via yt-dlp (lazy-imported because import is slow on the tablet), channel videos via the public Atom feed (no duration in that path). YouTube has no guest trends and no program sign-in any more: login is a cookies file at `~/.config/b8k/youtube-cookies.txt`, checked via `feed/channels`; when present it is passed to every yt-dlp call, `home()` returns `:ytrec` recommendations, and `ytdl_options()` hands the cookies to the player.
  - `vkvideo.py`: VK API (`api.vk.com`) with the anonymous token vkvideo.ru's own web client gets (`login.vk.com/?act=get_anonym_token`, public client id/secret from its scripts; cached in `~/.cache/b8k-video/vk-anonym.json`); many methods (`video.search`, `groups.search`) are refused with that token, so search goes through `catalog.getVideoSearchWeb2` (`content_type=author` for channels) and Главная through `catalog.getVideo` → `catalog.getSection` (trends). Sign-in is the TV-style code flow `auth.getAuthCode` → poll `auth.checkAuthCode` (status 0/1 waiting, others end it; a code lives ~3 min and the app requests a new one); the person's token is kept in `~/.config/b8k/video-vk.json` and replaces the anonymous one. The success branch of the poll is written to the usual response shape but has not been seen live yet. Channel ids are VK owner ids as strings (`"-55155418"` = community). Playback is plain `vkvideo.ru/video<owner>_<id>` through yt-dlp.
  - Searches go to every enabled source in parallel (`config["scope"]` = `"all"` or one source id), shown as each answers. `config["sort"]` = `"date"` (default) merges all sources' results newest first; `"relevance"` shows them per source, YouTube's section first. Sorting is done on the found (relevance) results: asking the services for date order is useless (YouTube ignores `sp=CAI=`, VK `sort=0` returns the newest of anything, matching or not). YouTube dates in result lists come from yt-dlp's `youtubetab:approximate_date` (day precision, from "3 days ago"); in channel search, text a source `owns()` (a link) goes to that source only. `config["disabled"]` lists sources switched off in Настройки; their videos are filtered out of the feed and Главная at display time.
- Followed channels in `video.json` carry optional details (avatar, handle, subscribers, description) plus app-written `latest` (newest video time, updated on each feed load) and `updated` (when details were fetched). Channels followed from a card have only an id and title; `refresh_channels` fills them in the background via `find_channel` when Каналы opens, and again after a week.
- Caching (the tablet is slow, so most of the app is built around not waiting):
  - The feed and each source's Главная are kept in `~/.cache/b8k-video/feed.json` / `home.json` and shown at once on start; they are refetched in the background when older than 30 / 60 minutes, then the tab is redrawn with `redraw()` (keeps the scroll position).
  - The feed fetches all channels in parallel; channel detail refreshes too.
  - Search results (10 min) and a channel's videos (15 min) are kept in memory.
  - `Thumbs`: 4 workers fetch and decode JPEGs off the GTK thread, a disk cache by URL SHA-1 (pruned at start to 30 days / 150 MB), an in-memory LRU of decoded pixbufs, one in-flight fetch per URL, and fetches for cards already destroyed are dropped (tracked by each image's `destroy` signal, since cards are built before `set_content` swaps the page).
  - `grid()` builds the first 9 cards at once and the rest in low-priority idle batches.
- Threading: source calls block on network, so they run through `Video.work(func, *args, done=...)` on a 6-worker pool, with `done(result, error)` delivered on the GTK main loop via `GLib.idle_add`. Never touch GTK widgets from worker threads.
- Optional shell integration: `/usr/local/share/b8k/python/b8k_ui` provides haptics (`Buzz`, hooked onto every `Gtk.Button` "pressed"), shared header CSS, and `ask()` confirmation dialogs (blocking — called from a worker thread). Everything must keep working when it is missing.
- State files: followed channels and settings `~/.config/b8k/video.json`, history `~/.local/share/b8k-video/history.json` (capped at 100), accounts `~/.config/b8k/video-vk.json`, `video-youtube.json`, `youtube-cookies.txt`.
- The player: `b8k-vplay --title T URL [option=value ...]` (source in the shell repo, `linux-port/tools-arm/b8k-vplay.c`); extra mpv options after the URL override its defaults, so `ytdl-raw-options` passed for cookies must repeat its `js-runtimes=quickjs`.
- `video.conf` registers the app with the shell's launcher (`b8k-apps`); `app_ids` must include the player's id `b8k-vplay` so the player window is grouped with the app.

## Conventions

- User-facing strings are Russian; code, comments, and commit messages are English (commit subjects are full sentences ending with a period).
- Comments explain *why*, usually a tablet constraint (slow CPU, on-screen keyboard covering the window, touch targets). Keep that density and tone.
- Style follows the existing code: `%`-formatting, no type hints, `# SPDX-License-Identifier: GPL-2.0` header on every file.
- UI layout decisions are driven by the on-screen keyboard: the window must fit the space the keyboard leaves (see the comment on the scrolled body), and no entry should take focus unasked.
