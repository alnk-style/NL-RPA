use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex,
};

use tauri::{menu::CheckMenuItem, Wry};

use crate::sidecar::request_backend;

pub struct LocalScheduler {
    is_running: AtomicBool,
    tray_item: Mutex<Option<CheckMenuItem<Wry>>>,
}

impl LocalScheduler {
    pub fn new() -> Self {
        Self {
            is_running: AtomicBool::new(false),
            tray_item: Mutex::new(None),
        }
    }

    pub fn start(&self) -> Result<(), String> {
        let body = request_backend("POST", "/api/scheduler/start")?;
        validate_status(&body, "running")?;
        self.is_running.store(true, Ordering::SeqCst);
        self.sync_tray_state();
        Ok(())
    }

    pub fn stop(&self) -> Result<(), String> {
        let body = request_backend("POST", "/api/scheduler/stop")?;
        validate_status(&body, "stopped")?;
        self.is_running.store(false, Ordering::SeqCst);
        self.sync_tray_state();
        Ok(())
    }

    pub fn toggle(&self) -> Result<(), String> {
        if self.get_status() {
            self.stop()
        } else {
            self.start()
        }
    }

    pub fn get_status(&self) -> bool {
        self.is_running.load(Ordering::SeqCst)
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
            let _ = item.set_checked(self.get_status());
        }
    }
}

fn validate_status(body: &str, expected: &str) -> Result<(), String> {
    let payload: serde_json::Value = serde_json::from_str(body)
        .map_err(|error| format!("Invalid scheduler response: {error}"))?;
    let actual = payload.get("status").and_then(serde_json::Value::as_str);

    if actual == Some(expected) {
        return Ok(());
    }

    Err(format!(
        "Scheduler returned {actual:?}, expected {expected:?}"
    ))
}
