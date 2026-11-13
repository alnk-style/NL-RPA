#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PID=""
FRONTEND_PID=""
BACKEND_LOG="$ROOT_DIR/logs/backend.log"
FRONTEND_LOG="$ROOT_DIR/logs/frontend.log"
CLEANUP_DONE=0

info() {
    printf '\033[1;34m[信息]\033[0m %s\n' "$1"
}

success() {
    printf '\033[1;32m[成功]\033[0m %s\n' "$1"
}

warn() {
    printf '\033[1;33m[提示]\033[0m %s\n' "$1"
}

error() {
    printf '\033[1;31m[错误]\033[0m %s\n' "$1" >&2
}

find_python() {
    if [ -n "${PYTHON_BIN:-}" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        printf '%s' "$PYTHON_BIN"
        return
    fi

    if command -v python3 >/dev/null 2>&1; then
        printf '%s' "python3"
        return
    fi

    if command -v python >/dev/null 2>&1; then
        printf '%s' "python"
        return
    fi

    return 1
}

stop_process() {
    local pid="$1"
    local name="$2"

    if ! kill -0 "$pid" 2>/dev/null; then
        return
    fi

    info "正在停止${name}..."
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true

    local attempt=0
    while kill -0 "$pid" 2>/dev/null && [ "$attempt" -lt 5 ]; do
        sleep 1
        attempt=$((attempt + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        pkill -KILL -P "$pid" 2>/dev/null || true
        kill -KILL "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    local exit_code="${1:-$?}"

    if [ "$CLEANUP_DONE" -eq 1 ]; then
        exit "$exit_code"
    fi
    CLEANUP_DONE=1

    trap - INT TERM EXIT
    stop_process "$FRONTEND_PID" "前端服务"
    stop_process "$BACKEND_PID" "后端服务"
    printf '\n'
    success "NL-RPA 平台已停止。"
    exit "$exit_code"
}

trap 'cleanup 130' INT
trap 'cleanup 143' TERM
trap 'cleanup $?' EXIT

info "正在检查 Python 运行环境..."
if ! PYTHON_CMD="$(find_python)"; then
    error "未找到 Python，请先安装 Python 3.11 或更高版本。"
    exit 1
fi
info "使用 Python 命令：$PYTHON_CMD"

info "正在检查 Python 依赖..."
if ! "$PYTHON_CMD" -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, playwright, openai, apscheduler, jose, passlib, multipart, aiofiles, docker, pydantic_settings, aiosmtplib, httpx" >/dev/null 2>&1; then
    warn "检测到 Python 依赖缺失，正在安装 requirements.txt..."
    "$PYTHON_CMD" -m pip install -r requirements.txt
    success "Python 依赖安装完成。"
else
    success "Python 依赖已安装。"
fi

info "正在检查 Playwright Chromium 浏览器..."
if "$PYTHON_CMD" -c '
from pathlib import Path
from playwright.sync_api import sync_playwright

playwright = sync_playwright().start()
try:
    is_installed = Path(playwright.chromium.executable_path).is_file()
finally:
    playwright.stop()

raise SystemExit(0 if is_installed else 1)
' >/dev/null 2>&1; then
    success "Playwright Chromium 浏览器已安装。"
else
    warn "未检测到 Playwright Chromium 浏览器，正在安装..."
    "$PYTHON_CMD" -m playwright install chromium
    success "Playwright Chromium 浏览器安装完成。"
fi

info "正在检查 .env 配置..."
if [ ! -f "$ROOT_DIR/.env" ]; then
    if [ ! -f "$ROOT_DIR/.env.example" ]; then
        error "未找到 .env.example，无法创建 .env 配置文件。"
        exit 1
    fi
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    warn "已从 .env.example 创建 .env，请填写 DEEPSEEK_API_KEY、CREDENTIAL_ENCRYPTION_KEY 等配置。"
else
    success ".env 配置文件已存在。"
fi

info "正在检查前端运行环境和依赖..."
if ! command -v node >/dev/null 2>&1; then
    error "未找到 Node.js，请先安装 Node.js 18 或更高版本。"
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    error "未找到 npm，请先安装 npm。"
    exit 1
fi

if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    warn "未检测到 frontend/node_modules，正在安装前端依赖..."
    (
        cd "$ROOT_DIR/frontend"
        npm install
    )
    success "前端依赖安装完成。"
else
    success "前端依赖已安装。"
fi

mkdir -p "$ROOT_DIR/logs"
: >"$BACKEND_LOG"
: >"$FRONTEND_LOG"

info "正在启动后端服务..."
"$PYTHON_CMD" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

info "正在启动前端服务..."
(
    cd "$ROOT_DIR/frontend"
    exec npm run dev
) >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

sleep 3

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    error "后端服务启动失败，请查看日志：$BACKEND_LOG"
    tail -n 30 "$BACKEND_LOG" >&2 || true
    exit 1
fi

if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    error "前端服务启动失败，请查看日志：$FRONTEND_LOG"
    tail -n 30 "$FRONTEND_LOG" >&2 || true
    exit 1
fi

info "正在打开默认浏览器..."
if command -v open >/dev/null 2>&1; then
    open "http://localhost:5173" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:5173" >/dev/null 2>&1 || true
else
    warn "未找到可用的浏览器打开命令，请手动访问 http://localhost:5173。"
fi

printf '\n'
success "NL-RPA 平台启动成功！"
printf '  %s\n' "前端地址：http://localhost:5173"
printf '  %s\n' "后端地址：http://localhost:8000"
printf '  %s\n' "接口文档：http://localhost:8000/docs"
printf '  %s\n' "后端日志：$BACKEND_LOG"
printf '  %s\n' "前端日志：$FRONTEND_LOG"
printf '  %s\n\n' "按 Ctrl+C 可停止全部服务。"

while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 1
done

error "检测到服务进程已退出，正在清理剩余进程..."
exit 1
