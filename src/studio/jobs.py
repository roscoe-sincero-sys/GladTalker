"""Background jobs with progress events, cancellation, and async subscriber fan-out.

Jobs run on worker threads (one queue per resource, e.g. a single "gpu" lane so renders never
compete for VRAM). Each job publishes JSON-able events; WebSocket handlers subscribe with an
asyncio queue and receive events via ``loop.call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("gladtalker.studio")

TERMINAL_STATES = ("succeeded", "failed", "cancelled")
MAX_EVENT_HISTORY = 500


class JobCancelled(Exception):
    """Raised inside a job when the user pressed Cancel."""


class UserFacingError(Exception):
    """An error whose message is plain-language and actionable (shown as-is in the UI)."""


@dataclass
class Job:
    id: str
    kind: str
    title: str
    state: str = "queued"
    stage: Optional[str] = None
    stages: list[str] = field(default_factory=list)
    progress: float = 0.0
    message: str = "Waiting to start…"
    eta_s: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    events: list[dict] = field(default_factory=list, repr=False)
    seq: Any = field(default_factory=itertools.count, repr=False)

    @property
    def done(self) -> bool:
        return self.state in TERMINAL_STATES

    def snapshot(self) -> dict:
        elapsed = None
        if self.started_at:
            elapsed = round((self.finished_at or time.time()) - self.started_at, 2)
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "stage": self.stage,
            "stages": list(self.stages),
            "progress": round(self.progress, 4),
            "message": self.message,
            "eta_s": None if self.eta_s is None else round(self.eta_s, 1),
            "elapsed_s": elapsed,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_event.is_set(),
        }


class JobContext:
    """Handle passed to a job function for reporting progress and honouring Cancel."""

    def __init__(self, manager: "JobManager", job: Job):
        self._manager = manager
        self.job = job

    @property
    def cancelled(self) -> bool:
        return self.job.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.job.cancel_event.is_set():
            raise JobCancelled()

    def set_stages(self, stages: list[str]) -> None:
        self.job.stages = list(stages)
        self._manager._publish(self.job, "state")

    def progress(
        self,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        stage: Optional[str] = None,
        eta_s: Optional[float] = None,
        **extra: Any,
    ) -> None:
        """Report overall progress (0..1, never decreases), a status message, and/or the stage.

        Raises JobCancelled if Cancel was requested, so frequent progress calls double as
        cancellation points.
        """
        self.check_cancelled()
        job = self.job
        if progress is not None:
            job.progress = min(1.0, max(job.progress, float(progress)))
        if stage is not None:
            job.stage = stage
        if message is not None:
            job.message = message
        job.eta_s = eta_s
        self._manager._publish(job, "progress", **extra)

    def log(self, message: str, level: str = "info") -> None:
        self._manager._publish(self.job, "log", level=level, message=message)


JobFn = Callable[[JobContext], Optional[dict]]


class JobManager:
    def __init__(self, lanes: Optional[dict[str, int]] = None, max_jobs: int = 200):
        self._lanes = {name: ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"studio-{name}")
                       for name, n in (lanes or {"gpu": 1, "io": 2}).items()}
        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs

    # -- submission -------------------------------------------------------------------------
    def submit(self, kind: str, title: str, fn: JobFn, lane: str = "gpu") -> Job:
        if lane not in self._lanes:
            raise KeyError(f"Unknown job lane '{lane}'")
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job
            self._prune()
        self._publish(job, "state")
        self._lanes[lane].submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: JobFn) -> None:
        ctx = JobContext(self, job)
        if job.cancel_event.is_set():
            self._finish(job, "cancelled", message="Cancelled before starting.")
            return
        job.state = "running"
        job.started_at = time.time()
        job.message = "Starting…"
        self._publish(job, "state")
        try:
            result = fn(ctx)
        except JobCancelled:
            self._finish(job, "cancelled", message="Cancelled.")
        except UserFacingError as exc:
            self._finish(job, "failed", error=str(exc), message=str(exc))
        except Exception as exc:  # noqa: BLE001 - report any engine failure to the UI
            log.error("Job %s (%s) failed:\n%s", job.id, job.kind, traceback.format_exc())
            msg = str(exc) or exc.__class__.__name__
            self._finish(job, "failed", error=msg, message=f"Something went wrong: {msg}")
        else:
            job.progress = 1.0
            job.result = result
            self._finish(job, "succeeded", message="Done.")

    def _finish(self, job: Job, state: str, message: str, error: Optional[str] = None) -> None:
        job.state = state
        job.error = error
        job.message = message
        job.eta_s = None
        job.finished_at = time.time()
        self._publish(job, "state")

    # -- queries & control --------------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def active(self, kind: Optional[str] = None) -> list[Job]:
        return [j for j in self.list() if not j.done and (kind is None or j.kind == kind)]

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if job is None:
            return None
        if not job.done:
            job.cancel_event.set()
            job.message = "Cancelling…"
            self._publish(job, "state")
        return job

    def shutdown(self) -> None:
        for job in self.list():
            job.cancel_event.set()
        for pool in self._lanes.values():
            pool.shutdown(wait=False, cancel_futures=True)

    def _prune(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        for job in sorted(self._jobs.values(), key=lambda j: j.created_at):
            if len(self._jobs) <= self._max_jobs:
                break
            if job.done:
                self._jobs.pop(job.id, None)
                self._subscribers.pop(job.id, None)

    # -- events --------------------------------------------------------------------------------
    def subscribe(self, job_id: str, loop: asyncio.AbstractEventLoop) -> tuple[asyncio.Queue, list[dict]]:
        """Register an asyncio queue for a job; returns it plus the event history so far."""
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            job = self._jobs[job_id]
            self._subscribers.setdefault(job_id, []).append((loop, queue))
            history = list(job.events)
        return queue, history

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id, [])
            self._subscribers[job_id] = [(lp, q) for lp, q in subs if q is not queue]

    def _publish(self, job: Job, event_type: str, **extra: Any) -> None:
        event = {"type": event_type, "seq": next(job.seq), "ts": time.time(), "job": job.snapshot(), **extra}
        with self._lock:
            job.events.append(event)
            if len(job.events) > MAX_EVENT_HISTORY:
                # Keep the first event (creation) and the most recent ones.
                del job.events[1 : len(job.events) - MAX_EVENT_HISTORY + 1]
            subs = list(self._subscribers.get(job.id, []))
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # subscriber's loop already closed
                self.unsubscribe(job.id, queue)
