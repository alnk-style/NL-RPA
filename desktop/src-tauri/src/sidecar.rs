use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use tauri::AppHandle;
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

use crate::settings::{sqlite_database_url, DesktopSettings};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8000;
const HEALTH_CHECK_ATTEMPTS: usize = 30;
const HEALTH_CHECK_INTERVAL: Duration = Duration::from_secs(1);
const BACKEND_PROCESS_NAME: &str = "backend-server.exe";
const AGENT_PROCESS_NAME: &str = "desktop-agent.exe";

pub struct SidecarManager {
    app: AppHandle,
    data_dir: PathBuf,
    agent_config_file: PathBuf,
    settings: Arc<Mutex<DesktopSettings>>,
    backend: Mutex<Option<CommandChild>>,
    agent: Mutex<Option<CommandChild>>,
}

impl SidecarManager {
    pub fn new(
        app: AppHandle,
        data_dir: PathBuf,
        agent_config_file: PathBuf,
        settings: Arc<Mutex<DesktopSettings>>,
    ) -> Self {
        Self {
            app,
            data_dir,
            agent_config_file,
            settings,
            backend: Mutex::new(None),
            agent: Mutex::new(None),
        }
    }

    pub fn start_backend(&self) -> Result<(), String> {
        let settings = self.current_settings()?;
        if !settings.uses_local_backend() {
            return Ok(());
        }

        let mut backend = self
            .backend
            .lock()
            .map_err(|_| "Backend sidecar lock is poisoned".to_string())?;

        if backend.is_some() {
            return Ok(());
        }

        stop_process_image(BACKEND_PROCESS_NAME);

        let (receiver, child) = self
            .app
            .shell()
            .sidecar("backend-server")
            .map_err(|error| error.to_string())?
            .current_dir(&self.data_dir)
            .env("PYTHONUNBUFFERED", "1")
            .env("NL_RPA_DESKTOP_MODE", "1")
            .env(
                "NL_RPA_PARENT_PID",
                std::process::id().to_string(),
            )
            .env("DATABASE_URL", sqlite_database_url(&self.data_dir))
            .spawn()
            .map_err(|error| error.to_string())?;

        log_sidecar_output("backend-server", receiver);
        *backend = Some(child);
        Ok(())
    }

    pub fn start_agent(&self) -> Result<(), String> {
        let mut agent = self
            .agent
            .lock()
            .map_err(|_| "Desktop Agent sidecar lock is poisoned".to_string())?;

        if agent.is_some() {
            return Ok(());
        }

        stop_process_image(AGENT_PROCESS_NAME);

        let settings = self.current_settings()?;
        let mut command = self
            .app
            .shell()
            .sidecar("desktop-agent")
            .map_err(|error| error.to_string())?
            .current_dir(&self.data_dir)
            .env(
                "NL_RPA_AGENT_CONFIG",
                self.agent_config_file.to_string_lossy().to_string(),
            )
            .env(
                "NL_RPA_PARENT_PID",
                std::process::id().to_string(),
            )
            .env("NL_RPA_AGENT_SERVER_URL", settings.agent_server_url())
            .env(
                "NL_RPA_AGENT_AUTO_PAIR",
                if settings.uses_local_backend() {
                    "1"
                } else {
                    "0"
                },
            );
        if !settings.agent_pairing_code.trim().is_empty() {
            command = command.env(
                "NL_RPA_AGENT_PAIRING_CODE",
                settings.agent_pairing_code.trim(),
            );
        }

        let (receiver, child) = command.spawn().map_err(|error| error.to_string())?;

        log_sidecar_output("desktop-agent", receiver);
        *agent = Some(child);
        Ok(())
    }

    pub fn stop_all(&self) {
        stop_child("desktop-agent", &self.agent);
        stop_child("backend-server", &self.backend);
        stop_process_image(AGENT_PROCESS_NAME);
        stop_process_image(BACKEND_PROCESS_NAME);
    }

    pub fn restart(&self) -> Result<(), String> {
        self.stop_all();
        std::thread::sleep(Duration::from_millis(500));

        let settings = self.current_settings()?;
        if settings.uses_local_backend() {
            self.start_backend()?;
            if !self.wait_for_backend() {
                return Err("Backend sidecar did not become healthy".to_string());
            }
        }

        self.start_agent()
    }

    pub fn wait_for_backend(&self) -> bool {
        for attempt in 1..=HEALTH_CHECK_ATTEMPTS {
            if self.health_check() {
                return true;
            }
            if attempt < HEALTH_CHECK_ATTEMPTS {
                std::thread::sleep(HEALTH_CHECK_INTERVAL);
            }
        }
        false
    }

    pub fn health_check(&self) -> bool {
        request_backend("GET", "/health").is_ok()
    }

    fn current_settings(&self) -> Result<DesktopSettings, String> {
        self.settings
            .lock()
            .map(|settings| settings.clone())
            .map_err(|_| "Desktop settings lock is poisoned".to_string())
    }
}

impl Drop for SidecarManager {
    fn drop(&mut self) {
        self.stop_all();
    }
}

pub(crate) fn request_backend(method: &str, path: &str) -> Result<String, String> {
    let address = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));
    let timeout = Duration::from_secs(2);
    let mut stream = match TcpStream::connect_timeout(&address, timeout) {
        Ok(stream) => stream,
        Err(error) => return Err(error.to_string()),
    };

    if stream.set_read_timeout(Some(timeout)).is_err()
        || stream.set_write_timeout(Some(timeout)).is_err()
    {
        return Err("Failed to configure backend socket timeout".to_string());
    }

    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| error.to_string())?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;

    let (headers, body) = response
        .split_once("\r\n\r\n")
        .unwrap_or((response.as_str(), ""));
    let status_line = headers.lines().next().unwrap_or_default();
    if !status_line.contains(" 200 ") {
        return Err(format!(
            "Backend returned an unsuccessful status: {status_line}"
        ));
    }

    Ok(body.to_string())
}

fn stop_child(name: &str, child: &Mutex<Option<CommandChild>>) {
    let child = match child.lock() {
        Ok(mut guard) => guard.take(),
        Err(poisoned) => poisoned.into_inner().take(),
    };

    if let Some(child) = child {
        kill_process_tree(child.pid());
        if let Err(error) = child.kill() {
            eprintln!("Failed to stop {name}: {error}");
        }
    }
}

#[cfg(windows)]
fn kill_process_tree(pid: u32) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

#[cfg(not(windows))]
fn kill_process_tree(_pid: u32) {}

#[cfg(windows)]
fn stop_process_image(image_name: &str) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    let _ = std::process::Command::new("taskkill")
        .args(["/IM", image_name, "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

#[cfg(not(windows))]
fn stop_process_image(_image_name: &str) {}

fn log_sidecar_output(
    name: &'static str,
    mut receiver: tauri::async_runtime::Receiver<CommandEvent>,
) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    println!("[{name}] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[{name}] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Error(error) => {
                    eprintln!("[{name}] process error: {error}");
                }
                CommandEvent::Terminated(payload) => {
                    println!("[{name}] exited with code {:?}", payload.code);
                }
                _ => {}
            }
        }
    });
}
