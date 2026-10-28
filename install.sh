#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Install haltia-video (Видео): sh install.sh [ROOT] [PREFIX]
# ROOT empty or / for this system; PREFIX /usr/local unless given (a
# package passes /usr). It needs Python 3 with PyGObject (GTK 3), haltia-kit
# (github.com/haltia-de/kit), yt-dlp with a JavaScript runtime
# (QuickJS), and haltia-vplay or mpv to play. haltia-vplay is installed when
# it was built (vplay/build.sh, or the package's build()). The .desktop file
# shows it in any desktop's launcher; the Haltia tablet shell puts it on its
# home screen (X-Haltia-Home).
set -eu
root=${1:-}
root=${root%/}
prefix=${2:-/usr/local}
here=$(cd "$(dirname "$0")" && pwd)
share=$root$prefix/share
bin=$root$prefix/bin
id=io.github.haltia_de.Video
old_id=io.github.trudenboy.Video
lib=$root$prefix/lib/haltia-video
rm -rf "$lib" "$root$prefix/lib/b8k-video"
for f in library.py kept.py search.py signin.py player.py pictures.py playlist.py renamed.py bookmarks.py bookmark_sync.py; do
	install -D -m 644 "$here/$f" "$lib/$f"
done
find "$here/haltia_video" -type f \( -name '*.py' -o -name '*.js' -o -name '*.css' -o -name '*.html' \) | while IFS= read -r f; do
	install -D -m 644 "$f" "$lib/${f#"$here/"}"
done
install -D -m 644 "$here/haltia-queue.lua" "$lib/haltia-queue.lua"
# The program runs from a module, which Python loads compiled (its
# __pycache__, made by the package), while the script Python is started
# with is compiled anew at every start. PREFIX/bin/haltia-video is a stub that
# runs the module as __main__ with sys.argv as given: the shell knows the
# program by its command line, python3 PREFIX/bin/haltia-video.
install -D -m 644 "$here/haltia-video" "$root$prefix/lib/haltia-video/haltia_video_main.py"
mkdir -p "$root$prefix/bin"
cat > "$root$prefix/bin/haltia-video" <<EOF
#!/usr/bin/env python3
# The code is $prefix/lib/haltia-video/haltia_video_main.py, run from there so that Python loads it compiled.
import runpy
import sys
sys.path.insert(0, "$prefix/lib/haltia-video")
runpy.run_module("haltia_video_main", run_name="__main__", alter_sys=False)
EOF
chmod 755 "$root$prefix/bin/haltia-video"
for f in "$here"/sources/*.py; do
	install -D -m 644 "$f" "$lib/sources/${f##*/}"
done
if [ -x "$here/vplay/haltia-vplay" ]; then
	install -D -m 755 "$here/vplay/haltia-vplay" "$bin/haltia-vplay"
	ln -sf haltia-vplay "$bin/b8k-vplay"
else
	echo "install.sh: vplay/haltia-vplay not built: Видео will play with mpv" >&2
fi
# The names from before ADR 0004, until 3.0: the commands (Файлы and
# scripts call b8k-vplay) and the old .desktop file, hidden.
ln -sf haltia-video "$bin/b8k-video"
install -D -m 644 "$here/data/$id.desktop" "$share/applications/$id.desktop"
install -D -m 644 "$here/data/$old_id.desktop" "$share/applications/$old_id.desktop"
install -D -m 644 "$here/data/$id.svg" "$share/icons/hicolor/scalable/apps/$id.svg"
install -D -m 644 "$here/data/$id.metainfo.xml" "$share/metainfo/$id.metainfo.xml"
rm -f "$share/icons/hicolor/scalable/apps/$old_id.svg" "$share/metainfo/$old_id.metainfo.xml"
# The shell's old entry for this program, from before the .desktop file.
rm -f "$root/usr/local/share/b8k/apps.d/video.conf" "$root/usr/local/share/b8k/video.svg"
