@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title NL-RPA 一键启动

echo [信息] 正在检查 Python 运行环境...
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11 或更高版本。
    pause
    exit /b 1
)
set "PYTHON_CMD=python"
echo [成功] Python 命令可用。

echo [信息] 正在检查 Python 依赖...
%PYTHON_CMD% -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, playwright, openai, apscheduler, jose, passlib, multipart, aiofiles, docker, pydantic_settings, aiosmtplib, httpx" >nul 2>&1
if errorlevel 1 (
    echo [提示] 检测到 Python 依赖缺失，正在安装 requirements.txt...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] Python 依赖安装失败，请检查网络或 pip 配置。
        pause
        exit /b 1
    )
    echo [成功] Python 依赖安装完成。
) else (
    echo [成功] Python 依赖已安装。
)

echo [信息] 正在检查 Playwright Chromium 浏览器...
%PYTHON_CMD% -c "from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); ok=Path(p.chromium.executable_path).is_file(); p.stop(); raise SystemExit(0 if ok else 1)" >nul 2>&1
if errorlevel 1 (
    echo [提示] 未检测到 Playwright Chromium 浏览器，正在安装...
    %PYTHON_CMD% -m playwright install chromium
    if errorlevel 1 (
        echo [错误] Playwright Chromium 浏览器安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
    echo [成功] Playwright Chromium 浏览器安装完成。
) else (
    echo [成功] Playwright Chromium 浏览器已安装。
)

echo [信息] 正在检查 .env 配置...
if not exist ".env" (
    if not exist ".env.example" (
        echo [错误] 未找到 .env.example，无法创建 .env 配置文件。
        pause
        exit /b 1
    )
    copy /Y ".env.example" ".env" >nul
    echo [提示] 已从 .env.example 创建 .env，请填写 DEEPSEEK_API_KEY、CREDENTIAL_ENCRYPTION_KEY 等配置。
) else (
    echo [成功] .env 配置文件已存在。
)

echo [信息] 正在检查前端运行环境和依赖...
where node >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 18 或更高版本。
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 npm，请先安装 npm。
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [提示] 未检测到 frontend\node_modules，正在安装前端依赖...
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        echo [错误] 前端依赖安装失败，请检查网络或 npm 配置。
        pause
        exit /b 1
    )
    popd
    echo [成功] 前端依赖安装完成。
) else (
    echo [成功] 前端依赖已安装。
)

if not exist "logs" mkdir "logs"
break > "logs\backend.log"
break > "logs\frontend.log"

echo [信息] 正在启动后端服务...
start "NL-RPA Backend" /B cmd /c "%PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > logs\backend.log 2>&1"

echo [信息] 正在启动前端服务...
pushd "%~dp0frontend"
start "NL-RPA Frontend" /B cmd /c "npm run dev > ..\logs\frontend.log 2>&1"
popd

echo [信息] 正在等待服务初始化...
ping 127.0.0.1 -n 4 >nul

echo [信息] 正在打开默认浏览器...
start "" "http://localhost:5173"

echo.
echo [成功] NL-RPA 平台启动成功！
echo   前端地址：http://localhost:5173
echo   后端地址：http://localhost:8000
echo   接口文档：http://localhost:8000/docs
echo   后端日志：%~dp0logs\backend.log
echo   前端日志：%~dp0logs\frontend.log
echo.
echo [提示] 服务正在后台运行，按任意键停止全部服务。
echo [提示] 如使用 Ctrl+C，请在终止批处理提示中输入 N，以便自动清理进程。
echo.

pause >nul
call "%~dp0stop.bat"

endlocal
exit /b 0
