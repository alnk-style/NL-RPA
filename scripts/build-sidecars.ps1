param(
    [string]$Python = "python",
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

& $Python (Join-Path $root "scripts\build_backend.py") `
    --target-triple $TargetTriple
& $Python (Join-Path $root "scripts\build_agent.py") `
    --target-triple $TargetTriple
