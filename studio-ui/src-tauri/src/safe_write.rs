//! Saving a file so that it is either fully replaced or left as it was, without loosening who may
//! read it, and without ever deleting the only complete copy of either version.
//!
//! The bytes go to a temporary ("staging") file next to the destination, which then takes its
//! place. When the destination already exists, its access restrictions are kept:
//! - **Windows:** the staging file is created with no sharing (no other handle can open it while
//!   it is written) and an owner-only DACL in the creating call itself; the destination's DACL
//!   (protected or inherited, as it was) is applied before any byte is written. `ReplaceFileW`
//!   then swaps it in, with a backup name in the same folder, keeping the replaced file's DACL,
//!   attributes, and creation time.
//! - **Unix:** the staging file is created owner-only (0600), then given the destination's
//!   permission bits and group; if its group can't be matched, the group and other bits are
//!   dropped rather than shared with a different group. A rename replaces the destination.
//!
//! A new file gets the folder's defaults, as any newly created file does.
//!
//! If the replacement fails partway (ReplaceFileW documents a state where the original has been
//! moved to the backup name), [`recover`] looks at what is actually on disk. It puts the previous
//! version back only into a free name (never over a file someone else saved meanwhile), deletes
//! the staging file only once that has worked or nothing was moved, and otherwise keeps both
//! files and says where they are.
//!
//! On Windows every path handed to the Win32 calls is a full extended-length path (`\\?\C:\…`,
//! `\\?\UNC\…`), so a project in a deeply nested folder saves even with long paths disabled; the
//! staging and backup names are short and don't grow with the project's name.

use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// A failed save, with what became of the files.
#[derive(Debug)]
pub struct SaveError {
    pub source: io::Error,
    /// None: the destination is exactly as it was before (or still absent).
    /// Some: what happened instead, and where each version is.
    pub recovery: Option<String>,
}

impl fmt::Display for SaveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.recovery {
            Some(recovery) => write!(f, "{}. {recovery}.", self.source),
            None => write!(f, "{}. The previous file, if any, is unchanged.", self.source),
        }
    }
}

impl From<SaveError> for io::Error {
    fn from(e: SaveError) -> Self {
        io::Error::new(e.source.kind(), e.to_string())
    }
}

pub fn write_atomically(path: &Path, contents: &[u8]) -> Result<(), SaveError> {
    write_atomically_with(path, |file, _| io::Write::write_all(file, contents))
}

/// Like [`write_atomically`], with the writing done by `write` (given the staging file and its
/// path; the tests use it to fail halfway and to inspect the staging file).
pub fn write_atomically_with(
    path: &Path,
    write: impl FnOnce(&mut fs::File, &Path) -> io::Result<()>,
) -> Result<(), SaveError> {
    write_with_replacer(path, write, platform::replace)
}

/// The whole save, with the final swap (`replace(staging, destination, backup)`) injectable so
/// the tests can reproduce each documented partial failure.
fn write_with_replacer(
    path: &Path,
    write: impl FnOnce(&mut fs::File, &Path) -> io::Result<()>,
    replace: impl FnOnce(&Path, &Path, Option<&Path>) -> io::Result<()>,
) -> Result<(), SaveError> {
    let existing = fs::metadata(path).is_ok_and(|m| m.is_file());
    let tmp = sibling(path, "tmp");
    let staged = (|| {
        let mut file = platform::create_staging(&tmp, existing.then_some(path))?;
        write(&mut file, &tmp)?;
        file.sync_all()
    })();
    if let Err(source) = staged {
        let _ = fs::remove_file(&tmp); // only ever holds the incomplete new contents
        return Err(SaveError { source, recovery: None });
    }
    let backup = existing.then(|| sibling(path, "bak"));
    match replace(&tmp, path, backup.as_deref()) {
        Ok(()) => {
            if let Some(backup) = &backup {
                let _ = fs::remove_file(backup); // the previous version, now replaced
            }
            Ok(())
        }
        Err(source) => Err(recover(source, &tmp, path, backup.as_deref(), existing)),
    }
}

/// After a failed swap, works out from the files themselves what is left. The previous version
/// is put back under its name only if that name is free (a no-replace move), so a save that
/// landed meanwhile from another window or program is never undone. The staging file is deleted
/// only when the previous version is in place again or was never moved; otherwise both files are
/// kept and named.
fn recover(source: io::Error, tmp: &Path, path: &Path, backup: Option<&Path>, existed: bool) -> SaveError {
    let is_file = |p: &Path| fs::symlink_metadata(p).is_ok_and(|m| m.is_file());
    let staged = is_file(tmp);
    let moved_aside = backup.filter(|b| is_file(b));
    let recovery = match (moved_aside, staged) {
        // Nothing was moved and the new contents weren't swapped in: what is at `path` (the
        // previous version, or someone else's newer save) isn't ours to touch.
        (None, true) if !existed || is_file(path) => {
            let _ = fs::remove_file(tmp);
            None
        }
        (None, true) => Some(format!(
            "{} is no longer there. The new project is at {}",
            path.display(),
            tmp.display()
        )),
        // The previous version was moved aside: put it back, but only into a free name.
        (Some(b), true) => match platform::restore(b, path) {
            Ok(()) => {
                let _ = fs::remove_file(tmp);
                None
            }
            Err(_) if is_file(path) => Some(format!(
                "{} was saved by something else meanwhile, so it was left as it is. Your new project is at {}, \
                 and the previous version is at {}",
                path.display(),
                tmp.display(),
                b.display()
            )),
            Err(e) => Some(format!(
                "The previous version couldn't be put back ({e}). Your new project is at {}, and the previous \
                 version is at {}",
                tmp.display(),
                b.display()
            )),
        },
        // The staging file is gone: it was moved, presumably into place. Keep what remains.
        (Some(b), false) => Some(format!(
            "The new project may have been saved as {}; the previous version was kept at {}",
            path.display(),
            b.display()
        )),
        (None, false) => Some(format!(
            "The new project may have been saved as {}; check that it is the version you expect",
            path.display()
        )),
    };
    SaveError { source, recovery }
}

/// A unique, short name next to `path`, e.g. `.gladtalker-1a2b-5f3e9c1d0.tmp`. It doesn't include
/// the project's name, so it never pushes a long name past a path or name length limit.
fn sibling(path: &Path, kind: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.subsec_nanos()).unwrap_or(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    path.with_file_name(format!(".gladtalker-{:x}-{nanos:x}{n:x}.{kind}", std::process::id()))
}

#[cfg(windows)]
mod platform {
    use std::fs;
    use std::io;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{FromRawHandle, RawHandle};
    use std::path::Path;
    use std::ptr::{null, null_mut};

    use windows_sys::Win32::Foundation::{LocalFree, ERROR_FILE_NOT_FOUND, ERROR_SUCCESS, GENERIC_WRITE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Security::Authorization::{
        ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW, SetNamedSecurityInfoW, SDDL_REVISION_1,
        SE_FILE_OBJECT,
    };
    use windows_sys::Win32::Security::{
        GetSecurityDescriptorControl, ACL, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
        PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES, SE_DACL_PROTECTED, UNPROTECTED_DACL_SECURITY_INFORMATION,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        CreateFileW, MoveFileExW, ReplaceFileW, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_NONE, READ_CONTROL,
        WRITE_DAC,
    };

    /// Owner only, nothing inherited from the folder: the staging file's DACL from the moment it
    /// exists, until the destination's own DACL replaces it.
    pub(super) const OWNER_ONLY: &str = "D:P(A;;FA;;;OW)";

    /// `path` for a Win32 call: full, NUL-terminated, and extended-length (`\\?\C:\…` or
    /// `\\?\UNC\server\share\…`), so it isn't limited to MAX_PATH when long paths are off.
    pub(super) fn wide(path: &Path) -> Vec<u16> {
        let full = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
        let units: Vec<u16> = full.as_os_str().encode_wide().collect();
        let utf16 = |s: &str| s.encode_utf16().collect::<Vec<u16>>();
        let mut out = if units.starts_with(&utf16(r"\\?\")) || units.starts_with(&utf16(r"\\.\")) {
            units // already extended-length, or a device path
        } else if units.starts_with(&utf16(r"\\")) {
            [utf16(r"\\?\UNC\"), units[2..].to_vec()].concat()
        } else if units.len() >= 3 && units[1] == u16::from(b':') && units[2] == u16::from(b'\\') {
            [utf16(r"\\?\"), units].concat()
        } else {
            units
        };
        out.push(0);
        out
    }

    /// Creates the staging file with no sharing, so no other handle can open it while this one
    /// is open. With `restrict`, it is also owner-only from creation (the DACL is part of the
    /// CreateFileW call, so no broader handle can ever be granted).
    pub(super) fn create_exclusive(tmp: &Path, restrict: bool) -> io::Result<fs::File> {
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        if restrict {
            let sddl: Vec<u16> = OWNER_ONLY.encode_utf16().chain(Some(0)).collect();
            // SAFETY: NUL-terminated SDDL; `sd` is freed with LocalFree below.
            if unsafe { ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), SDDL_REVISION_1, &mut sd, null_mut()) } == 0 {
                return Err(io::Error::last_os_error());
            }
        }
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: sd,
            bInheritHandle: 0,
        };
        // SAFETY: valid NUL-terminated path and attributes; the handle is owned by the File below.
        let handle = unsafe {
            CreateFileW(wide(tmp).as_ptr(), GENERIC_WRITE | WRITE_DAC | READ_CONTROL, FILE_SHARE_NONE,
                if restrict { &attributes } else { null() }, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, null_mut())
        };
        let created = if handle == INVALID_HANDLE_VALUE { Err(io::Error::last_os_error()) } else { Ok(()) };
        if !sd.is_null() {
            // SAFETY: allocated by ConvertStringSecurityDescriptorToSecurityDescriptorW.
            unsafe { LocalFree(sd) };
        }
        created?;
        // SAFETY: a valid handle we own and nothing else refers to.
        Ok(unsafe { fs::File::from_raw_handle(handle as RawHandle) })
    }

    /// The staging file for a save: exclusive, and with `like`'s DACL in place before anything is
    /// written (owner-only until then).
    pub(super) fn create_staging(tmp: &Path, like: Option<&Path>) -> io::Result<fs::File> {
        let file = create_exclusive(tmp, like.is_some())?;
        if let Some(like) = like {
            if let Err(e) = copy_dacl(like, tmp) {
                drop(file);
                let _ = fs::remove_file(tmp);
                return Err(io::Error::new(e.kind(), format!("couldn't apply the existing file's permissions: {e}")));
            }
        }
        Ok(file)
    }

    fn copy_dacl(from: &Path, to: &Path) -> io::Result<()> {
        let mut dacl: *mut ACL = null_mut();
        let mut sd: PSECURITY_DESCRIPTOR = null_mut();
        // SAFETY: valid NUL-terminated path; out-pointers are valid; `sd` is freed with LocalFree.
        let err = unsafe {
            GetNamedSecurityInfoW(wide(from).as_ptr(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                null_mut(), null_mut(), &mut dacl, null_mut(), &mut sd)
        };
        if err != ERROR_SUCCESS {
            return Err(io::Error::from_raw_os_error(err as i32));
        }
        let result = (|| {
            if dacl.is_null() {
                return Ok(()); // no DACL (e.g. FAT): nothing to keep
            }
            let (mut control, mut revision) = (0u16, 0u32);
            // SAFETY: `sd` came from GetNamedSecurityInfoW.
            if unsafe { GetSecurityDescriptorControl(sd, &mut control, &mut revision) } == 0 {
                return Err(io::Error::last_os_error());
            }
            let inheritance = if control & SE_DACL_PROTECTED != 0 {
                PROTECTED_DACL_SECURITY_INFORMATION
            } else {
                UNPROTECTED_DACL_SECURITY_INFORMATION
            };
            // SAFETY: valid path; `dacl` points into `sd`, which is still alive. (Setting a DACL
            // isn't data access, so the staging file's no-sharing handle doesn't block it.)
            let err = unsafe {
                SetNamedSecurityInfoW(wide(to).as_ptr(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | inheritance,
                    null_mut(), null_mut(), dacl, null())
            };
            if err == ERROR_SUCCESS { Ok(()) } else { Err(io::Error::from_raw_os_error(err as i32)) }
        })();
        // SAFETY: allocated by GetNamedSecurityInfoW.
        unsafe { LocalFree(sd) };
        result
    }

    /// Moves the previous version back to `path`, only if nothing is there (no MOVEFILE_REPLACE_EXISTING).
    pub(super) fn restore(backup: &Path, path: &Path) -> io::Result<()> {
        // SAFETY: valid NUL-terminated paths.
        if unsafe { MoveFileExW(wide(backup).as_ptr(), wide(path).as_ptr(), 0) } != 0 {
            Ok(())
        } else {
            Err(io::Error::last_os_error())
        }
    }

    /// Puts `tmp` in `path`'s place. An existing file is replaced with ReplaceFileW (keeping its
    /// DACL, attributes, and creation time), moving it aside to `backup` on the way, so a partial
    /// failure always leaves it somewhere `recover` can find it. A new file is simply renamed.
    pub(super) fn replace(tmp: &Path, path: &Path, backup: Option<&Path>) -> io::Result<()> {
        if let Some(backup) = backup {
            // SAFETY: valid NUL-terminated paths; reserved pointers are null.
            let ok = unsafe { ReplaceFileW(wide(path).as_ptr(), wide(tmp).as_ptr(), wide(backup).as_ptr(), 0, null(), null()) };
            if ok != 0 {
                return Ok(());
            }
            let err = io::Error::last_os_error();
            if err.raw_os_error() != Some(ERROR_FILE_NOT_FOUND as i32) || fs::metadata(path).is_ok() {
                return Err(err);
            }
            // The file was removed meanwhile: there is nothing left to keep, so a rename will do.
        }
        fs::rename(tmp, path)
    }
}

#[cfg(unix)]
mod platform {
    use std::fs;
    use std::io;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
    use std::path::Path;

    /// Creates the staging file owner-only, then gives it `like`'s permission bits and group
    /// (or, if the group can't be matched, only its owner bits), before anything is written.
    pub(super) fn create_staging(tmp: &Path, like: Option<&Path>) -> io::Result<fs::File> {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        if like.is_some() {
            options.mode(0o600);
        }
        let file = options.open(tmp)?;
        if let Some(like) = like {
            let applied = (|| {
                let meta = fs::metadata(like)?;
                let mut mode = meta.mode() & 0o7777;
                if std::os::unix::fs::fchown(&file, None, Some(meta.gid())).is_err() {
                    mode &= 0o700; // a different group must not get the original group's access
                }
                file.set_permissions(fs::Permissions::from_mode(mode))
            })();
            if let Err(e) = applied {
                drop(file);
                let _ = fs::remove_file(tmp);
                return Err(e);
            }
        }
        Ok(file)
    }

    /// Moves the previous version back to `path`, only if nothing is there: link(2) refuses an
    /// existing name, unlike rename(2).
    pub(super) fn restore(backup: &Path, path: &Path) -> io::Result<()> {
        fs::hard_link(backup, path)?;
        fs::remove_file(backup)
    }

    /// rename(2) replaces the destination atomically: it is never missing, so no backup is needed.
    pub(super) fn replace(tmp: &Path, path: &Path, _backup: Option<&Path>) -> io::Result<()> {
        fs::rename(tmp, path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn names(dir: &Path) -> Vec<String> {
        let mut v: Vec<_> = fs::read_dir(dir).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        v.sort();
        v
    }

    #[test]
    fn saving_replaces_the_file_whole_or_not_at_all() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("scene.gladtalker.json");
        fs::write(&path, b"original project").unwrap();

        // A write that fails halfway leaves the original bytes, and no temporary file.
        let err = write_atomically_with(&path, |f, _| {
            f.write_all(b"half of the new")?;
            Err(io::Error::other("disk full"))
        })
        .unwrap_err();
        assert!(err.recovery.is_none());
        assert!(err.to_string().ends_with("The previous file, if any, is unchanged."));
        assert_eq!(fs::read(&path).unwrap(), b"original project");
        assert_eq!(names(tmp.path()), ["scene.gladtalker.json"], "the temporary file was removed");

        write_atomically(&path, b"new project").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"new project");
        assert_eq!(names(tmp.path()), ["scene.gladtalker.json"], "no temporary or backup file is left");
        write_atomically(&tmp.path().join("fresh.json"), b"{}").unwrap(); // a new file works too
        assert_eq!(fs::read(tmp.path().join("fresh.json")).unwrap(), b"{}");
    }

    /// Each documented partial failure of the swap, reproduced at the replacement boundary.
    mod replacement_failures {
        use super::*;

        fn setup() -> (tempfile::TempDir, PathBuf) {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("scene.gladtalker.json");
            fs::write(&path, b"previous version").unwrap();
            (dir, path)
        }

        fn new_contents(f: &mut fs::File, _: &Path) -> io::Result<()> {
            f.write_all(b"new version")
        }

        fn with_contents(dir: &Path, contents: &[u8]) -> Vec<String> {
            names(dir).into_iter().filter(|n| fs::read(dir.join(n)).is_ok_and(|c| c == contents)).collect()
        }

        #[test]
        fn nothing_moved_1176_with_a_backup_name() {
            // ERROR_UNABLE_TO_MOVE_REPLACEMENT with a backup name: both files keep their names.
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |_, _, backup| {
                assert!(backup.is_some(), "an existing file is always replaced with a backup name");
                Err(io::Error::other("unable to move replacement (1176)"))
            })
            .unwrap_err();
            assert!(err.recovery.is_none(), "{err}");
            assert_eq!(fs::read(&path).unwrap(), b"previous version");
            assert_eq!(names(dir.path()), ["scene.gladtalker.json"]);
        }

        #[test]
        fn original_moved_aside_1177_is_put_back() {
            // ERROR_UNABLE_TO_MOVE_REPLACEMENT_2: the original now has the backup name.
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |_, path, backup| {
                fs::rename(path, backup.unwrap())?;
                Err(io::Error::other("unable to move replacement (1177)"))
            })
            .unwrap_err();
            assert!(err.recovery.is_none(), "{err}");
            assert!(err.to_string().ends_with("The previous file, if any, is unchanged."));
            assert_eq!(fs::read(&path).unwrap(), b"previous version");
            assert_eq!(names(dir.path()), ["scene.gladtalker.json"]);
        }

        #[test]
        fn if_the_original_cant_be_put_back_both_versions_are_kept_and_named() {
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |_, path, backup| {
                fs::rename(path, backup.unwrap())?;
                fs::create_dir(path)?; // something now blocks the original's name
                Err(io::Error::other("unable to move replacement (1177)"))
            })
            .unwrap_err();
            let message = err.to_string();
            let new = with_contents(dir.path(), b"new version");
            let old = with_contents(dir.path(), b"previous version");
            assert_eq!((new.len(), old.len()), (1, 1), "a complete copy of each version survives");
            assert!(message.contains(&dir.path().join(&new[0]).display().to_string()), "{message}");
            assert!(message.contains(&dir.path().join(&old[0]).display().to_string()), "{message}");
        }

        #[test]
        fn a_competing_save_that_lands_meanwhile_is_never_undone() {
            // 1177: the original was moved to the backup name, and before recovery another
            // window or editor saves its own version under the project's name.
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |_, path, backup| {
                fs::rename(path, backup.unwrap())?;
                fs::write(path, b"someone else's save")?;
                Err(io::Error::other("unable to move replacement (1177)"))
            })
            .unwrap_err();
            assert_eq!(fs::read(&path).unwrap(), b"someone else's save", "the competing save stays");
            let new = with_contents(dir.path(), b"new version");
            let old = with_contents(dir.path(), b"previous version");
            assert_eq!((new.len(), old.len()), (1, 1), "a complete copy of each version survives");
            let message = err.to_string();
            assert!(!message.contains("unchanged"), "{message}");
            assert!(message.contains("saved by something else meanwhile"), "{message}");
            assert!(message.contains(&dir.path().join(&new[0]).display().to_string()), "{message}");
            assert!(message.contains(&dir.path().join(&old[0]).display().to_string()), "{message}");
        }

        #[test]
        fn an_original_that_vanished_meanwhile_keeps_the_new_contents() {
            // Not a ReplaceFileW state with a backup name, but the file can be deleted by
            // something else: then the staging file is the only copy and must not be deleted.
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |_, path, _| {
                fs::remove_file(path)?;
                Err(io::Error::other("unable to move replacement (1176)"))
            })
            .unwrap_err();
            let new = with_contents(dir.path(), b"new version");
            assert_eq!(new.len(), 1, "the new contents survive");
            assert!(err.to_string().contains(&dir.path().join(&new[0]).display().to_string()), "{err}");
        }

        #[test]
        fn could_not_remove_replaced_1175_leaves_both_names() {
            // ERROR_UNABLE_TO_REMOVE_REPLACED: "the replaced and replacement files retain their
            // original file names".
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |_, _, _| {
                Err(io::Error::other("unable to remove replaced (1175)"))
            })
            .unwrap_err();
            assert!(err.recovery.is_none(), "{err}");
            assert_eq!(fs::read(&path).unwrap(), b"previous version");
            assert_eq!(names(dir.path()), ["scene.gladtalker.json"]);
        }

        #[test]
        fn a_swap_that_happened_despite_the_error_keeps_the_backup_and_says_so() {
            // Not a documented outcome, but if the staging file is gone, nothing is deleted.
            let (dir, path) = setup();
            let err = write_with_replacer(&path, new_contents, |tmp, path, backup| {
                fs::rename(path, backup.unwrap())?;
                fs::rename(tmp, path)?;
                Err(io::Error::other("reported failure"))
            })
            .unwrap_err();
            assert_eq!(fs::read(&path).unwrap(), b"new version");
            assert_eq!(with_contents(dir.path(), b"previous version").len(), 1);
            assert!(err.to_string().contains("may have been saved"), "{err}");
        }
    }

    #[cfg(windows)]
    mod acl {
        use super::super::platform::{create_exclusive, wide, OWNER_ONLY};
        use super::*;
        use std::ptr::null_mut;
        use windows_sys::Win32::Foundation::{LocalFree, ERROR_SHARING_VIOLATION, ERROR_SUCCESS};
        use windows_sys::Win32::Security::Authorization::{
            ConvertSecurityDescriptorToStringSecurityDescriptorW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetNamedSecurityInfoW, SetNamedSecurityInfoW, SDDL_REVISION_1, SE_FILE_OBJECT,
        };
        use windows_sys::Win32::Security::{
            GetSecurityDescriptorDacl, ACL, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR,
        };

        /// The file's DACL as SDDL, e.g. `D:PAI(A;;FA;;;OW)`.
        fn dacl_sddl(path: &Path) -> String {
            let mut sd: PSECURITY_DESCRIPTOR = null_mut();
            let mut text = null_mut();
            let mut len = 0u32;
            unsafe {
                let err = GetNamedSecurityInfoW(wide(path).as_ptr(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                    null_mut(), null_mut(), null_mut(), null_mut(), &mut sd);
                assert_eq!(err, ERROR_SUCCESS);
                assert_ne!(ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, SDDL_REVISION_1,
                    DACL_SECURITY_INFORMATION, &mut text, &mut len), 0);
                let s = String::from_utf16_lossy(std::slice::from_raw_parts(text, len as usize));
                LocalFree(text.cast());
                LocalFree(sd);
                s.trim_end_matches('\0').to_string()
            }
        }

        fn is_owner_only(sddl: &str) -> bool {
            sddl.starts_with("D:P") && sddl.ends_with("(A;;FA;;;OW)") && sddl.matches('(').count() == 1
        }

        /// Gives the file a protected DACL: only its owner, nothing inherited from the folder.
        fn restrict_to_owner(path: &Path) {
            let sddl: Vec<u16> = OWNER_ONLY.encode_utf16().chain(Some(0)).collect();
            let mut sd: PSECURITY_DESCRIPTOR = null_mut();
            unsafe {
                assert_ne!(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), SDDL_REVISION_1,
                    &mut sd, null_mut()), 0);
                let (mut present, mut defaulted) = (0, 0);
                let mut dacl: *mut ACL = null_mut();
                assert_ne!(GetSecurityDescriptorDacl(sd, &mut present, &mut dacl, &mut defaulted), 0);
                let err = SetNamedSecurityInfoW(wide(path).as_ptr(), SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    null_mut(), null_mut(), dacl, std::ptr::null());
                assert_eq!(err, ERROR_SUCCESS);
                LocalFree(sd);
            }
        }

        fn sharing_violation(path: &Path) -> bool {
            fs::File::open(path).err().and_then(|e| e.raw_os_error()) == Some(ERROR_SHARING_VIOLATION as i32)
        }

        #[test]
        fn saves_in_folders_deeper_than_max_path() {
            // With long paths disabled (the Windows default), a raw path over 260 characters
            // fails in CreateFileW; the extended-length form doesn't.
            let tmp = tempfile::tempdir().unwrap();
            let mut dir = tmp.path().to_path_buf();
            while dir.as_os_str().len() < 250 {
                dir.push("a-nested-project-folder");
            }
            fs::create_dir_all(&dir).unwrap();
            let path = dir.join("my long-named scene.gladtalker.json");
            assert!(path.as_os_str().len() > 260, "{}", path.as_os_str().len());

            write_atomically(&path, b"v1").unwrap(); // create
            restrict_to_owner(&path);
            let restricted = dacl_sddl(&path);
            write_atomically_with(&path, |f, staging| {
                assert!(staging.as_os_str().len() > 260);
                assert_eq!(dacl_sddl(staging), restricted);
                f.write_all(b"v2")
            })
            .unwrap(); // overwrite: ACL copy, ReplaceFileW with a backup
            assert_eq!(fs::read(&path).unwrap(), b"v2");
            assert_eq!(dacl_sddl(&path), restricted);
            assert_eq!(names(&dir), ["my long-named scene.gladtalker.json"]);
        }

        #[test]
        fn extended_length_paths_for_drive_and_unc_paths() {
            let text = |p: &str| String::from_utf16(&wide(Path::new(p))[..]).unwrap().trim_end_matches('\0').to_string();
            assert_eq!(text(r"C:\Projects\a.json"), r"\\?\C:\Projects\a.json");
            assert_eq!(text(r"C:/Projects/./x/../a.json"), r"\\?\C:\Projects\a.json");
            assert_eq!(text(r"\\server\share\a.json"), r"\\?\UNC\server\share\a.json");
            assert_eq!(text(r"\\?\C:\already.json"), r"\\?\C:\already.json");
        }

        #[test]
        fn the_staging_file_is_owner_only_and_unshared_from_creation() {
            let tmp = tempfile::tempdir().unwrap();
            let staging = tmp.path().join("staging.tmp");
            let file = create_exclusive(&staging, true).unwrap();
            // The DACL came with CreateFileW: no handle was ever granted under broader permissions.
            assert!(is_owner_only(&dacl_sddl(&staging)), "{}", dacl_sddl(&staging));
            assert!(sharing_violation(&staging), "nobody else can open it while it is being written");
            drop(file);
        }

        #[test]
        fn overwriting_keeps_the_projects_access_restrictions() {
            let tmp = tempfile::tempdir().unwrap();
            let path = tmp.path().join("private.gladtalker.json");
            fs::write(&path, b"secret dialogue").unwrap();
            let inherited = dacl_sddl(&path);
            restrict_to_owner(&path);
            let restricted = dacl_sddl(&path);
            assert!(is_owner_only(&restricted), "{restricted}");
            assert_ne!(restricted, inherited);

            write_atomically_with(&path, |f, staging| {
                // The new contents are never readable more widely, not even while being written.
                assert_eq!(dacl_sddl(staging), restricted);
                assert!(sharing_violation(staging));
                f.write_all(b"new secret dialogue")
            })
            .unwrap();
            assert_eq!(fs::read(&path).unwrap(), b"new secret dialogue");
            assert_eq!(dacl_sddl(&path), restricted, "the saved file kept its restrictions");
            assert_eq!(names(tmp.path()), ["private.gladtalker.json"], "no staging or backup file is left");

            // A file that inherits the folder's permissions keeps inheriting them.
            let open = tmp.path().join("shared.gladtalker.json");
            fs::write(&open, b"v1").unwrap();
            let before = dacl_sddl(&open);
            write_atomically(&open, b"v2").unwrap();
            assert_eq!(dacl_sddl(&open), before);
        }
    }

    #[cfg(unix)]
    #[test]
    fn overwriting_keeps_the_projects_permission_bits() {
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("private.gladtalker.json");
        fs::write(&path, b"secret dialogue").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        write_atomically_with(&path, |f, staging| {
            assert_eq!(fs::metadata(staging).unwrap().permissions().mode() & 0o777, 0o600);
            f.write_all(b"new")
        })
        .unwrap();
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }
}
