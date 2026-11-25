//! Saving a file so that it is either fully replaced or left as it was, without loosening who may
//! read it.
//!
//! The bytes go to a temporary file next to the destination, which then takes its place. When the
//! destination already exists, its access restrictions are kept:
//! - **Windows:** the temporary file gets the destination's DACL (protected or inherited, as it
//!   was) *before* any byte is written, and `ReplaceFileW` swaps it in, which keeps the replaced
//!   file's DACL, attributes, and creation time.
//! - **Unix:** the temporary file is created owner-only (0600), then given the destination's
//!   permission bits and group; if its group can't be matched, the group and other bits are
//!   dropped rather than shared with a different group.
//!
//! A new file gets the folder's defaults, as any newly created file does.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub fn write_atomically(path: &Path, contents: &[u8]) -> io::Result<()> {
    write_atomically_with(path, |file, _| io::Write::write_all(file, contents))
}

/// Like [`write_atomically`], with the writing done by `write` (given the temporary file and its
/// path; the tests use it to fail halfway and to inspect the temporary file).
pub fn write_atomically_with(
    path: &Path,
    write: impl FnOnce(&mut fs::File, &Path) -> io::Result<()>,
) -> io::Result<()> {
    let tmp = temp_sibling(path);
    let existing = fs::metadata(path).is_ok_and(|m| m.is_file());
    let mut keep_tmp = false;
    let result = (|| {
        let mut file = platform::create_like(&tmp, existing.then_some(path))?;
        write(&mut file, &tmp)?;
        file.sync_all()?;
        drop(file);
        platform::replace(&tmp, path, existing).inspect_err(|e| keep_tmp = platform::tmp_holds_the_only_copy(e))
    })();
    if result.is_err() && !keep_tmp {
        let _ = fs::remove_file(&tmp);
    }
    result
}

fn temp_sibling(path: &Path) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    path.with_file_name(format!(".{name}.{}-{nanos}-{n}.tmp", std::process::id()))
}

#[cfg(windows)]
mod platform {
    use std::fs;
    use std::io;
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;
    use std::ptr::{null, null_mut};

    use windows_sys::Win32::Foundation::{LocalFree, ERROR_FILE_NOT_FOUND, ERROR_SUCCESS, ERROR_UNABLE_TO_MOVE_REPLACEMENT_2};
    use windows_sys::Win32::Security::Authorization::{GetNamedSecurityInfoW, SetNamedSecurityInfoW, SE_FILE_OBJECT};
    use windows_sys::Win32::Security::{
        GetSecurityDescriptorControl, ACL, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
        PSECURITY_DESCRIPTOR, SE_DACL_PROTECTED, UNPROTECTED_DACL_SECURITY_INFORMATION,
    };
    use windows_sys::Win32::Storage::FileSystem::ReplaceFileW;

    pub(super) fn wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain(Some(0)).collect()
    }

    /// Creates the temporary file with `like`'s DACL already in place (before anything is written).
    pub(super) fn create_like(tmp: &Path, like: Option<&Path>) -> io::Result<fs::File> {
        let file = fs::OpenOptions::new().write(true).create_new(true).open(tmp)?;
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
            // SAFETY: valid path; `dacl` points into `sd`, which is still alive.
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

    /// Puts `tmp` in `path`'s place. An existing file is replaced with ReplaceFileW, which keeps
    /// its DACL, attributes, and creation time; a new one is simply renamed.
    pub(super) fn replace(tmp: &Path, path: &Path, existing: bool) -> io::Result<()> {
        if existing {
            // SAFETY: valid NUL-terminated paths; no backup file; reserved pointers are null.
            let ok = unsafe { ReplaceFileW(wide(path).as_ptr(), wide(tmp).as_ptr(), null(), 0, null(), null()) };
            if ok != 0 {
                return Ok(());
            }
            let err = io::Error::last_os_error();
            if err.raw_os_error() != Some(ERROR_FILE_NOT_FOUND as i32) {
                return Err(err);
            }
            // The file was removed meanwhile: there is nothing left to keep, so a rename will do.
        }
        fs::rename(tmp, path)
    }

    /// ReplaceFileW's one failure that moves the original aside: then the temporary file holds
    /// the only complete copy of the new contents, so it is kept.
    pub(super) fn tmp_holds_the_only_copy(err: &io::Error) -> bool {
        err.raw_os_error() == Some(ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 as i32)
    }
}

#[cfg(unix)]
mod platform {
    use std::fs;
    use std::io;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
    use std::path::Path;

    /// Creates the temporary file owner-only, then gives it `like`'s permission bits and group
    /// (or, if the group can't be matched, only its owner bits), before anything is written.
    pub(super) fn create_like(tmp: &Path, like: Option<&Path>) -> io::Result<fs::File> {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        if like.is_some() {
            options.mode(0o600);
        }
        let file = options.open(tmp)?;
        if let Some(like) = like {
            let meta = fs::metadata(like)?;
            let mut mode = meta.mode() & 0o7777;
            if std::os::unix::fs::fchown(&file, None, Some(meta.gid())).is_err() {
                mode &= 0o700; // a different group must not get the original group's access
            }
            file.set_permissions(fs::Permissions::from_mode(mode))?;
        }
        Ok(file)
    }

    pub(super) fn replace(tmp: &Path, path: &Path, _existing: bool) -> io::Result<()> {
        fs::rename(tmp, path)
    }

    pub(super) fn tmp_holds_the_only_copy(_err: &io::Error) -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn saving_replaces_the_file_whole_or_not_at_all() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("scene.gladtalker.json");
        fs::write(&path, b"original project").unwrap();

        // A write that fails halfway leaves the original bytes, and no temporary file.
        let err = write_atomically_with(&path, |f, _| {
            f.write_all(b"half of the new")?;
            Err(io::Error::other("disk full"))
        });
        assert!(err.is_err());
        assert_eq!(fs::read(&path).unwrap(), b"original project");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1, "the temporary file was removed");

        write_atomically(&path, b"new project").unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"new project");
        assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
        write_atomically(&tmp.path().join("fresh.json"), b"{}").unwrap(); // a new file works too
        assert_eq!(fs::read(tmp.path().join("fresh.json")).unwrap(), b"{}");
    }

    #[cfg(windows)]
    mod acl {
        use super::super::platform::wide;
        use super::*;
        use std::ptr::null_mut;
        use windows_sys::Win32::Foundation::{LocalFree, ERROR_SUCCESS};
        use windows_sys::Win32::Security::Authorization::{
            ConvertSecurityDescriptorToStringSecurityDescriptorW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetNamedSecurityInfoW, SetNamedSecurityInfoW, SDDL_REVISION_1, SE_FILE_OBJECT,
        };
        use windows_sys::Win32::Security::{
            GetSecurityDescriptorDacl, ACL, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
            PSECURITY_DESCRIPTOR,
        };

        /// The file's DACL as SDDL, e.g. `D:P(A;;FA;;;OW)`.
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

        /// Gives the file a protected DACL: only its owner, nobody inherited from the folder.
        fn restrict_to_owner(path: &Path) {
            let sddl: Vec<u16> = "D:P(A;;FA;;;OW)".encode_utf16().chain(Some(0)).collect();
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

        #[test]
        fn overwriting_keeps_the_projects_access_restrictions() {
            let tmp = tempfile::tempdir().unwrap();
            let path = tmp.path().join("private.gladtalker.json");
            fs::write(&path, b"secret dialogue").unwrap();
            let inherited = dacl_sddl(&path);
            restrict_to_owner(&path);
            let restricted = dacl_sddl(&path);
            // Protected ("P", plus "AI" as Windows records it), with the owner as its only entry.
            assert!(restricted.starts_with("D:P") && restricted.ends_with("(A;;FA;;;OW)"), "{restricted}");
            assert_eq!(restricted.matches('(').count(), 1, "{restricted}");
            assert_ne!(restricted, inherited);

            write_atomically_with(&path, |f, tmp_path| {
                // The new contents are never readable more widely, not even while being written.
                assert_eq!(dacl_sddl(tmp_path), restricted);
                f.write_all(b"new secret dialogue")
            })
            .unwrap();
            assert_eq!(fs::read(&path).unwrap(), b"new secret dialogue");
            assert_eq!(dacl_sddl(&path), restricted, "the saved file kept its restrictions");
            assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);

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
        write_atomically_with(&path, |f, tmp_path| {
            assert_eq!(fs::metadata(tmp_path).unwrap().permissions().mode() & 0o777, 0o600);
            f.write_all(b"new")
        })
        .unwrap();
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }
}
