import os
import sys
import time
import unittest
import tempfile
import shutil

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitoring import monitor


class TestMonitoring(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="healthcare_test_mon_")
        self._original_path = monitor.MONITORING_LOG_PATH
        monitor.MONITORING_LOG_PATH = os.path.join(self._tmpdir, "monitoring.jsonl")

    def tearDown(self):
        monitor.MONITORING_LOG_PATH = self._original_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_track_success_logs_metric(self):
        with monitor.track("test.module") as status:
            time.sleep(0.001)
        records = monitor.load_metrics()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["module"], "test.module")
        self.assertTrue(records[0]["success"])
        self.assertGreater(records[0]["latency_ms"], 0)

    def test_track_manual_failure_flag(self):
        with monitor.track("test.module") as status:
            status["success"] = False
        records = monitor.load_metrics()
        self.assertFalse(records[0]["success"])

    def test_track_exception_marks_failure_and_reraises(self):
        with self.assertRaises(ValueError):
            with monitor.track("test.module"):
                raise ValueError("boom")
        records = monitor.load_metrics()
        self.assertFalse(records[0]["success"])

    def test_summarize_latency_aggregates_per_module(self):
        for _ in range(3):
            with monitor.track("module.a"):
                pass
        with monitor.track("module.b") as status:
            status["success"] = False

        summary = monitor.summarize_latency()
        self.assertEqual(summary["module.a"]["count"], 3)
        self.assertEqual(summary["module.a"]["success_rate"], 1.0)
        self.assertEqual(summary["module.b"]["count"], 1)
        self.assertEqual(summary["module.b"]["success_rate"], 0.0)

    def test_percentile_calculation(self):
        values = [10, 20, 30, 40, 50]
        # p50 of [10,20,30,40,50] should be the median, 30
        self.assertEqual(monitor._percentile(values, 0.5), 30)

    def test_percentile_empty_list(self):
        self.assertEqual(monitor._percentile([], 0.95), 0.0)

    def test_load_metrics_with_no_file_returns_empty(self):
        self.assertEqual(monitor.load_metrics(), [])

    def test_throughput_over_time_buckets_by_time(self):
        with monitor.track("module.a"):
            pass
        buckets = monitor.throughput_over_time()
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["requests"], 1)


if __name__ == "__main__":
    unittest.main()
