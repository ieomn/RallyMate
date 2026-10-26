#!/usr/bin/env bash
set -euo pipefail
umask 077

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$DEPLOY_DIR/../.." && pwd)"
ENV_FILE="$DEPLOY_DIR/runtime.env"
[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE" >&2; exit 1; }
# runtime.env is a trusted, server-only shell configuration file.
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
export PATH="$PROJECT_ROOT/.venv/bin:$PROJECT_ROOT/tools/node/bin:$PROJECT_ROOT/tools/bin:$PATH"
# Keep every writable runtime/cache path on the AutoDL data disk.
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$PROJECT_ROOT/runtime/cache}"
export HF_HOME="${HF_HOME:-$XDG_CACHE_HOME/huggingface}"
export TORCH_HOME="${TORCH_HOME:-$XDG_CACHE_HOME/torch}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$XDG_CACHE_HOME/matplotlib}"
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-$XDG_CACHE_HOME/ultralytics}"
export TMPDIR="${TMPDIR:-$PROJECT_ROOT/runtime/tmp}"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
if [[ -d "$PROJECT_ROOT/tools/egl/usr/lib/x86_64-linux-gnu" ]]; then
    export LD_LIBRARY_PATH="$PROJECT_ROOT/tools/egl/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
mkdir -p -- "$XDG_CACHE_HOME" "$HF_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" "$YOLO_CONFIG_DIR" "$TMPDIR"
export RALLYMATE_ENVIRONMENT="${RALLYMATE_ENVIRONMENT:-development}"
export RALLYMATE_MODEL_LICENSE_ACK="${RALLYMATE_MODEL_LICENSE_ACK:-development}"
export RALLYMATE_DEVICE="${RALLYMATE_DEVICE:-0}"
export RALLYMATE_DATA_ROOT="${RALLYMATE_DATA_ROOT:-$PROJECT_ROOT/service_data}"
export RALLYMATE_API_ORIGIN=http://127.0.0.1:8001
export RALLYMATE_LOCAL_TUNNEL=1
export RALLYMATE_CPU_THREADS="${RALLYMATE_CPU_THREADS:-4}"
export OMP_NUM_THREADS="$RALLYMATE_CPU_THREADS"
export OMP_THREAD_LIMIT="$RALLYMATE_CPU_THREADS"
export MKL_NUM_THREADS="$RALLYMATE_CPU_THREADS"
export OPENBLAS_NUM_THREADS="$RALLYMATE_CPU_THREADS"
export NUMEXPR_NUM_THREADS="$RALLYMATE_CPU_THREADS"
export OPENCV_FOR_THREADS_NUM="$RALLYMATE_CPU_THREADS"
api_key="${RALLYMATE_API_KEY:-}"
if [[ ${#api_key} -lt 32 || "$api_key" == REPLACE_* ]]; then
    echo "Set a random RALLYMATE_API_KEY of at least 32 characters in runtime.env." >&2
    exit 1
fi

cd -- "$PROJECT_ROOT"
case "${1:-}" in
    api)
        exec "$PROJECT_ROOT/.venv/bin/python" -c \
            'from rallymate_service.cli import api_main; api_main()' \
            --host 127.0.0.1 --port 8001
        ;;
    worker)
        exec "$PROJECT_ROOT/.venv/bin/python" -c \
            'from rallymate_service.cli import worker_main; worker_main()'
        ;;
    monitor)
        exec "$PROJECT_ROOT/.venv/bin/python" "$DEPLOY_DIR/monitor.py"
        ;;
    web)
        cd -- "$PROJECT_ROOT/scoring-demo-web"
        exec "$PROJECT_ROOT/tools/node/bin/node" node_modules/vinext/dist/cli.js \
            start --hostname 127.0.0.1 --port 8000
        ;;
    cloudflared)
        # A marker lets manage.sh ignore a hostname from an earlier tunnel run.
        printf 'RALLYMATE_TUNNEL_STARTED %s\n' "$(date -u +%FT%TZ)"
        exec "$PROJECT_ROOT/tools/cloudflared" tunnel \
            --no-autoupdate --protocol http2 --url http://127.0.0.1:8000
        ;;
    *) echo "Usage: $0 {api|worker|web|cloudflared|monitor}" >&2; exit 2 ;;
esac
