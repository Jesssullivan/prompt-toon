from __future__ import annotations

import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import gateway_capacity as capacity  # noqa: E402


CAPACITY = ROOT / "tools" / "gateway_capacity.py"
FAKE_SERVER = ROOT / "tests" / "fake_ptoon_serve.py"


class GatewayCapacityTest(unittest.TestCase):
    def test_sampler_startup_failure_is_reported_without_waiting_for_timeout(self) -> None:
        first_sample = threading.Event()
        failures: list[BaseException] = []
        with mock.patch.object(Path, "is_file", return_value=True), mock.patch.object(
            capacity, "_status_value", side_effect=RuntimeError("sample failed")
        ):
            capacity._sample(
                SimpleNamespace(),
                SimpleNamespace(pid=1),
                threading.Event(),
                [],
                first_sample,
                failures,
            )

        self.assertTrue(first_sample.is_set())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)

    def test_capacity_gate_measures_an_isolated_gateway_process(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "PROMPT_TOON_PTOON": str(FAKE_SERVER),
                "PROMPT_TOON_GATEWAY_CAPACITY_STREAMS": "1",
                "PROMPT_TOON_GATEWAY_CAPACITY_PADDING_BYTES": "1024",
                "PYTHONPATH": str(ROOT),
            }
        )
        for name in (
            "PROMPT_TOON_GATEWAY_MAX_RSS_KIB",
            "PROMPT_TOON_SERVICE_MAX_RSS_KIB",
            "PROMPT_TOON_GATEWAY_MAX_THREADS",
        ):
            env.pop(name, None)

        completed = subprocess.run(
            [sys.executable, str(CAPACITY)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            # The script runs both provider protocols in separately spawned
            # gateway processes. Let each protocol's own bounded deadline
            # report and clean up failures before this outer watchdog fires.
            timeout=2 * capacity.RESULT_TIMEOUT_SECONDS + 60,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("anthropic=1/1; openai=1/1", completed.stdout)
        self.assertIn("isolated gateway process", completed.stdout)

    def test_sigterm_closes_worker_and_reaps_resident(self) -> None:
        old_env = os.environ.get("PROMPT_TOON_GATEWAY_CAPACITY_STREAMS")
        old_streams = capacity.STREAMS
        worker = None
        try:
            os.environ["PROMPT_TOON_GATEWAY_CAPACITY_STREAMS"] = "1"
            capacity.STREAMS = 1
            worker = capacity._start_gateway_worker(
                str(FAKE_SERVER), "anthropic", "http://127.0.0.1:9", 4096
            )
            worker.process.terminate()
            self.assertTrue(
                worker.control.poll(capacity.PROCESS_EXIT_TIMEOUT_SECONDS),
                "terminated worker did not report graceful cleanup",
            )
            message = worker.control.recv()
            self.assertEqual(message.get("kind"), "stopped", message)
            worker.process.join(timeout=capacity.PROCESS_EXIT_TIMEOUT_SECONDS)
            self.assertFalse(worker.process.is_alive())
            self.assertEqual(worker.process.exitcode, 0)
            self.assertTrue(
                capacity._wait_for_pid_exit(
                    worker.resident_pid, capacity.PROCESS_EXIT_TIMEOUT_SECONDS
                ),
                f"resident {worker.resident_pid} survived worker SIGTERM",
            )
        finally:
            capacity.STREAMS = old_streams
            if old_env is None:
                os.environ.pop("PROMPT_TOON_GATEWAY_CAPACITY_STREAMS", None)
            else:
                os.environ["PROMPT_TOON_GATEWAY_CAPACITY_STREAMS"] = old_env
            if worker is not None:
                worker.control.close()
                if worker.process.is_alive():
                    capacity._terminate_process(worker.process)


if __name__ == "__main__":
    unittest.main()
