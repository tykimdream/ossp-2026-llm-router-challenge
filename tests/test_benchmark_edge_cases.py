# SPDX-FileCopyrightText: Copyright 2026 SK TELECOM CO., LTD.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/benchmark_edge_cases.py"
SPEC = importlib.util.spec_from_file_location("benchmark_edge_cases", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class BenchmarkEdgeCasesTest(unittest.TestCase):
    def test_v6_and_v7_workers_write_valid_submissions(self) -> None:
        for router in ("v6", "v7"):
            with self.subTest(router=router), tempfile.TemporaryDirectory() as directory:
                output = pathlib.Path(directory) / "submission.json"
                metrics = pathlib.Path(directory) / "metrics.json"
                args = argparse.Namespace(
                    worker_input=ROOT / "data/toy/inputs.json",
                    worker_output=output,
                    worker_metrics=metrics,
                    worker_router=router,
                    worker_tier="fast",
                )
                self.assertEqual(0, benchmark._worker(args))
                routed = json.loads(output.read_text(encoding="utf-8"))
                measurements = json.loads(metrics.read_text(encoding="utf-8"))
            self.assertEqual("fast", routed["tier"])
            self.assertGreater(len(routed["decisions"]), 0)
            self.assertGreaterEqual(measurements["worker_seconds"], 0)

    def test_resume_rejects_failed_records(self) -> None:
        valid = {
            "timed_out": False,
            "return_code": 0,
            "submission_valid": True,
            "output_limit_passed": True,
        }
        self.assertTrue(benchmark._record_ok(valid))
        self.assertFalse(benchmark._record_ok({**valid, "return_code": 1}))


if __name__ == "__main__":
    unittest.main()
