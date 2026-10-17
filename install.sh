#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Install b8k-video into a root file system: sh install.sh [ROOT]
# (ROOT empty or / for this system). The tablet shell's build runs it for
# its image. It plays through the shell's b8k-vplay (or mpv elsewhere) and
# needs yt-dlp with a JavaScript runtime (quickjs) for YouTube.
set -eu
root=${1:-}
root=${root%/}
here=$(cd "$(dirname "$0")" && pwd)
install -D -m 755 "$here/b8k-video" "$root/usr/local/bin/b8k-video"
install -D -m 644 "$here/library.py" "$root/usr/local/lib/b8k-video/library.py"
for f in "$here"/sources/*.py; do
	install -D -m 644 "$f" "$root/usr/local/lib/b8k-video/sources/${f##*/}"
done
install -D -m 644 "$here/video.svg" "$root/usr/local/share/b8k/video.svg"
install -D -m 644 "$here/video.conf" "$root/usr/local/share/b8k/apps.d/video.conf"
