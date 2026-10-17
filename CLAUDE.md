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
- Exercise a source without the UI: `python3 -c 'import sources; s = sources.load()["youtube"]; print(s.search("test")[:2])'` from the repo root.

## Architecture

- `b8k-video` — the whole app in one file: a `Gtk.Application` (`b8k.Video`) with a search row, three tabs (Подписки = merged feed of followed channels, Каналы, История) and a scrolled body holding a note label plus the current content (a 3-column `FlowBox` of cards, or the channel list). A search or a single channel replaces the page content and shows a back button; `self.channel` doubles as that "sub-page" marker (`{"search": query}` for video search, `{"channels": query, "source": src, "found": [...]}` for channel search), and async callbacks check it to drop stale results.
- `sources/` — pluggable video sources. Any module defining a module-level `SOURCE` object is picked up by `sources.load()`; a source that fails to import is logged and skipped. The interface (`id`, `label`, `search`, `channel_videos`, `search_channels`, `find_channel`, `watch_url`, optional `warm_up` and `owns`) and the video/channel dict shapes are documented in `sources/__init__.py`.
  - `youtube.py`: search via yt-dlp (lazy-imported because import is slow on the tablet), channel videos via the public Atom feed (no duration in that path).
  - `vkvideo.py`: VK API (`api.vk.com`) with the anonymous token vkvideo.ru's own web client gets (`login.vk.com/?act=get_anonym_token`, public client id/secret from its scripts); many methods (`video.search`, `groups.search`) are refused with that token, so search goes through `catalog.getVideoSearchWeb2` (`content_type=author` for channels). Channel ids are VK owner ids as strings (`"-55155418"` = community). Playback is plain `vkvideo.ru/video<owner>_<id>` through yt-dlp.
  - Searches go to the source chosen by the button left of the search field (`config["source"]`); in channel search, text a source `owns()` (a link) goes to that source instead. Videos and channels of all sources mix in the feed, Каналы and История.
- Followed channels in `video.json` carry optional details (avatar, handle, subscribers, description) plus app-written `latest` (newest video time, updated on each feed load) and `updated` (when details were fetched). Channels followed from a card have only an id and title; `refresh_channels` fills them in the background via `find_channel` when Каналы opens, and again after a week.
- Threading: source calls block on network, so they run through `Video.work(func, arg, done)` in a daemon thread, with `done(result, error)` delivered on the GTK main loop via `GLib.idle_add`. Thumbnails are fetched by a 3-worker pool in `Thumbs` and cached by URL SHA-1 in `~/.cache/b8k-video/`. Never touch GTK widgets from worker threads.
- Optional shell integration: `/usr/local/share/b8k/python/b8k_ui` provides haptics (`Buzz`, hooked onto every `Gtk.Button` "pressed"), shared header CSS, and `ask()` confirmation dialogs (blocking — called from a worker thread). Everything must keep working when it is missing.
- State files: followed channels `~/.config/b8k/video.json`, history `~/.local/share/b8k-video/history.json` (capped at 100), both written atomically via `save_json` (write `.new`, then `os.replace`).
- `video.conf` registers the app with the shell's launcher (`b8k-apps`); `app_ids` must include the player's id `b8k-vplay` so the player window is grouped with the app.

## Conventions

- User-facing strings are Russian; code, comments, and commit messages are English (commit subjects are full sentences ending with a period).
- Comments explain *why*, usually a tablet constraint (slow CPU, on-screen keyboard covering the window, touch targets). Keep that density and tone.
- Style follows the existing code: `%`-formatting, no type hints, `# SPDX-License-Identifier: GPL-2.0` header on every file.
- UI layout decisions are driven by the on-screen keyboard: the window must fit the space the keyboard leaves (see the comment on the scrolled body), and no entry should take focus unasked.
