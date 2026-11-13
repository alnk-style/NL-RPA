#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod autostart;
mod scheduler;
mod settings;
mod sidecar;
mod single_instance;
mod tray;
mod window_manager;

use std::io;

use autostart::AutostartState;
use scheduler::LocalScheduler;
use settings::{
    apply_settings, settings_view, DesktopSettingsInput, DesktopSettingsView, RuntimeState,
};
use sidecar::SidecarManager;
use tauri::Manager;

const HEALTH_CHECK_ATTEMPTS: usize = 30;

fn main() {
    #[cfg(windows)]
    let _instance_guard = match single_instance::acquire(
        "Local\\com.nlrpa.desktop",
    ) {
        Some(guard) => guard,
        None => return,
    };

    let launch_hidden = std::env::args().any(|arg| arg == "--autostart");

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            let runtime =
                RuntimeState::load(app.handle()).map_err(|error| io::Error::other(error))?;
            let settings = runtime
                .snapshot()
                .map_err(|error| io::Error::other(error))?;
            let sidecars = SidecarManager::new(
                app.handle().clone(),
                runtime.paths.data_dir.clone(),
                runtime.paths.agent_config_file.clone(),
                runtime.settings.clone(),
            );
            let mut backend_ready = false;

            if settings.uses_local_backend() {
                if let Err(error) = sidecars.start_backend() {
                    eprintln!("Failed to start backend sidecar: {error}");
                }
                backend_ready = sidecars.wait_for_backend();
                if !backend_ready {
                    eprintln!(
                        "Backend health check did not pass after \
                         {HEALTH_CHECK_ATTEMPTS} attempts"
                    );
                }
            }

            if let Err(error) = sidecars.start_agent() {
                eprintln!("Failed to start desktop Agent sidecar: {error}");
            }

            let scheduler = LocalScheduler::new();
            if backend_ready {
                if let Err(error) = scheduler.start() {
                    eprintln!("Failed to start local scheduler: {error}");
                }
            }

            app.manage(runtime);
            app.manage(sidecars);
            app.manage(scheduler);
            app.manage(AutostartState::new());
            tray::create_tray(app.handle())?;

            if launch_hidden {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            autostart::get_autostart_enabled,
            autostart::set_autostart_enabled,
            window_manager::open_logs_directory,
            get_desktop_settings,
            save_desktop_settings,
        ])
        .on_window_event(|window, event| window_manager::handle_window_event(window, event))
        .run(tauri::generate_context!())
        .expect("failed to run NL-RPA Desktop");
}

#[tauri::command]
fn get_desktop_settings(
    runtime: tauri::State<'_, RuntimeState>,
) -> Result<DesktopSettingsView, String> {
    let settings = runtime.snapshot()?;
    Ok(settings_view(&settings))
}

#[tauri::command]
fn save_desktop_settings(
    input: DesktopSettingsInput,
    runtime: tauri::State<'_, RuntimeState>,
    sidecars: tauri::State<'_, SidecarManager>,
) -> Result<DesktopSettingsView, String> {
    let settings = apply_settings(&runtime, input)?;
    sidecars.restart()?;
    Ok(settings_view(&settings))
}
