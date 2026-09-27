#!/usr/bin/env bash
set -euo pipefail
umask 077

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$DEPLOY_DIR/../.." && pwd)"
OPS_DIR="$PROJECT_ROOT/service_data/ops"
CONFIG="$DEPLOY_DIR/supervisord.conf"
SUPERVISORD="$PROJECT_ROOT/.venv/bin/supervisord"
SUPERVISORCTL="$PROJECT_ROOT/.venv/bin/supervisorctl"

control() { "$SUPERVISORCTL" -c "$CONFIG" "$@"; }
is_running() { control pid >/dev/null 2>&1; }

show_url() {
    if ! control status cloudflared 2>/dev/null | grep -q ' RUNNING '; then
        echo "Cloudflare tunnel is not RUNNING." >&2
        return 1
    fi
    local current_url
    current_url="$(awk '
        /RALLYMATE_TUNNEL_STARTED/ { url="" }
        match($0, /https:\/\/[a-z0-9-]+\.trycloudflare\.com/) {
            url=substr($0, RSTART, RLENGTH)
        }
        END { if (url != "") print url }
    ' "$OPS_DIR/cloudflared.log" 2>/dev/null || true)"
    if [[ -n "$current_url" ]]; then
        printf '%s\n' "$current_url"
    else
        echo "Tunnel URL is not available yet. Check: $0 logs cloudflared" >&2
        return 1
    fi
}

start_service() {
    local dependency
    for dependency in "$SUPERVISORD" "$SUPERVISORCTL" \
        "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/tools/node/bin/node" \
        "$PROJECT_ROOT/tools/cloudflared"; do
        [[ -x "$dependency" ]] || { echo "Missing executable: $dependency" >&2; return 1; }
    done
    [[ -f "$DEPLOY_DIR/runtime.env" ]] || {
        echo "Create $DEPLOY_DIR/runtime.env from runtime.env.example first." >&2
        return 1
    }
    mkdir -p -- "$OPS_DIR"
    chmod 700 "$OPS_DIR"
    chmod 600 "$DEPLOY_DIR/runtime.env"
    if is_running; then
        control start all
    else
        "$SUPERVISORD" -c "$CONFIG"
    fi
    local attempt program_states expected_programs
    expected_programs="$(grep -c '^\[program:' "$CONFIG")"
    for attempt in {1..60}; do
        program_states="$(control status 2>&1 || true)"
        if [[ $(printf '%s\n' "$program_states" | grep -c ' RUNNING ' || true) == "$expected_programs" ]]; then
            break
        fi
        if printf '%s\n' "$program_states" | grep -q ' FATAL '; then
            printf '%s\n' "$program_states" >&2
            echo "A service failed to start. Inspect: $0 logs" >&2
            return 1
        fi
        sleep 1
    done
    control status
    show_url || true
}

stop_service() {
    if ! is_running; then
        echo "RallyMate supervisor is already stopped."
        return
    fi
    # Stop waits for child exit before shutting down the private supervisor.
    control stop all
    control shutdown
    local attempt
    for attempt in {1..100}; do
        if ! is_running; then return; fi
        sleep 0.1
    done
    echo "Supervisor is still shutting down; check $OPS_DIR/supervisord.log." >&2
    return 1
}

case "${1:-status}" in
    start) start_service ;;
    stop) stop_service ;;
    restart) stop_service; start_service ;;
    status)
        if ! is_running; then echo "RallyMate supervisor is stopped."; exit 1; fi
        result=0
        control status || result=$?
        show_url || true
        exit "$result"
        ;;
    url) show_url ;;
    health)
        exec "$PROJECT_ROOT/.venv/bin/python" "$DEPLOY_DIR/monitor.py" --snapshot
        ;;
    logs)
        case "${2:-all}" in
            api|worker|web|cloudflared|monitor|supervisord)
                exec tail -n 100 -F -- "$OPS_DIR/$2.log"
                ;;
            all)
                exec tail -n 50 -F -- "$OPS_DIR/api.log" "$OPS_DIR/worker.log" \
                    "$OPS_DIR/web.log" "$OPS_DIR/cloudflared.log" "$OPS_DIR/monitor.log"
                ;;
            *) echo "Log name must be api, worker, web, cloudflared, monitor, supervisord or all." >&2; exit 2 ;;
        esac
        ;;
    *) echo "Usage: $0 {start|stop|restart|status|url|health|logs [api|worker|web|cloudflared|monitor|supervisord|all]}" >&2; exit 2 ;;
esac
