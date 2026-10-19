#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# What the deb brings of its own, at the versions of requirements-yt-dlp.txt
# and requirements.txt (the tablet's, Alpine 3.24; the Flatpak manifest
# names the same files): yt-dlp with its EJS scripts, and QuickJS to run
# them. The program puts vendor/ first on sys.path and vendor/bin first on
# PATH, so yt-dlp is found by it and by mpv, and qjs by yt-dlp.
#
#   sh debian/vendor.sh build DIR          fetch (checked) and build into DIR
#   sh debian/vendor.sh install DIR DEST
set -eu
dir=$2

fetch() {  # url sha256
	file=$dir/src/${1##*/}
	[ -f "$file" ] || curl -fsSL --retry 3 -o "$file" "$1"
	echo "$2  $file" | sha256sum -c --quiet
}

case $1 in
build)
	mkdir -p "$dir/src" "$dir/out/bin"
	fetch https://files.pythonhosted.org/packages/69/b2/8cd1613f56eed7ceb64fbd4df3f1c01246bfb098e6f398228bafda22b80b/yt_dlp-2026.8.19-py3-none-any.whl \
		1d57897e94c6665a0a6f9bc54b34e584284e32c034ffab3a7df25d8f7b24eedf
	fetch https://files.pythonhosted.org/packages/e3/bd/520769863744b669440a924271a6159ddd82ad5ae26b4ac4d4b69e9f8d44/yt_dlp_ejs-0.8.0-py3-none-any.whl \
		79300e5fca7f937a1eeede11f0456862c1b41107ce1d726871e0207424f4bdb4
	# bellard.org is slow at times; Alpine keeps the same file.
	fetch https://distfiles.alpinelinux.org/distfiles/v3.24/quickjs-2025-09-13-2.tar.xz \
		996c6b5018fc955ad4d06426d0e9cb713685a00c825aa5c0418bd53f7df8b0b4
	for whl in "$dir"/src/*.whl; do
		python3 -m zipfile -e "$whl" "$dir/out"
	done
	tar -xJf "$dir/src/quickjs-2025-09-13-2.tar.xz" -C "$dir/src"
	make -C "$dir/src/quickjs-2025-09-13" qjs
	cp "$dir/src/quickjs-2025-09-13/qjs" "$dir/out/bin/qjs"
	cat > "$dir/out/bin/yt-dlp" <<-'EOF'
	#!/bin/sh
	# The yt-dlp of Видео's vendor/ (the distribution's is too old), for mpv.
	vendor=$(dirname "$(dirname "$(readlink -f "$0")")")
	PYTHONPATH=$vendor${PYTHONPATH:+:$PYTHONPATH} exec python3 -m yt_dlp "$@"
	EOF
	;;
install)
	dest=$3
	mkdir -p "$dest"
	cp -r "$dir"/out/yt_dlp "$dir"/out/yt_dlp_ejs "$dest/"
	install -D -m 755 "$dir/out/bin/qjs" "$dest/bin/qjs"
	install -D -m 755 "$dir/out/bin/yt-dlp" "$dest/bin/yt-dlp"
	find "$dest" -type d -exec chmod 755 {} + -o -type f -name '*.py' -exec chmod 644 {} +
	;;
esac
