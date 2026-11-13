@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo [信息] 正在停止 NL-RPA 平台服务...

call :stop_port "8000" "后端 uvicorn 服务"
call :stop_port "5173" "前端 Vite 服务"

echo.
echo [成功] NL-RPA 平台服务已停止。
echo.
endlocal
exit /b 0

:stop_port
set "PORT=%~1"
set "SERVICE_NAME=%~2"
set "SEEN=0"
set "STOPPED=0"

for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do (
    set "SEEN=1"
    echo [信息] 正在停止%SERVICE_NAME%（PID %%P）...
    taskkill /PID %%P /T /F >nul 2>&1
    if errorlevel 1 (
        echo [错误] 无法停止%SERVICE_NAME%（PID %%P），请以管理员身份运行。
    ) else (
        set "STOPPED=1"
    )
)

if "!SEEN!"=="0" (
    echo [提示] 未发现正在运行的%SERVICE_NAME%。
) else if "!STOPPED!"=="1" (
    echo [成功] %SERVICE_NAME%已停止。
) else (
    echo [错误] %SERVICE_NAME%停止失败，请以管理员身份运行重试。
)

exit /b 0
