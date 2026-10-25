#!/usr/bin/env bash
# Minimal GLVND loader for SAM diagnostic rendering, without system installation.
set -Eeuo pipefail
ROOT="${RALLYMATE_ROOT:-/root/autodl-tmp/rallymate}"
case "$(realpath -m "$ROOT")" in /root/autodl-tmp/*) ;; *) echo 'Project must be under /root/autodl-tmp' >&2; exit 2;; esac
cd "$ROOT"
mkdir -p tools/downloads tools/egl
# Ubuntu Jammy libegl1; SHA256 from https://packages.ubuntu.com/jammy/amd64/libegl1/download
curl --fail --location --retry 3 --connect-timeout 20 --max-time 180 \
  --output tools/downloads/libegl1_1.4.0-1_amd64.deb \
  https://archive.ubuntu.com/ubuntu/pool/main/libg/libglvnd/libegl1_1.4.0-1_amd64.deb
printf '%s  %s\n' 4a35c0e925e15a076e7ce11d7c76f8ecb16615fc347c2c09afa4c205a7ca8ec4 tools/downloads/libegl1_1.4.0-1_amd64.deb | sha256sum --check
dpkg-deb -x tools/downloads/libegl1_1.4.0-1_amd64.deb tools/egl
if [[ ! -e tools/egl/usr/lib/x86_64-linux-gnu/libEGL.so ]]; then
  ln -s libEGL.so.1 tools/egl/usr/lib/x86_64-linux-gnu/libEGL.so
fi
printf 'Use LD_LIBRARY_PATH=%s/tools/egl/usr/lib/x86_64-linux-gnu[:existing value]\n' "$ROOT"
