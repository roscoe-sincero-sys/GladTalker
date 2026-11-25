//! Keeps at most one engine running and serializes everything that changes it.
//!
//! Start, restart, and "use this folder now" all take the same lock for the whole stop → start
//! sequence. A restart or folder change requested while a cold start is still running (which can
//! take minutes) therefore waits for that start and then replaces it, instead of finding nothing
//! to stop and handing back the old engine.

use std::path::PathBuf;
use std::sync::Mutex;

use crate::engine::Status;

/// What the supervisor needs from a running engine (a real process, or a fake in tests).
pub trait Engine: Send {
    fn is_alive(&mut self) -> bool;
    fn status(&self) -> Status;
}

impl Engine for crate::engine::Running {
    fn is_alive(&mut self) -> bool {
        crate::engine::Running::is_alive(self)
    }
    fn status(&self) -> Status {
        self.status.clone()
    }
}

pub struct Supervisor<E: Engine> {
    lifecycle: Mutex<()>,
    running: Mutex<Option<E>>,
    /// The folder the user picked in this session: tried before anything else.
    chosen: Mutex<Option<PathBuf>>,
}

impl<E: Engine> Default for Supervisor<E> {
    fn default() -> Self {
        Supervisor { lifecycle: Mutex::new(()), running: Mutex::new(None), chosen: Mutex::new(None) }
    }
}

impl<E: Engine> Supervisor<E> {
    /// Ensures an engine is running and returns its status. With `restart`, the current engine
    /// (if any) is stopped first. With `choose`, that folder is preferred from now on. `start`
    /// gets the preferred folder and starts an engine, or explains why it couldn't.
    pub fn ensure(
        &self,
        restart: bool,
        choose: Option<PathBuf>,
        start: impl FnOnce(Option<PathBuf>) -> Result<E, Status>,
    ) -> Status {
        let _one_at_a_time = self.lifecycle.lock().unwrap();
        if let Some(folder) = choose {
            *self.chosen.lock().unwrap() = Some(folder);
        }
        {
            let mut running = self.running.lock().unwrap();
            if restart {
                drop(running.take()); // dropping a real engine stops its process tree
            }
            if let Some(engine) = running.as_mut() {
                if engine.is_alive() {
                    return engine.status();
                }
            }
            *running = None;
        }
        let chosen = self.chosen.lock().unwrap().clone();
        match start(chosen) {
            Ok(engine) => {
                let status = engine.status();
                *self.running.lock().unwrap() = Some(engine);
                status
            }
            Err(status) => status,
        }
    }

    /// The running engine's status, if there is one (without starting anything).
    pub fn current(&self) -> Option<Status> {
        self.running.lock().unwrap().as_ref().map(Engine::status)
    }

    /// Stops the engine (app exit). Doesn't wait for a start in progress.
    pub fn stop(&self) {
        drop(self.running.lock().unwrap().take());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{mpsc, Arc};
    use std::thread;
    use std::time::Duration;

    struct Fake {
        name: String,
        alive: bool,
        stops: Arc<AtomicUsize>,
    }

    impl Engine for Fake {
        fn is_alive(&mut self) -> bool {
            self.alive
        }
        fn status(&self) -> Status {
            Status::Ready { base: self.name.clone(), token: String::new(), folder: String::new(), python: String::new(),
                workspace: String::new(), note: None }
        }
    }

    impl Drop for Fake {
        fn drop(&mut self) {
            self.stops.fetch_add(1, Ordering::SeqCst);
        }
    }

    fn base(status: &Status) -> String {
        match status {
            Status::Ready { base, .. } => base.clone(),
            other => panic!("{other:?}"),
        }
    }

    fn fake(name: &str, stops: &Arc<AtomicUsize>) -> Fake {
        Fake { name: name.into(), alive: true, stops: stops.clone() }
    }

    #[test]
    fn reuses_a_live_engine_and_replaces_a_dead_one() {
        let stops = Arc::new(AtomicUsize::new(0));
        let sup = Supervisor::<Fake>::default();
        assert_eq!(base(&sup.ensure(false, None, |_| Ok(fake("first", &stops)))), "first");
        assert_eq!(base(&sup.ensure(false, None, |_| panic!("must not start a second engine"))), "first");
        sup.running.lock().unwrap().as_mut().unwrap().alive = false;
        assert_eq!(base(&sup.ensure(false, None, |_| Ok(fake("second", &stops)))), "second");
    }

    #[test]
    fn a_restart_during_a_cold_start_replaces_the_engine_that_start_produces() {
        let stops = Arc::new(AtomicUsize::new(0));
        let sup = Arc::new(Supervisor::<Fake>::default());
        let (started_tx, started_rx) = mpsc::channel();
        let first = {
            let (sup, stops) = (sup.clone(), stops.clone());
            thread::spawn(move || {
                sup.ensure(false, None, |_| {
                    started_tx.send(()).unwrap();
                    thread::sleep(Duration::from_millis(200)); // a slow cold start
                    Ok(fake("old", &stops))
                })
            })
        };
        started_rx.recv().unwrap(); // the first start is in progress
        let restarted = sup.ensure(true, None, |_| Ok(fake("new", &stops)));
        assert_eq!(base(&first.join().unwrap()), "old");
        assert_eq!(base(&restarted), "new", "the restart must not hand back the engine that was starting");
        assert_eq!(stops.load(Ordering::SeqCst), 1, "the old engine was stopped");
        assert_eq!(base(&sup.current().unwrap()), "new");
    }

    #[test]
    fn a_chosen_folder_is_passed_to_every_later_start() {
        let stops = Arc::new(AtomicUsize::new(0));
        let sup = Supervisor::<Fake>::default();
        sup.ensure(false, None, |chosen| {
            assert_eq!(chosen, None);
            Ok(fake("default", &stops))
        });
        sup.ensure(true, Some("/picked".into()), |chosen| {
            assert_eq!(chosen, Some(PathBuf::from("/picked")));
            Ok(fake("picked", &stops))
        });
        sup.ensure(true, None, |chosen| {
            assert_eq!(chosen, Some(PathBuf::from("/picked")), "a later restart keeps the user's choice");
            Ok(fake("picked again", &stops))
        });
    }

    #[test]
    fn a_failed_start_is_reported_and_leaves_no_engine() {
        let sup = Supervisor::<Fake>::default();
        let status = sup.ensure(false, None, |_| Err(Status::NotFound { message: "none".into(), folder: None }));
        assert!(matches!(status, Status::NotFound { .. }));
        assert!(sup.current().is_none());
    }
}
