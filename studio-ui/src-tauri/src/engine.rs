//! Finding, starting, and stopping the local GladTalker engine (`studio_server.py`).
//!
//! The desktop app doesn't bundle Python. It uses a GladTalker folder that already has a Python
//! environment set up (see the README), which it finds in this order:
//!
//! 1. the `GLADTALKER_HOME` environment variable;
//! 2. the folder chosen earlier with "Choose GladTalker folder…" (saved in the app's config);
//! 3. a GladTalker folder that contains the app (a development build, or the app copied there).
//!
//! Inside it, Python is `GLADTALKER_PYTHON` if set, else the first of `venv-dml`, `venv`, `.venv`
//! that exists. The engine is started on a free port with a fresh random token, and told to shut
//! itself down if the app goes away (`--exit-with-parent`).

use std::collections::VecDeque;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// How long a cold start may take (importing PyTorch and loading the face detector can take a
/// minute on a slow disk).
pub const START_TIMEOUT: Duration = Duration::from_secs(300);
const LOG_TAIL_LINES: usize = 40;
const VENV_NAMES: [&str; 3] = ["venv-dml", "venv", ".venv"];

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct SavedChoice {
    pub folder: Option<PathBuf>,
}

/// What the UI is told about the engine.
#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum Status {
    /// No GladTalker folder (or no Python in it) was found: the UI asks the user to choose one.
    NotFound { message: String, folder: Option<String> },
    /// Started, but it stopped or never became ready.
    Failed { message: String, folder: String, python: String, log: String, log_path: String },
    /// Running and accepting requests at `base` with `token`.
    Ready { base: String, token: String, folder: String, python: String, workspace: String },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Located {
    pub folder: PathBuf,
    pub python: PathBuf,
}

pub fn is_engine_folder(folder: &Path) -> bool {
    folder.join("studio_server.py").is_file()
}

/// The Python interpreter inside a GladTalker folder's virtual environment.
pub fn find_python(folder: &Path, override_python: Option<&Path>) -> Option<PathBuf> {
    if let Some(p) = override_python {
        return p.is_file().then(|| p.to_path_buf());
    }
    VENV_NAMES.iter().map(|name| venv_python(&folder.join(name))).find(|p| p.is_file())
}

fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// Folders to try, best first (see the module docs).
pub fn candidate_folders(home: Option<PathBuf>, saved: Option<PathBuf>, exe: Option<PathBuf>) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    out.extend(home);
    out.extend(saved);
    if let Some(exe) = exe {
        out.extend(exe.ancestors().skip(1).take(6).map(Path::to_path_buf));
    }
    out
}

/// The first candidate that is a GladTalker folder with a Python environment.
pub fn locate(candidates: &[PathBuf], override_python: Option<&Path>) -> Result<Located, Status> {
    let mut folder_without_python: Option<&PathBuf> = None;
    for folder in candidates.iter().filter(|f| is_engine_folder(f)) {
        if let Some(python) = find_python(folder, override_python) {
            return Ok(Located { folder: folder.clone(), python });
        }
        folder_without_python.get_or_insert(folder);
    }
    Err(match folder_without_python {
        Some(folder) => Status::NotFound {
            message: format!(
                "GladTalker is in {}, but its Python environment isn't set up yet (no venv-dml, venv, or .venv folder). \
                 Follow the installation steps in its README, then choose Try again.",
                folder.display()
            ),
            folder: Some(folder.display().to_string()),
        },
        None => Status::NotFound {
            message: "GladTalker Studio needs the GladTalker folder (the one with studio_server.py and its Python \
                      environment). Choose where it is on this computer."
                .into(),
            folder: None,
        },
    })
}

pub fn workspace_of(folder: &Path) -> PathBuf {
    folder.join("results").join("studio")
}

/// Is `path` a file inside the engine's workspace? Used before revealing a file in the file
/// manager, so the UI can only point at what the engine itself wrote.
pub fn is_inside(workspace: &Path, path: &Path) -> bool {
    match (fs::canonicalize(workspace), fs::canonicalize(path)) {
        (Ok(ws), Ok(p)) => p.starts_with(&ws) && p != ws,
        _ => false,
    }
}

/// Parses the engine's ready line: `{"event": "listening", "port": 54321, ...}`.
pub fn parse_ready_line(line: &str) -> Option<u16> {
    let value: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if value.get("event")?.as_str()? != "listening" {
        return None;
    }
    u16::try_from(value.get("port")?.as_u64()?).ok()
}

pub fn new_token() -> String {
    format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple())
}

/// Keeps the last lines of the engine's output for error messages, and writes all of it to a log
/// file.
#[derive(Clone)]
struct LogSink {
    tail: Arc<Mutex<VecDeque<String>>>,
    file: Arc<Mutex<Option<fs::File>>>,
}

impl LogSink {
    fn new(path: &Path) -> Self {
        if let Some(dir) = path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        LogSink {
            tail: Arc::new(Mutex::new(VecDeque::new())),
            file: Arc::new(Mutex::new(fs::File::create(path).ok())),
        }
    }

    fn push(&self, line: &str) {
        let mut tail = self.tail.lock().unwrap();
        if tail.len() == LOG_TAIL_LINES {
            tail.pop_front();
        }
        tail.push_back(line.to_string());
        drop(tail);
        if let Some(f) = self.file.lock().unwrap().as_mut() {
            let _ = writeln!(f, "{line}");
        }
    }

    fn text(&self) -> String {
        self.tail.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n")
    }
}

pub struct Running {
    child: Child,
    pub status: Status,
}

impl Running {
    pub fn is_alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }
}

/// Starts the engine and waits until it is accepting connections (or fails).
pub fn start(found: &Located, log_path: &Path) -> Result<Running, Status> {
    let token = new_token();
    let workspace = workspace_of(&found.folder);
    let mut cmd = Command::new(&found.python);
    cmd.arg("studio_server.py")
        .args(["--port", "0", "--log-level", "warning", "--token", &token])
        .args(["--exit-with-parent", &std::process::id().to_string()])
        .arg("--workspace")
        .arg(&workspace)
        .current_dir(&found.folder)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env_remove("GLADTALKER_STUDIO_TOKEN")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let failed = |message: String, log: String| Status::Failed {
        message,
        folder: found.folder.display().to_string(),
        python: found.python.display().to_string(),
        log,
        log_path: log_path.display().to_string(),
    };

    let log = LogSink::new(log_path);
    log.push(&format!("Starting {} studio_server.py in {}", found.python.display(), found.folder.display()));
    let mut child = cmd
        .spawn()
        .map_err(|e| failed(format!("Couldn't start Python ({}): {e}", found.python.display()), log.text()))?;

    // Read both streams to the end on their own threads (a full pipe would stall the engine).
    let (ready_tx, ready_rx) = mpsc::channel::<u16>();
    let stdout = child.stdout.take().expect("piped");
    let stderr = child.stderr.take().expect("piped");
    let out_log = log.clone();
    thread::spawn(move || {
        let mut announced = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !announced {
                if let Some(port) = parse_ready_line(&line) {
                    announced = true;
                    let _ = ready_tx.send(port);
                    continue;
                }
            }
            out_log.push(&line);
        }
    });
    let err_log = log.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            err_log.push(&line);
        }
    });

    let deadline = std::time::Instant::now() + START_TIMEOUT;
    loop {
        match ready_rx.recv_timeout(Duration::from_millis(250)) {
            Ok(port) => {
                let status = Status::Ready {
                    base: format!("http://127.0.0.1:{port}"),
                    token,
                    folder: found.folder.display().to_string(),
                    python: found.python.display().to_string(),
                    workspace: workspace.display().to_string(),
                };
                return Ok(Running { child, status });
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {}
        }
        if let Ok(Some(code)) = child.try_wait() {
            thread::sleep(Duration::from_millis(200)); // let the readers catch the last lines
            return Err(failed(format!("The engine stopped while starting ({code})."), log.text()));
        }
        if std::time::Instant::now() > deadline {
            stop(&mut child);
            return Err(failed(
                format!("The engine didn't start within {} seconds.", START_TIMEOUT.as_secs()),
                log.text(),
            ));
        }
    }
}

/// Stops the engine and everything it started. On Windows a venv's python.exe is a launcher
/// whose child is the real interpreter, so the whole process tree is ended.
pub fn stop(child: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

impl Drop for Running {
    fn drop(&mut self) {
        stop(&mut self.child);
    }
}

pub fn load_choice(path: &Path) -> SavedChoice {
    fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

pub fn save_choice(path: &Path, choice: &SavedChoice) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    fs::write(path, serde_json::to_string_pretty(choice)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn engine_folder(root: &Path, venv: Option<&str>) -> PathBuf {
        fs::create_dir_all(root).unwrap();
        fs::write(root.join("studio_server.py"), "").unwrap();
        if let Some(name) = venv {
            let py = venv_python(&root.join(name));
            fs::create_dir_all(py.parent().unwrap()).unwrap();
            fs::write(&py, "").unwrap();
        }
        root.to_path_buf()
    }

    #[test]
    fn prefers_the_directml_venv_then_venv_then_dot_venv() {
        let tmp = tempfile::tempdir().unwrap();
        let folder = engine_folder(&tmp.path().join("gt"), Some(".venv"));
        assert!(find_python(&folder, None).unwrap().starts_with(folder.join(".venv")));
        engine_folder(&folder, Some("venv"));
        assert!(find_python(&folder, None).unwrap().starts_with(folder.join("venv")));
        engine_folder(&folder, Some("venv-dml"));
        assert!(find_python(&folder, None).unwrap().starts_with(folder.join("venv-dml")));
    }

    #[test]
    fn an_explicit_python_wins_but_must_exist() {
        let tmp = tempfile::tempdir().unwrap();
        let folder = engine_folder(&tmp.path().join("gt"), Some("venv"));
        let custom = tmp.path().join("python-custom");
        assert_eq!(find_python(&folder, Some(&custom)), None);
        fs::write(&custom, "").unwrap();
        assert_eq!(find_python(&folder, Some(&custom)), Some(custom));
    }

    #[test]
    fn locates_the_first_usable_candidate_and_explains_what_is_missing() {
        let tmp = tempfile::tempdir().unwrap();
        let no_python = engine_folder(&tmp.path().join("a"), None);
        let usable = engine_folder(&tmp.path().join("b"), Some("venv"));
        let not_engine = tmp.path().join("c");
        fs::create_dir_all(&not_engine).unwrap();

        let found = locate(&[not_engine.clone(), no_python.clone(), usable.clone()], None).unwrap();
        assert_eq!(found.folder, usable);

        match locate(&[not_engine.clone(), no_python.clone()], None) {
            Err(Status::NotFound { message, folder }) => {
                assert!(message.contains("Python environment isn't set up"), "{message}");
                assert_eq!(folder, Some(no_python.display().to_string()));
            }
            other => panic!("{other:?}"),
        }
        match locate(&[not_engine], None) {
            Err(Status::NotFound { folder: None, message }) => assert!(message.contains("Choose where it is")),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn candidates_are_home_then_saved_then_folders_around_the_app() {
        let exe = PathBuf::from("/x/gt/studio-ui/src-tauri/target/debug/app.exe");
        let c = candidate_folders(Some("/home".into()), Some("/saved".into()), Some(exe));
        assert_eq!(c[0], PathBuf::from("/home"));
        assert_eq!(c[1], PathBuf::from("/saved"));
        assert!(c.contains(&PathBuf::from("/x/gt")));
        assert!(!c.contains(&PathBuf::from("/x/gt/studio-ui/src-tauri/target/debug/app.exe")));
    }

    #[test]
    fn reads_only_the_listening_line() {
        assert_eq!(parse_ready_line(r#"{"event": "listening", "host": "127.0.0.1", "port": 54321}"#), Some(54321));
        assert_eq!(parse_ready_line(r#"{"event": "other", "port": 1}"#), None);
        assert_eq!(parse_ready_line(r#"{"event": "listening", "port": 70000}"#), None);
        assert_eq!(parse_ready_line("INFO: Started server"), None);
    }

    #[test]
    fn tokens_are_long_random_and_url_safe() {
        let (a, b) = (new_token(), new_token());
        assert_eq!(a.len(), 64);
        assert_ne!(a, b);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn only_files_inside_the_workspace_can_be_revealed() {
        let tmp = tempfile::tempdir().unwrap();
        let ws = workspace_of(tmp.path());
        let video = ws.join("renders").join("x").join("out.mp4");
        fs::create_dir_all(video.parent().unwrap()).unwrap();
        fs::write(&video, "").unwrap();
        let outside = tmp.path().join("secret.txt");
        fs::write(&outside, "").unwrap();

        assert!(is_inside(&ws, &video));
        assert!(!is_inside(&ws, &outside));
        assert!(!is_inside(&ws, &ws.join("renders").join("..").join("..").join("..").join("secret.txt")));
        assert!(!is_inside(&ws, &ws));
        assert!(!is_inside(&ws, &ws.join("missing.mp4")));
    }

    #[test]
    fn the_saved_choice_round_trips() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("cfg").join("engine.json");
        assert_eq!(load_choice(&path), SavedChoice::default());
        let choice = SavedChoice { folder: Some(tmp.path().to_path_buf()) };
        save_choice(&path, &choice).unwrap();
        assert_eq!(load_choice(&path), choice);
    }
}
