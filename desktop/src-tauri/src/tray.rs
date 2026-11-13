use std::error::Error;

use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager,
};

use crate::{autostart::AutostartState, scheduler::LocalScheduler, window_manager};

const SHOW_WINDOW: &str = "show_main_window";
const TOGGLE_SCHEDULER: &str = "toggle_scheduler";
const TOGGLE_AUTOSTART: &str = "toggle_autostart";
const OPEN_LOGS: &str = "open_logs";
const QUIT: &str = "quit";

pub fn create_tray(app: &AppHandle) -> Result<(), Box<dyn Error>> {
    let show_item = MenuItem::with_id(app, SHOW_WINDOW, "显示主窗口", true, None::<&str>)?;
    let scheduler_item = CheckMenuItem::with_id(
        app,
        TOGGLE_SCHEDULER,
        "启动/停止调度",
        true,
        app.state::<LocalScheduler>().get_status(),
        None::<&str>,
    )?;
    let autostart_item = CheckMenuItem::with_id(
        app,
        TOGGLE_AUTOSTART,
        "开机自启动",
        true,
        app.state::<AutostartState>().is_enabled(),
        None::<&str>,
    )?;
    let logs_item = MenuItem::with_id(app, OPEN_LOGS, "查看日志", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, QUIT, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &show_item,
            &scheduler_item,
            &autostart_item,
            &logs_item,
            &quit_item,
        ],
    )?;
    let icon = app.default_window_icon().cloned().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "Default tray icon is missing")
    })?;
    TrayIconBuilder::new()
        .icon(icon)
        .tooltip("NL-RPA Desktop")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            SHOW_WINDOW => {
                if let Some(window) = app.get_webview_window("main") {
                    window_manager::show_webview_from_tray(&window);
                }
            }
            TOGGLE_SCHEDULER => {
                if let Err(error) = app.state::<LocalScheduler>().toggle() {
                    eprintln!("Failed to toggle scheduler: {error}");
                }
            }
            TOGGLE_AUTOSTART => {
                if let Err(error) = app.state::<AutostartState>().toggle() {
                    eprintln!("Failed to update autostart setting: {error}");
                }
            }
            OPEN_LOGS => window_manager::open_log_directory(app),
            QUIT => {
                app.state::<crate::sidecar::SidecarManager>().stop_all();
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;

    app.state::<LocalScheduler>()
        .attach_tray_item(scheduler_item);
    app.state::<AutostartState>()
        .attach_tray_item(autostart_item);

    Ok(())
}
