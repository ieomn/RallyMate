#!/usr/bin/env bash
# Isolated Linux validation environments; does not start or migrate the service.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
uv_tool="${UV:-uv}"
venv_root="${RALLYMATE_LINUX_VENVS:-$HOME/.venvs}"
runtime_root="${RALLYMATE_LINUX_RUNTIME:-$HOME/.local/share/rallymate/runtime}"
if [[ "${1:-}" == "--help" ]]; then
    printf '%s\n' 'Requires Linux, uv, and existing runtime/sam3d/{repo,dinov3,checkpoints}.' \
        'Optional environment: UV, RALLYMATE_LINUX_VENVS, RALLYMATE_LINUX_RUNTIME.' \
        'Installs isolated validation dependencies; never starts the production service.'
    exit 0
fi
[[ "$(uname -s)" == Linux ]] || { printf '%s\n' 'Run inside Linux or WSL2.' >&2; exit 1; }
command -v "$uv_tool" >/dev/null
for name in repo dinov3 checkpoints; do
    [[ -d "$project_root/runtime/sam3d/$name" ]] || { printf 'Missing SAM asset directory: %s\n' "$name" >&2; exit 1; }
done
mkdir -p -- "$venv_root"
if [[ ! -x "$venv_root/rallymate-service/bin/python" ]]; then
    "$uv_tool" venv --python 3.12 "$venv_root/rallymate-service"
fi
if [[ ! -x "$venv_root/rallymate-sam3d/bin/python" ]]; then
    "$uv_tool" venv --python 3.11 "$venv_root/rallymate-sam3d"
fi
"$uv_tool" pip install --python "$venv_root/rallymate-service/bin/python" \
    -r "$project_root/scripts/requirements/linux-service-validation.txt"
"$uv_tool" pip install --python "$venv_root/rallymate-sam3d/bin/python" \
    torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
"$uv_tool" pip install --python "$venv_root/rallymate-sam3d/bin/python" \
    -r "$project_root/scripts/requirements/linux-sam-validation.txt"

# Cache and NPY IPC live on the Linux filesystem; reuse the pinned model assets.
"$venv_root/rallymate-sam3d/bin/python" - "$project_root" "$runtime_root" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).resolve() / 'runtime' / 'sam3d'
runtime = Path(sys.argv[2]).resolve()
runtime.mkdir(parents=True, exist_ok=True)
for name in ('repo', 'dinov3', 'checkpoints'):
    link = runtime / name
    expected = (source / name).resolve(strict=True)
    if not link.exists() and not link.is_symlink():
        link.symlink_to(expected, target_is_directory=True)
    if link.resolve(strict=True) != expected:
        raise RuntimeError(f'Refusing to replace existing runtime asset: {link}')
(runtime / 'cache').mkdir(exist_ok=True)
print(f'Linux runtime: {runtime}')
print('Use RALLYMATE_SAM3D_IPC_TRANSPORT=npy on Linux; GPU tests require an idle service.')
PY
