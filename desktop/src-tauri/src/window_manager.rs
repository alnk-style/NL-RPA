use std::{
    path::PathBuf,
    sync::atomic::{AtomicBool, Ordering},
};

use tauri::{Manager, Runtime, WebviewWindow, Window, WindowEvent};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_shell::ShellExt;

static HAS_NOTIFIED_FIRST_HIDE: AtomicBool = AtomicBool::new(false);

pub fn hide_to_tray(window: &Window) {
    if let Err(error) = window.hide() {
        eprintln!("Failed to hide window: {error}");
        return;
    }

    if !HAS_NOTIFIED_FIRST_HIDE.swap(true, Ordering::SeqCst) {
        let _ = window
            .app_handle()
            .notification()
            .builder()
            .title("NL-RPA Desktop")
            .body("应用已在后台运行，点击托盘图标可恢复")
            .show();
    }
}

pub fn show_from_tray(window: &Window) {
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
}

pub fn show_webview_from_tray(window: &WebviewWindow) {
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
}

pub fn handle_window_event(window: &Window, event: &WindowEvent) {
    if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();
        hide_to_tray(window);
    }
}

pub(crate) fn open_log_directory<R: Runtime>(app: &tauri::AppHandle<R>) {
    let log_dir = app
        .path()
        .app_data_dir()
        .map(|path| path.join("logs"))
        .unwrap_or_else(|_| PathBuf::from("logs"));
    let _ = std::fs::create_dir_all(&log_dir);

    #[allow(deprecated)]
    let _ = app
        .shell()
        .open(log_dir.to_string_lossy().into_owned(), None);
}

#[tauri::command]
pub fn open_logs_directory(app: tauri::AppHandle) -> Result<(), String> {
    open_log_directory(&app);
    Ok(())
}
