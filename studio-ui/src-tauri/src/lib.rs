//! GladTalker Studio desktop shell (issue #11, phase 3).
//!
//! The window shows the same Studio UI as the web version (`studio-ui/dist`). This shell adds
//! what a browser can't do well:
//! - starts and stops the local engine (`studio_server.py`) on a private port with a fresh token;
//! - native "Save project" and "Choose GladTalker folder" dialogs;
//! - "Show in folder" for finished videos (only files inside the engine's workspace);
//! - a desktop notification when a render ends while the app is in the background;
//! - render progress on the taskbar icon.

// Status is the error type for starting the engine: it happens once per start and is sent to the
// UI as is, so its size (a few strings) doesn't matter and boxing it would only add noise.
#![allow(clippy::result_large_err)]

mod engine;
mod supervisor;

use std::path::PathBuf;

use tauri::window::{ProgressBarState, ProgressBarStatus};
use tauri::{AppHandle, Manager, RunEvent, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

use engine::Status;
use supervisor::Supervisor;

const MAX_PROJECT_BYTES: usize = 50 * 1024 * 1024;

/// The one engine this app runs (see supervisor.rs: start, restart, and folder changes are
/// serialized, so none of them acts on a stale engine).
type AppState = Supervisor<engine::Running>;

/// Where settings and logs go: the app's standard folders, or `GLADTALKER_STUDIO_DATA_DIR`
/// (used by the desktop tests so they never touch a real user's settings).
fn data_override() -> Option<PathBuf> {
    std::env::var_os("GLADTALKER_STUDIO_DATA_DIR").filter(|v| !v.is_empty()).map(PathBuf::from)
}

fn config_file(app: &AppHandle) -> Option<PathBuf> {
    match data_override() {
        Some(dir) => Some(dir.join("engine.json")),
        None => app.path().app_config_dir().ok().map(|d| d.join("engine.json")),
    }
}

fn log_file(app: &AppHandle) -> PathBuf {
    match data_override() {
        Some(dir) => dir.join("logs"),
        None => app.path().app_log_dir().unwrap_or_else(|_| std::env::temp_dir().join("GladTalker Studio")),
    }
    .join("engine.log")
}

/// The engine's workspace: `<folder>/results/studio`, or `GLADTALKER_STUDIO_WORKSPACE`.
fn workspace_for(folder: &std::path::Path) -> PathBuf {
    std::env::var_os("GLADTALKER_STUDIO_WORKSPACE")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| engine::workspace_of(folder))
}

/// Development builds load the UI from the dev server (`build.devUrl`), a different origin
/// from the packaged app's, so the engine is told to accept it. Release builds never are.
fn dev_origins(app: &AppHandle) -> Vec<String> {
    if !cfg!(debug_assertions) {
        return Vec::new();
    }
    app.config().build.dev_url.iter().map(|url| url.origin().ascii_serialization()).collect()
}

fn stop_engine(app: &AppHandle) {
    app.state::<AppState>().stop();
}

/// Starts the engine if it isn't running (with `restart`, stops it first; with `choose`, uses
/// that folder from now on), and reports its state.
fn ensure_started(app: &AppHandle, restart: bool, choose: Option<PathBuf>) -> Status {
    app.state::<AppState>().ensure(restart, choose, |chosen| {
        let saved = config_file(app).map(|p| engine::load_choice(&p).folder).unwrap_or_default();
        let home = std::env::var_os("GLADTALKER_HOME").map(PathBuf::from);
        let python = std::env::var_os("GLADTALKER_PYTHON").map(PathBuf::from);
        let candidates = engine::candidate_folders(chosen, home, saved, std::env::current_exe().ok());
        let found = engine::locate(&candidates, python.as_deref())?;
        engine::start(&found, &log_file(app), &workspace_for(&found.folder), &dev_origins(app))
    })
}

/// Adds a note for the user to a Ready status (the UI announces it).
fn with_note(status: Status, note: String) -> Status {
    match status {
        Status::Ready { base, token, folder, python, workspace, .. } => {
            Status::Ready { base, token, folder, python, workspace, note: Some(note) }
        }
        other => other,
    }
}

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(f).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn start_engine(app: AppHandle) -> Result<Status, String> {
    blocking(move || ensure_started(&app, false, None)).await
}

#[tauri::command]
async fn restart_engine(app: AppHandle) -> Result<Status, String> {
    blocking(move || ensure_started(&app, true, None)).await
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
        let saved = config_file(&app)
            .map(|path| engine::save_choice(&path, &engine::SavedChoice { folder: Some(folder.clone()) }));
        // Used first from now on in this session, whether or not it could be remembered.
        let status = ensure_started(&app, true, Some(folder));
        Some(match saved {
            Some(Err(err)) => with_note(status, format!("GladTalker Studio couldn’t remember this folder for next time ({err}).")),
            _ => status,
        })
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
        // Whole or not at all: a failed save never leaves a truncated project behind.
        engine::write_atomically(&path, contents.as_bytes())
            .map_err(|e| format!("Couldn’t save {}: {e}. The previous file, if any, is unchanged.", path.display()))?;
        Ok(Some(path.display().to_string()))
    })
    .await?
}

/// "Show in folder" for a finished render. Only files the engine wrote (inside its workspace).
#[tauri::command]
fn reveal_file(app: AppHandle, path: String) -> Result<(), String> {
    let workspace = match app.state::<AppState>().current() {
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
