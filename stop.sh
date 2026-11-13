#!/bin/bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

info() {
    printf '\033[1;34m[信息]\033[0m %s\n' "$1"
}

success() {
    printf '\033[1;32m[成功]\033[0m %s\n' "$1"
}

warn() {
    printf '\033[1;33m[提示]\033[0m %s\n' "$1"
}

find_pids_by_pattern() {
    local pattern="$1"
    pgrep -f "$pattern" 2>/dev/null || true
}

find_pids_by_port() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    fi
}

stop_matching_processes() {
    local name="$1"
    local pattern="$2"
    local pids
    pids="$(find_pids_by_pattern "$pattern")"

    if [ -z "$pids" ]; then
        warn "未发现正在运行的${name}。"
        return
    fi

    info "正在停止${name}..."
    kill -TERM $pids 2>/dev/null || true

    local attempt=0
    while [ "$attempt" -lt 5 ]; do
        local remaining
        remaining="$(find_pids_by_pattern "$pattern")"
        if [ -z "$remaining" ]; then
            break
        fi
        sleep 1
        attempt=$((attempt + 1))
    done

    local remaining
    remaining="$(find_pids_by_pattern "$pattern")"
    if [ -n "$remaining" ]; then
        warn "${name}未完全退出，正在强制结束..."
        kill -KILL $remaining 2>/dev/null || true
    fi
}

stop_listening_port() {
    local name="$1"
    local port="$2"
    local pids
    pids="$(find_pids_by_port "$port")"

    if [ -z "$pids" ]; then
        return 1
    fi

    info "正在停止占用端口 ${port} 的${name}..."
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    kill -KILL $pids 2>/dev/null || true
    return 0
}

info "正在停止 NL-RPA 平台服务..."

if ! stop_listening_port "后端服务" "8000"; then
    stop_matching_processes "后端 uvicorn 服务" "uvicorn app\\.main:app.*--port[[:space:]]*8000"
fi

if ! stop_listening_port "前端服务" "5173"; then
    stop_matching_processes "前端 Vite 服务" "$ROOT_DIR/frontend/node_modules/(\\.bin/vite|vite/bin/vite\\.js)"
fi

printf '\n'
success "NL-RPA 平台服务已停止。"
