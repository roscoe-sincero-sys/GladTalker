//! Finding, starting, and stopping the local GladTalker engine (`studio_server.py`).
//!
//! The desktop app doesn't bundle Python. It uses a GladTalker folder that already has a Python
//! environment set up (see the README), which it finds in this order:
//!
//! 1. the folder just chosen with "Choose GladTalker folder…" (this session), so an explicit
//!    choice always wins, even over a stale `GLADTALKER_HOME`;
//! 2. the `GLADTALKER_HOME` environment variable;
//! 3. the folder chosen in an earlier session (saved in the app's config);
//! 4. a GladTalker folder that contains the app (a development build, or the app copied there).
//!
//! Inside it, Python is `GLADTALKER_PYTHON` if set, else the first of `venv-dml`, `venv`, `.venv`
//! that exists. The engine is started on a free port with a fresh random token, and told to shut
//! itself down if the app goes away (`--exit-with-parent`). The token is passed in the
//! environment (`GLADTALKER_STUDIO_TOKEN`), never on the command line, which other programs on
//! the computer can read.

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
    /// Running and accepting requests at `base` with `token`. `note`: something the user should
    /// know that doesn't stop them (say, the chosen folder couldn't be remembered).
    Ready { base: String, token: String, folder: String, python: String, workspace: String, note: Option<String> },
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
pub fn candidate_folders(
    chosen: Option<PathBuf>,
    home: Option<PathBuf>,
    saved: Option<PathBuf>,
    exe: Option<PathBuf>,
) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    out.extend(chosen);
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

/// The engine's workspace as one absolute path, used both on the engine's command line and for
/// the shell's own checks (Reveal). A relative override is resolved against `base`, the app's
/// working directory at launch: the engine runs in its own folder, so passing it a relative path
/// would make the two processes disagree about where the workspace is.
pub fn resolve_workspace(override_path: Option<PathBuf>, base: &Path, folder: &Path) -> PathBuf {
    match override_path.filter(|p| !p.as_os_str().is_empty()) {
        Some(p) if p.is_absolute() => p,
        Some(p) => base.join(p),
        None => workspace_of(folder),
    }
}

/// Is `path` a file inside the engine's workspace? Used before revealing a file in the file
/// manager, so the UI can only point at what the engine itself wrote.
pub fn is_inside(workspace: &Path, path: &Path) -> bool {
    match (fs::canonicalize(workspace), fs::canonicalize(path)) {
        (Ok(ws), Ok(p)) => p.starts_with(&ws) && p != ws,
        _ => false,
    }
}

/// The only address the desktop app's engine may listen on.
pub const ENGINE_HOST: &str = "127.0.0.1";

/// Parses the engine's ready line: `{"event": "listening", "host": "127.0.0.1", "port": 54321, ...}`.
pub fn parse_ready_line(line: &str) -> Option<(String, u16)> {
    let value: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if value.get("event")?.as_str()? != "listening" {
        return None;
    }
    let port = u16::try_from(value.get("port")?.as_u64()?).ok()?;
    Some((value.get("host")?.as_str()?.to_string(), port))
}

/// The engine's command line and environment. The token travels in the environment (process
/// lists show command lines); the address is fixed to 127.0.0.1 whatever the user's web-mode
/// settings are; `extra_origins` is the dev server's origin in development builds.
pub fn engine_command(found: &Located, workspace: &Path, token: &str, extra_origins: &[String]) -> Command {
    let mut cmd = Command::new(&found.python);
    cmd.arg("studio_server.py")
        .args(["--host", ENGINE_HOST, "--port", "0", "--log-level", "warning"])
        .args(["--exit-with-parent", &std::process::id().to_string()])
        .arg("--workspace")
        .arg(workspace);
    for origin in extra_origins {
        cmd.args(["--allow-origin", origin]);
    }
    cmd.current_dir(&found.folder)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("GLADTALKER_STUDIO_TOKEN", token)
        .env_remove("GLADTALKER_STUDIO_HOST")
        .env_remove("GLADTALKER_STUDIO_PORT");
    cmd
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
pub fn start(found: &Located, log_path: &Path, workspace: &Path, extra_origins: &[String]) -> Result<Running, Status> {
    let token = new_token();
    let mut cmd = engine_command(found, workspace, &token, extra_origins);
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        // Its own process group, so stop() can end the engine and anything it started (FFmpeg).
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
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
    let (ready_tx, ready_rx) = mpsc::channel::<(String, u16)>();
    let stdout = child.stdout.take().expect("piped");
    let stderr = child.stderr.take().expect("piped");
    let out_log = log.clone();
    thread::spawn(move || {
        let mut announced = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !announced {
                if let Some(address) = parse_ready_line(&line) {
                    announced = true;
                    let _ = ready_tx.send(address);
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
            Ok((host, port)) => {
                if host != ENGINE_HOST {
                    stop(&mut child);
                    return Err(failed(
                        format!("The engine listened on {host} instead of {ENGINE_HOST}, so it was stopped."),
                        log.text(),
                    ));
                }
                let status = Status::Ready {
                    base: format!("http://{ENGINE_HOST}:{port}"),
                    token,
                    folder: found.folder.display().to_string(),
                    python: found.python.display().to_string(),
                    workspace: workspace.display().to_string(),
                    note: None,
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
/// whose child is the real interpreter, so the whole process tree is ended; elsewhere the
/// engine's process group is.
pub fn stop(child: &mut Child) {
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .args(["-KILL", "--", &format!("-{}", child.id())])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
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
    crate::safe_write::write_atomically(path, serde_json::to_string_pretty(choice)?.as_bytes())
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
    fn candidates_are_chosen_then_home_then_saved_then_folders_around_the_app() {
        let exe = PathBuf::from("/x/gt/studio-ui/src-tauri/target/debug/app.exe");
        let c = candidate_folders(Some("/chosen".into()), Some("/home".into()), Some("/saved".into()), Some(exe.clone()));
        assert_eq!(c[0], PathBuf::from("/chosen")); // what the user just picked beats GLADTALKER_HOME
        assert_eq!(c[1], PathBuf::from("/home"));
        assert_eq!(c[2], PathBuf::from("/saved"));
        assert_eq!(candidate_folders(None, Some("/home".into()), None, Some(exe))[0], PathBuf::from("/home"));
        assert!(c.contains(&PathBuf::from("/x/gt")));
        assert!(!c.contains(&PathBuf::from("/x/gt/studio-ui/src-tauri/target/debug/app.exe")));
    }

    #[test]
    fn reads_only_the_listening_line() {
        assert_eq!(parse_ready_line(r#"{"event": "listening", "host": "127.0.0.1", "port": 54321}"#),
            Some(("127.0.0.1".to_string(), 54321)));
        assert_eq!(parse_ready_line(r#"{"event": "listening", "host": "::1", "port": 1}"#), Some(("::1".to_string(), 1)));
        assert_eq!(parse_ready_line(r#"{"event": "other", "host": "127.0.0.1", "port": 1}"#), None);
        assert_eq!(parse_ready_line(r#"{"event": "listening", "host": "127.0.0.1", "port": 70000}"#), None);
        assert_eq!(parse_ready_line("INFO: Started server"), None);
    }

    fn arg_list(cmd: &Command) -> Vec<String> {
        cmd.get_args().map(|a| a.to_string_lossy().into_owned()).collect()
    }

    #[test]
    fn the_engine_is_pinned_to_loopback_and_never_gets_the_token_on_its_command_line() {
        let found = Located { folder: PathBuf::from("/gt"), python: PathBuf::from("/gt/venv/bin/python") };
        let cmd = engine_command(&found, Path::new("/ws"), "secret-token", &[]);
        let args = arg_list(&cmd);
        let host_at = args.iter().position(|a| a == "--host").expect("--host");
        assert_eq!(args[host_at + 1], ENGINE_HOST);
        assert!(!args.iter().any(|a| a.contains("secret-token") || a == "--token"), "{args:?}");
        assert!(!args.iter().any(|a| a == "--allow-origin"));
        let envs: Vec<_> = cmd.get_envs().map(|(k, v)| (k.to_string_lossy().into_owned(), v.map(|v| v.to_string_lossy().into_owned()))).collect();
        assert!(envs.contains(&("GLADTALKER_STUDIO_TOKEN".into(), Some("secret-token".into()))));
        // A web-mode bind address set by the user (say ::1 or 0.0.0.0) is not inherited.
        assert!(envs.contains(&("GLADTALKER_STUDIO_HOST".into(), None)));
        assert!(envs.contains(&("GLADTALKER_STUDIO_PORT".into(), None)));
    }

    #[test]
    fn development_builds_allow_the_dev_server_origin() {
        let found = Located { folder: PathBuf::from("/gt"), python: PathBuf::from("/gt/venv/bin/python") };
        let args = arg_list(&engine_command(&found, Path::new("/ws"), "t", &["http://localhost:5173".into()]));
        let at = args.iter().position(|a| a == "--allow-origin").expect("--allow-origin");
        assert_eq!(args[at + 1], "http://localhost:5173");
    }

    #[test]
    fn a_relative_workspace_override_is_made_absolute_once_for_shell_and_engine() {
        let base = Path::new(if cfg!(windows) { r"C:\launch\here" } else { "/launch/here" });
        let folder = Path::new(if cfg!(windows) { r"C:\GladTalker" } else { "/GladTalker" });
        let relative = resolve_workspace(Some(PathBuf::from("ws")), base, folder);
        assert_eq!(relative, base.join("ws"));
        assert!(relative.is_absolute());
        let absolute = folder.join("elsewhere");
        assert_eq!(resolve_workspace(Some(absolute.clone()), base, folder), absolute);
        assert_eq!(resolve_workspace(None, base, folder), workspace_of(folder));
        assert_eq!(resolve_workspace(Some(PathBuf::new()), base, folder), workspace_of(folder));

        // The engine gets that same absolute path (it runs in its own folder, not the app's).
        let found = Located { folder: folder.to_path_buf(), python: folder.join("python") };
        let cmd = engine_command(&found, &relative, "t", &[]);
        let args: Vec<_> = cmd.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
        let at = args.iter().position(|a| a == "--workspace").expect("--workspace");
        assert_eq!(Path::new(&args[at + 1]), relative);
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
