import asyncio
import threading
import time
import unittest
from unittest import mock

from src.studio.jobs import JobCancelled, JobManager, UserFacingError


def _wait(job, timeout=5.0):
    deadline = time.time() + timeout
    while not job.done and time.time() < deadline:
        time.sleep(0.01)
    return job


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.jobs = JobManager(lanes={"gpu": 1, "io": 1})

    def tearDown(self):
        self.jobs.shutdown()

    def test_success_records_result_and_events(self):
        def fn(ctx):
            ctx.set_stages(["a", "b"])
            ctx.progress(0.5, "half", stage="a")
            ctx.progress(0.25, "progress never goes backwards", stage="b")
            return {"ok": True}

        job = _wait(self.jobs.submit("t", "Test", fn))
        self.assertEqual(job.state, "succeeded")
        self.assertEqual(job.result, {"ok": True})
        self.assertEqual(job.progress, 1.0)
        types = [e["type"] for e in job.events]
        self.assertEqual(types[0], "state")
        self.assertIn("progress", types)
        progresses = [e["job"]["progress"] for e in job.events if e["type"] == "progress"]
        self.assertEqual(progresses, sorted(progresses))
        self.assertEqual(job.events[-1]["job"]["state"], "succeeded")

    def test_user_facing_error_message_kept(self):
        def fn(ctx):
            raise UserFacingError("Region is too small.")

        job = _wait(self.jobs.submit("t", "Test", fn))
        self.assertEqual((job.state, job.error, job.message), ("failed", "Region is too small.", "Region is too small."))

    def test_unexpected_error(self):
        # The engine logs the traceback of unexpected failures; capture it instead of printing it.
        with self.assertLogs("gladtalker.studio", level="ERROR") as logs:
            job = _wait(self.jobs.submit("t", "Test", lambda ctx: 1 / 0))
        self.assertEqual(job.state, "failed")
        self.assertIn("division by zero", job.error)
        self.assertIn("ZeroDivisionError", "\n".join(logs.output))

    def test_cancel_running_job(self):
        started = threading.Event()

        def fn(ctx):
            started.set()
            while True:
                ctx.progress(message="working")
                time.sleep(0.01)

        job = self.jobs.submit("t", "Test", fn)
        self.assertTrue(started.wait(2))
        self.jobs.cancel(job.id)
        self.assertEqual(_wait(job).state, "cancelled")

    def test_cancel_queued_job(self):
        gate = threading.Event()
        first = self.jobs.submit("t", "First", lambda ctx: gate.wait(5))
        second = self.jobs.submit("t", "Second", lambda ctx: {"ran": True})
        self.jobs.cancel(second.id)
        gate.set()
        _wait(first)
        self.assertEqual(_wait(second).state, "cancelled")
        self.assertIsNone(second.result)

    def test_job_cancelled_exception_maps_to_cancelled(self):
        def fn(ctx):
            raise JobCancelled()

        self.assertEqual(_wait(self.jobs.submit("t", "T", fn)).state, "cancelled")

    def test_async_subscriber_receives_live_events(self):
        gate = threading.Event()

        def fn(ctx):
            gate.wait(5)
            ctx.progress(0.5, "half")
            return {}

        job = self.jobs.submit("t", "Test", fn)

        async def consume():
            queue, history = self.jobs.subscribe(job.id, asyncio.get_running_loop())
            gate.set()
            seen = list(history)
            while not (seen and seen[-1]["type"] == "state" and seen[-1]["job"]["state"] == "succeeded"):
                seen.append(await asyncio.wait_for(queue.get(), 5))
            self.jobs.unsubscribe(job.id, queue)
            return seen

        events = asyncio.run(consume())
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(set(seqs)), "no duplicates, in order")
        self.assertIn("half", [e["job"]["message"] for e in events])


class ExplainLoadFailureTests(unittest.TestCase):
    def test_busy_or_damaged_files_explain_the_failure(self):
        from src.studio.jobs import RetryLaterError, UserFacingError, explain_load_failure

        for found in (RetryLaterError("busy, try again"), UserFacingError("damaged, repair")):
            def recheck(found=found):
                raise found

            with self.assertRaises(type(found)) as caught:
                with explain_load_failure(recheck):
                    raise RuntimeError("PytorchStreamReader failed")
            self.assertIs(caught.exception, found)
            self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_healthy_files_leave_the_original_error_and_cancellation_alone(self):
        from src.studio.jobs import JobCancelled, explain_load_failure

        recheck = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with explain_load_failure(recheck):
                raise RuntimeError("boom")
        recheck.assert_called_once()
        recheck.reset_mock()
        with self.assertRaises(JobCancelled):
            with explain_load_failure(recheck):
                raise JobCancelled()
        recheck.assert_not_called()



if __name__ == "__main__":
    unittest.main()
