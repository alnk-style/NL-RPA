# Sidecar binaries

Build the two executables before running or packaging the Tauri app:

```powershell
python scripts/build_backend.py
python scripts/build_agent.py
```

`build_backend.py` also installs the Playwright browser on the build machine
and embeds the required Chromium files into `backend-server.exe`.

Tauri expects the target-suffixed files in this directory, for example:

- `backend-server-x86_64-pc-windows-msvc.exe`
- `desktop-agent-x86_64-pc-windows-msvc.exe`
