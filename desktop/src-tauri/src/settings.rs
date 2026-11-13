use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use base64::{engine::general_purpose::URL_SAFE, Engine as _};
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

const DEFAULT_REMOTE_BACKEND_URL: &str = "";

pub(crate) fn sqlite_database_url(data_dir: &Path) -> String {
    let database_path = data_dir.join("rpa.db").to_string_lossy().replace('\\', "/");
    format!("sqlite+aiosqlite:///{database_path}")
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct DesktopSettings {
    pub run_mode: String,
    pub remote_backend_url: String,
    pub deepseek_api_key: String,
    pub agent_pairing_code: String,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            run_mode: "local".to_string(),
            remote_backend_url: DEFAULT_REMOTE_BACKEND_URL.to_string(),
            deepseek_api_key: String::new(),
            agent_pairing_code: String::new(),
        }
    }
}

impl DesktopSettings {
    pub fn uses_local_backend(&self) -> bool {
        self.run_mode != "remote"
    }

    pub fn agent_server_url(&self) -> String {
        if self.uses_local_backend() {
            return "http://127.0.0.1:8000".to_string();
        }

        self.remote_backend_url
            .trim()
            .trim_end_matches('/')
            .to_string()
    }
}

#[derive(Clone)]
pub struct RuntimePaths {
    pub data_dir: PathBuf,
    pub settings_file: PathBuf,
    pub env_file: PathBuf,
    pub agent_config_file: PathBuf,
}

impl RuntimePaths {
    pub fn load(app: &AppHandle) -> Result<Self, String> {
        let data_dir = match app.path().home_dir() {
            Ok(path) => path,
            Err(_) => std::env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .ok_or_else(|| {
                    "Could not resolve the current user profile".to_string()
                })?,
        };
        let data_dir = data_dir.join(".nl-rpa");
        fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;

        Ok(Self {
            settings_file: data_dir.join("desktop-settings.json"),
            env_file: data_dir.join(".env"),
            agent_config_file: data_dir.join("config.json"),
            data_dir,
        })
    }

    pub fn load_settings(&self) -> Result<DesktopSettings, String> {
        if !self.settings_file.exists() {
            return Ok(DesktopSettings::default());
        }

        let raw = fs::read_to_string(&self.settings_file).map_err(|error| error.to_string())?;
        serde_json::from_str(&raw).map_err(|error| error.to_string())
    }

    pub fn save_settings(&self, settings: &DesktopSettings) -> Result<(), String> {
        let raw = serde_json::to_string_pretty(settings).map_err(|error| error.to_string())?;
        fs::write(&self.settings_file, raw).map_err(|error| error.to_string())
    }

    pub fn write_env_file(&self, settings: &DesktopSettings) -> Result<(), String> {
        let encryption_key = read_env_value(&self.env_file, "CREDENTIAL_ENCRYPTION_KEY")
            .unwrap_or_else(generate_fernet_key);
        let jwt_secret =
            read_env_value(&self.env_file, "JWT_SECRET_KEY").unwrap_or_else(generate_fernet_key);
        let deepseek_key = if settings.deepseek_api_key.trim().is_empty() {
            read_env_value(&self.env_file, "DEEPSEEK_API_KEY").unwrap_or_default()
        } else {
            settings.deepseek_api_key.trim().to_string()
        };
        let database_url = sqlite_database_url(&self.data_dir);

        let mut contents = format!(
            concat!(
                "DEEPSEEK_API_KEY={}\n",
                "CREDENTIAL_ENCRYPTION_KEY={}\n",
                "JWT_SECRET_KEY={}\n",
                "DEV_AUTH_BYPASS=true\n",
                "DATABASE_URL={}\n",
            ),
            single_line(&deepseek_key),
            single_line(&encryption_key),
            single_line(&jwt_secret),
            single_line(&database_url),
        );
        let preserved = preserved_env_lines(&self.env_file);
        if !preserved.is_empty() {
            contents.push_str("# 用户自定义开关（桌面版会保留）\n");
            for line in preserved {
                contents.push_str(&line);
                contents.push('\n');
            }
        }
        fs::write(&self.env_file, contents).map_err(|error| error.to_string())
    }
}

pub struct RuntimeState {
    pub paths: RuntimePaths,
    pub settings: Arc<Mutex<DesktopSettings>>,
}

impl RuntimeState {
    pub fn load(app: &AppHandle) -> Result<Self, String> {
        let paths = RuntimePaths::load(app)?;
        let settings = paths.load_settings()?;
        paths.write_env_file(&settings)?;
        Ok(Self {
            paths,
            settings: Arc::new(Mutex::new(settings)),
        })
    }

    pub fn snapshot(&self) -> Result<DesktopSettings, String> {
        self.settings
            .lock()
            .map(|settings| settings.clone())
            .map_err(|_| "Desktop settings lock is poisoned".to_string())
    }
}

#[derive(Debug, Deserialize)]
pub struct DesktopSettingsInput {
    pub run_mode: String,
    pub remote_backend_url: String,
    pub deepseek_api_key: Option<String>,
    pub agent_pairing_code: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct DesktopSettingsView {
    pub run_mode: String,
    pub remote_backend_url: String,
    pub deepseek_api_key_set: bool,
    pub agent_pairing_code_set: bool,
}

pub fn apply_settings(
    state: &RuntimeState,
    input: DesktopSettingsInput,
) -> Result<DesktopSettings, String> {
    let run_mode = if input.run_mode == "remote" {
        "remote"
    } else {
        "local"
    };
    let remote_url = input.remote_backend_url.trim().trim_end_matches('/');
    if run_mode == "remote" && remote_url.is_empty() {
        return Err("Remote backend URL is required".to_string());
    }

    let mut settings = state
        .settings
        .lock()
        .map_err(|_| "Desktop settings lock is poisoned".to_string())?;
    settings.run_mode = run_mode.to_string();
    settings.remote_backend_url = remote_url.to_string();

    if let Some(api_key) = input.deepseek_api_key {
        let api_key = api_key.trim();
        if !api_key.is_empty() {
            settings.deepseek_api_key = api_key.to_string();
        }
    }
    if let Some(pairing_code) = input.agent_pairing_code {
        settings.agent_pairing_code = pairing_code.trim().to_string();
    }

    state.paths.save_settings(&settings)?;
    state.paths.write_env_file(&settings)?;
    Ok(settings.clone())
}

pub fn settings_view(settings: &DesktopSettings) -> DesktopSettingsView {
    DesktopSettingsView {
        run_mode: settings.run_mode.clone(),
        remote_backend_url: settings.remote_backend_url.clone(),
        deepseek_api_key_set: !settings.deepseek_api_key.trim().is_empty(),
        agent_pairing_code_set: !settings.agent_pairing_code.trim().is_empty(),
    }
}

fn read_env_value(path: &Path, key: &str) -> Option<String> {
    let raw = fs::read_to_string(path).ok()?;
    raw.lines().find_map(|line| {
        let (candidate, value) = line.split_once('=')?;
        if candidate.trim() == key {
            Some(value.trim().to_string())
        } else {
            None
        }
    })
}

const MANAGED_ENV_KEYS: [&str; 5] = [
    "DEEPSEEK_API_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
    "JWT_SECRET_KEY",
    "DEV_AUTH_BYPASS",
    "DATABASE_URL",
];

fn preserved_env_lines(path: &Path) -> Vec<String> {
    let Ok(raw) = fs::read_to_string(path) else {
        return Vec::new();
    };
    raw.lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') {
                return None;
            }
            let (key, _) = trimmed.split_once('=')?;
            if MANAGED_ENV_KEYS.contains(&key.trim()) {
                return None;
            }
            Some(trimmed.to_string())
        })
        .collect()
}

fn single_line(value: &str) -> String {
    value.replace(['\r', '\n'], "")
}

fn generate_fernet_key() -> String {
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    URL_SAFE.encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::{sqlite_database_url, DesktopSettings, RuntimePaths};
    use std::path::Path;

    #[test]
    fn local_and_remote_agent_urls_are_normalized() {
        let mut settings = DesktopSettings::default();
        assert_eq!(settings.agent_server_url(), "http://127.0.0.1:8000",);

        settings.run_mode = "remote".to_string();
        settings.remote_backend_url = "https://rpa.example.com/".to_string();
        assert_eq!(settings.agent_server_url(), "https://rpa.example.com",);
    }

    #[test]
    fn env_file_contains_generated_credentials() {
        let temp_dir =
            std::env::temp_dir().join(format!("nl-rpa-settings-{}", std::process::id(),));
        std::fs::create_dir_all(&temp_dir).unwrap();
        let paths = RuntimePaths {
            data_dir: temp_dir.clone(),
            settings_file: temp_dir.join("settings.json"),
            env_file: temp_dir.join(".env"),
            agent_config_file: temp_dir.join("config.json"),
        };
        paths.write_env_file(&DesktopSettings::default()).unwrap();
        let raw = std::fs::read_to_string(&paths.env_file).unwrap();

        assert!(raw.contains("CREDENTIAL_ENCRYPTION_KEY="));
        assert!(raw.contains("JWT_SECRET_KEY="));
        assert!(raw.contains("DEV_AUTH_BYPASS=true"));
        assert!(raw.contains(&format!("DATABASE_URL={}", sqlite_database_url(&temp_dir),)));
    }

    #[test]
    fn env_file_preserves_custom_switches() {
        let temp_dir =
            std::env::temp_dir().join(format!("nl-rpa-settings-custom-{}", std::process::id(),));
        std::fs::create_dir_all(&temp_dir).unwrap();
        let paths = RuntimePaths {
            data_dir: temp_dir.clone(),
            settings_file: temp_dir.join("settings.json"),
            env_file: temp_dir.join(".env"),
            agent_config_file: temp_dir.join("config.json"),
        };
        std::fs::write(
            &paths.env_file,
            "DEEPSEEK_API_KEY=old\nMCP_CLIENT=true\nMETRICS_API=true\n",
        )
        .unwrap();

        paths.write_env_file(&DesktopSettings::default()).unwrap();
        let raw = std::fs::read_to_string(&paths.env_file).unwrap();

        assert!(raw.contains("MCP_CLIENT=true"));
        assert!(raw.contains("METRICS_API=true"));
        // 托管键只写一次，不会被自定义区重复保留。
        assert_eq!(raw.matches("DEEPSEEK_API_KEY=").count(), 1);
        assert_eq!(raw.matches("DATABASE_URL=").count(), 1);
    }

    #[test]
    fn sqlite_database_url_uses_absolute_windows_path() {
        let data_dir = Path::new(r"C:\Users\Administrator\AppData\Roaming\com.nlrpa.desktop");

        assert_eq!(
            sqlite_database_url(data_dir),
            "sqlite+aiosqlite:///C:/Users/Administrator/AppData/Roaming/com.nlrpa.desktop/rpa.db",
        );
    }
}
