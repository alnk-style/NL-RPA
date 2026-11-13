use std::{
    io,
    process::Command,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};

use tauri::{menu::CheckMenuItem, Wry};

const APP_NAME: &str = "NL-RPA Desktop";
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";

pub fn enable_autostart() -> io::Result<()> {
    #[cfg(not(target_os = "windows"))]
    {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "Autostart is only supported on Windows",
        ));
    }

    #[cfg(target_os = "windows")]
    {
        let executable = std::env::current_exe()?;
        let command = format!("\"{}\" --autostart", executable.display());
        let status = Command::new("reg.exe")
            .arg("add")
            .arg(RUN_KEY)
            .arg("/v")
            .arg(APP_NAME)
            .arg("/t")
            .arg("REG_SZ")
            .arg("/d")
            .arg(&command)
            .arg("/f")
            .status()?;

        ensure_success(status, "register autostart")
    }
}

pub fn disable_autostart() -> io::Result<()> {
    #[cfg(not(target_os = "windows"))]
    {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "Autostart is only supported on Windows",
        ));
    }

    #[cfg(target_os = "windows")]
    {
        if !is_autostart_enabled() {
            return Ok(());
        }

        let status = Command::new("reg.exe")
            .arg("delete")
            .arg(RUN_KEY)
            .arg("/v")
            .arg(APP_NAME)
            .arg("/f")
            .status()?;

        ensure_success(status, "remove autostart")
    }
}

pub fn is_autostart_enabled() -> bool {
    #[cfg(not(target_os = "windows"))]
    {
        return false;
    }

    #[cfg(target_os = "windows")]
    {
        Command::new("reg.exe")
            .arg("query")
            .arg(RUN_KEY)
            .arg("/v")
            .arg(APP_NAME)
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }
}

pub struct AutostartState {
    enabled: AtomicBool,
    tray_item: Mutex<Option<CheckMenuItem<Wry>>>,
}

impl AutostartState {
    pub fn new() -> Self {
        Self {
            enabled: AtomicBool::new(is_autostart_enabled()),
            tray_item: Mutex::new(None),
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::SeqCst)
    }

    pub fn enable(&self) -> io::Result<()> {
        enable_autostart()?;
        self.enabled.store(true, Ordering::SeqCst);
        self.sync_tray_state();
        Ok(())
    }

    pub fn disable(&self) -> io::Result<()> {
        disable_autostart()?;
        self.enabled.store(false, Ordering::SeqCst);
        self.sync_tray_state();
        Ok(())
    }

    pub fn set_enabled(&self, enabled: bool) -> io::Result<()> {
        if enabled {
            self.enable()
        } else {
            self.disable()
        }
    }

    pub fn toggle(&self) -> io::Result<()> {
        self.set_enabled(!self.is_enabled())
    }

    pub fn attach_tray_item(&self, item: CheckMenuItem<Wry>) {
        match self.tray_item.lock() {
            Ok(mut current) => {
                *current = Some(item);
            }
            Err(poisoned) => {
                *poisoned.into_inner() = Some(item);
            }
        }
        self.sync_tray_state();
    }

    fn sync_tray_state(&self) {
        let item = match self.tray_item.lock() {
            Ok(current) => current.clone(),
            Err(poisoned) => poisoned.into_inner().clone(),
        };

        if let Some(item) = item {
            let _ = item.set_checked(self.is_enabled());
        }
    }
}

#[cfg(target_os = "windows")]
fn ensure_success(status: std::process::ExitStatus, action: &str) -> io::Result<()> {
    if status.success() {
        return Ok(());
    }

    Err(io::Error::new(
        io::ErrorKind::Other,
        format!("Failed to {action}: registry command exited with {status}"),
    ))
}

#[tauri::command]
pub fn get_autostart_enabled(state: tauri::State<'_, AutostartState>) -> bool {
    state.is_enabled()
}

#[tauri::command]
pub fn set_autostart_enabled(
    state: tauri::State<'_, AutostartState>,
    enabled: bool,
) -> Result<(), String> {
    state
        .set_enabled(enabled)
        .map_err(|error| error.to_string())
}
