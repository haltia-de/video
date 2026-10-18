#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Install b8k-video (Видео): sh install.sh [ROOT] [PREFIX]
# ROOT empty or / for this system; PREFIX /usr/local unless given (a
# package passes /usr). It needs Python 3 with PyGObject (GTK 3), b8k-compat
# (github.com/trudenboy/b8k-compat), yt-dlp with a JavaScript runtime
# (QuickJS), and b8k-vplay or mpv to play. b8k-vplay is installed when it
# was built (vplay/build.sh, or the package's build()). The .desktop file
# shows it in any desktop's launcher; the b8k tablet shell puts it on its
# home screen (X-B8K-Home).
set -eu
root=${1:-}
root=${root%/}
prefix=${2:-/usr/local}
here=$(cd "$(dirname "$0")" && pwd)
share=$root$prefix/share
id=io.github.trudenboy.Video
install -D -m 755 "$here/b8k-video" "$root$prefix/bin/b8k-video"
lib=$root$prefix/lib/b8k-video
rm -rf "$lib"
for f in library.py kept.py search.py signin.py player.py; do
	install -D -m 644 "$here/$f" "$lib/$f"
done
for f in "$here"/sources/*.py; do
	install -D -m 644 "$f" "$lib/sources/${f##*/}"
done
if [ -x "$here/vplay/b8k-vplay" ]; then
	install -D -m 755 "$here/vplay/b8k-vplay" "$root$prefix/bin/b8k-vplay"
else
	echo "install.sh: vplay/b8k-vplay not built: Видео will play with mpv" >&2
fi
install -D -m 644 "$here/data/$id.desktop" "$share/applications/$id.desktop"
install -D -m 644 "$here/data/$id.svg" "$share/icons/hicolor/scalable/apps/$id.svg"
install -D -m 644 "$here/data/$id.metainfo.xml" "$share/metainfo/$id.metainfo.xml"
# The shell's old entry for this program, from before the .desktop file.
rm -f "$root/usr/local/share/b8k/apps.d/video.conf" "$root/usr/local/share/b8k/video.svg"
