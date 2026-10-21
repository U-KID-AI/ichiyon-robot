#!/bin/sh
set -eu
# Official 9.0.2 release; PGP verified with the FFmpeg release signing key
# FCF986EA15E6E293A5644F10B4322F04D67658D8 before pinning this SHA256.
version=9.0.2
sha256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
cd "$work"
curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
    "https://ffmpeg.org/releases/ffmpeg-$version.tar.xz" -o source.tar.xz
printf '%s  source.tar.xz\n' "$sha256" | sha256sum -c -
tar -xf source.tar.xz
cd "ffmpeg-$version"
./configure --prefix=/opt/ffmpeg --disable-doc --disable-debug --disable-ffplay \
    --enable-gpl --enable-gnutls --enable-libmp3lame --enable-libopus \
    --enable-libvorbis --enable-libx264 --enable-libvpx --enable-libxml2 \
    --enable-libass --enable-libfreetype --enable-libfontconfig --enable-libdav1d
# Bound compiler memory on the production host. No native-CPU tuning so the
# result is portable across machines of the selected Docker architecture.
make -j2
make install
