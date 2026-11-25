//! GladTalker Studio desktop shell (issue #11, phase 3).
//!
//! The window shows the same Studio UI as the web version (`studio-ui/dist`). This shell adds
//! what a browser can't do well:
//! - starts and stops the local engine (`studio_server.py`) on a private port with a fresh token;
//! - native "Save project" and "Choose GladTalker folder" dialogs;
//! - "Show in folder" for finished videos (only files inside the engine's workspace);
//! - a desktop notification when a render ends while the app is in the background;
//! - render progress on the taskbar icon.

mod engine;

use std::path::PathBuf;
use std::sync::Mutex;

use tauri::window::{ProgressBarState, ProgressBarStatus};
use tauri::{AppHandle, Manager, RunEvent, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

use engine::Status;

const MAX_PROJECT_BYTES: usize = 50 * 1024 * 1024;

#[derive(Default)]
struct AppState {
    /// Held while the engine is being started, so two calls never start two engines.
    starting: Mutex<()>,
    engine: Mutex<Option<engine::Running>>,
}

fn config_file(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("engine.json"))
}

fn log_file(app: &AppHandle) -> PathBuf {
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("GladTalker Studio"))
        .join("engine.log")
}

fn stop_engine(app: &AppHandle) {
    let state = app.state::<AppState>();
    let running = state.engine.lock().unwrap().take();
    drop(running); // Drop stops the process tree
}

/// Starts the engine if it isn't running, and reports its state.
fn ensure_started(app: &AppHandle) -> Status {
    let state = app.state::<AppState>();
    let _one_at_a_time = state.starting.lock().unwrap();
    {
        let mut running = state.engine.lock().unwrap();
        if let Some(r) = running.as_mut() {
            if r.is_alive() {
                return r.status.clone();
            }
        }
        *running = None;
    }
    let saved = config_file(app).map(|p| engine::load_choice(&p).folder).unwrap_or_default();
    let home = std::env::var_os("GLADTALKER_HOME").map(PathBuf::from);
    let python = std::env::var_os("GLADTALKER_PYTHON").map(PathBuf::from);
    let candidates = engine::candidate_folders(home, saved, std::env::current_exe().ok());
    let found = match engine::locate(&candidates, python.as_deref()) {
        Ok(found) => found,
        Err(status) => return status,
    };
    match engine::start(&found, &log_file(app)) {
        Ok(running) => {
            let status = running.status.clone();
            *state.engine.lock().unwrap() = Some(running);
            status
        }
        Err(status) => status,
    }
}

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(f).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn start_engine(app: AppHandle) -> Result<Status, String> {
    blocking(move || ensure_started(&app)).await
}

#[tauri::command]
async fn restart_engine(app: AppHandle) -> Result<Status, String> {
    blocking(move || {
        stop_engine(&app);
        ensure_started(&app)
    })
    .await
}

/// Lets the user point at their GladTalker folder. Returns None if they cancel.
#[tauri::command]
async fn choose_engine_folder(app: AppHandle) -> Result<Option<Status>, String> {
    blocking(move || {
        let picked = app
            .dialog()
            .file()
            .set_title("Choose the GladTalker folder (the one with studio_server.py)")
            .blocking_pick_folder()?; // None: the user cancelled
        let folder = picked.into_path().ok()?;
        if !engine::is_engine_folder(&folder) {
            return Some(Status::NotFound {
                message: format!(
                    "{} isn't a GladTalker folder: it has no studio_server.py. Choose the folder you cloned or unzipped GladTalker into.",
                    folder.display()
                ),
                folder: None,
            });
        }
        if let Some(path) = config_file(&app) {
            let _ = engine::save_choice(&path, &engine::SavedChoice { folder: Some(folder) });
        }
        stop_engine(&app);
        Some(ensure_started(&app))
    })
    .await
}

/// "Save project": a native Save dialog, then the file is written where the user chose.
#[tauri::command]
async fn save_project(app: AppHandle, contents: String, suggested_name: String) -> Result<Option<String>, String> {
    if contents.len() > MAX_PROJECT_BYTES {
        return Err("The project is too large to save.".into());
    }
    blocking(move || {
        let picked = app
            .dialog()
            .file()
            .set_title("Save GladTalker project")
            .add_filter("GladTalker project", &["json"])
            .set_file_name(&suggested_name)
            .blocking_save_file();
        let Some(path) = picked.and_then(|p| p.into_path().ok()) else {
            return Ok(None);
        };
        std::fs::write(&path, contents).map_err(|e| format!("Couldn't save {}: {e}", path.display()))?;
        Ok(Some(path.display().to_string()))
    })
    .await?
}

/// "Show in folder" for a finished render. Only files the engine wrote (inside its workspace).
#[tauri::command]
fn reveal_file(app: AppHandle, path: String) -> Result<(), String> {
    let workspace = match app.state::<AppState>().engine.lock().unwrap().as_ref().map(|r| r.status.clone()) {
        Some(Status::Ready { workspace, .. }) => PathBuf::from(workspace),
        _ => return Err("The engine isn't running.".into()),
    };
    let target = PathBuf::from(&path);
    if !engine::is_inside(&workspace, &target) {
        return Err("Only files that GladTalker Studio made can be shown.".into());
    }
    app.opener().reveal_item_in_dir(&target).map_err(|e| e.to_string())
}

/// A desktop notification, only when the user is looking at something else.
#[tauri::command]
fn notify(window: WebviewWindow, title: String, body: String) -> Result<bool, String> {
    if window.is_focused().unwrap_or(false) {
        return Ok(false);
    }
    window
        .app_handle()
        .notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|e| e.to_string())?;
    Ok(true)
}

/// Render progress on the taskbar icon: a fraction 0–1, or none to clear it.
#[tauri::command]
fn set_progress(window: WebviewWindow, fraction: Option<f64>, failed: bool) -> Result<(), String> {
    let state = match fraction {
        None => ProgressBarState { status: Some(ProgressBarStatus::None), progress: None },
        Some(f) => ProgressBarState {
            status: Some(if failed { ProgressBarStatus::Error } else { ProgressBarStatus::Normal }),
            progress: Some((f.clamp(0.0, 1.0) * 100.0).round() as u64),
        },
    };
    window.set_progress_bar(state).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            start_engine,
            restart_engine,
            choose_engine_folder,
            save_project,
            reveal_file,
            notify,
            set_progress
        ])
        .build(tauri::generate_context!())
        .expect("error while building GladTalker Studio")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_engine(app);
            }
        });
}
