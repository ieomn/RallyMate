#!/usr/bin/env bash
# Install only this project's environment. No service start, config, or system mutation.
set -Eeuo pipefail
ROOT="${RALLYMATE_ROOT:-/root/autodl-tmp/rallymate}"
BASE_PYTHON="${RALLYMATE_BASE_PYTHON:-/root/miniconda3/bin/python}"
case "$(realpath -m "$ROOT")" in /root/autodl-tmp/*) ;; *) echo 'Project must be under /root/autodl-tmp' >&2; exit 2;; esac
cd "$ROOT"
mkdir -p .venvs cache/pip cache/npm cache/torch cache/huggingface cache/matplotlib config/ultralytics tmp tools/downloads tools/node tools/bin service_data/ops/install
export TMPDIR="$ROOT/tmp"
export XDG_CACHE_HOME="$ROOT/cache"
export XDG_CONFIG_HOME="$ROOT/config"
export YOLO_CONFIG_DIR="$ROOT/config/ultralytics"
export PIP_CACHE_DIR="$ROOT/cache/pip"
export npm_config_cache="$ROOT/cache/npm"
export HF_HOME="$ROOT/cache/huggingface"
export TORCH_HOME="$ROOT/cache/torch"
export MPLCONFIGDIR="$ROOT/cache/matplotlib"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_INDEX_URL="${RALLYMATE_PIP_INDEX_URL:-https://pypi.org/simple}"
export WRANGLER_SEND_METRICS=false
export DO_NOT_TRACK=1
INSTALL_DIR="$ROOT/service_data/ops/install"
printf 'running\n' > "$INSTALL_DIR/bootstrap.exit"
trap 'code=$?; printf "%s\n" "$code" > "$INSTALL_DIR/bootstrap.exit"' EXIT
if [[ ! -e .venv ]]; then
  ENV_DIR="$ROOT/.venvs/sam-$(date -u +%Y%m%dT%H%M%SZ)"
  "$BASE_PYTHON" -m venv --system-site-packages "$ENV_DIR"
  ln -s "$ENV_DIR" .venv
fi
PYTHON="$ROOT/.venv/bin/python"
"$PYTHON" -c 'import torch, torchvision; assert torch.cuda.is_available(); print("Base CUDA:", torch.__version__, torchvision.__version__, torch.version.cuda, torch.cuda.get_device_name(0))'

install_python() {
  "$PYTHON" -m pip install -c deploy/autodl/requirements-constraints.txt numpy Cython 'setuptools<81' wheel
  "$PYTHON" -m pip install --no-build-isolation -c deploy/autodl/requirements-constraints.txt -e '.[service,rtmpose]' scipy xtcocotools
  "$PYTHON" -m pip install -c deploy/autodl/requirements-constraints.txt -r deploy/autodl/requirements-sam-core.in 'av==16.0.1' imageio-ffmpeg
  "$PYTHON" -m pip install --ignore-installed --no-deps -c deploy/autodl/requirements-constraints.txt supervisor
  "$PYTHON" - <<'PY'
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import av, cv2, imageio_ffmpeg, numpy, torch, torchvision
import xtcocotools._mask
assert numpy.__version__ == '1.26.4'
assert torch.cuda.is_available()
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
target = Path('tools/bin/ffmpeg')
if not target.exists():
    target.symlink_to(ffmpeg)
names = ['numpy', 'opencv-python', 'torch', 'torchvision', 'ultralytics', 'mmengine', 'mmcv-lite', 'mmpose', 'mmdet', 'scipy', 'xtcocotools', 'pytorch-lightning', 'pyrender', 'timm', 'huggingface_hub', 'av', 'imageio-ffmpeg', 'supervisor']
versions = {name: metadata.version(name) for name in names}
versions.update(python=__import__('sys').version, cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(0), ffmpeg=ffmpeg)
Path('service_data/ops/install/python-versions.json').write_text(json.dumps(versions, indent=2) + '\n')
print(json.dumps(versions, indent=2))
PY
  "$PYTHON" -m pip freeze --local > "$INSTALL_DIR/python-freeze.txt"
  "$PYTHON" -m pip check > "$INSTALL_DIR/pip-check.txt" 2>&1 || true
  .venv/bin/supervisord --version
  tools/bin/ffmpeg -version | head -1
}

install_tools_and_frontend() {
  curl --fail --location --retry 3 --connect-timeout 20 --max-time 900 --output tools/downloads/node-v22.23.2-linux-x64.tar.xz https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz
  printf '%s  %s\n' d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307 tools/downloads/node-v22.23.2-linux-x64.tar.xz | sha256sum --check
  tar -xJf tools/downloads/node-v22.23.2-linux-x64.tar.xz -C tools/node --strip-components=1
  curl --fail --location --retry 3 --connect-timeout 20 --max-time 900 --output tools/downloads/cloudflared_2026.9.1_amd64.deb https://pkg.cloudflare.com/cloudflared/pool/main/c/cloudflared/cloudflared_2026.9.1_amd64.deb
  printf '%s  %s\n' 3be76adc4185d36a0bfb4c2dd8663292f0ed363797f2180333b513b43c81d419 tools/downloads/cloudflared_2026.9.1_amd64.deb | sha256sum --check
  dpkg-deb -x tools/downloads/cloudflared_2026.9.1_amd64.deb tools/downloads/cloudflared-package
  install -m 755 tools/downloads/cloudflared-package/usr/bin/cloudflared tools/cloudflared
  export PATH="$ROOT/tools/node/bin:$PATH"
  node --version
  tools/cloudflared --version
  cd scoring-demo-web
  npm ci --no-audit --no-fund
  npm run build
}

case "${1:-all}" in
  python) install_python > "$INSTALL_DIR/python-install.log" 2>&1 ;;
  frontend) install_tools_and_frontend > "$INSTALL_DIR/frontend-install.log" 2>&1 ;;
  all)
    install_python > "$INSTALL_DIR/python-install.log" 2>&1 & PY_PID=$!
    install_tools_and_frontend > "$INSTALL_DIR/frontend-install.log" 2>&1 & WEB_PID=$!
    FAIL=0
    wait "$PY_PID" || FAIL=1
    wait "$WEB_PID" || FAIL=1
    exit "$FAIL"
    ;;
  *) echo 'Usage: bootstrap-sam-environment.sh [all|python|frontend]' >&2; exit 2 ;;
esac
