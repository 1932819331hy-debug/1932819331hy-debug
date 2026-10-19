"""Cloud handoff must expose evidence without claiming a worker or Page write."""
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path


class CloudReceiptTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location(
            "cloud_receipt", Path(__file__).with_name("run_cloud_probe.py"))
        module = importlib.util.module_from_spec(spec)
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                with mock.patch("subprocess.run", side_effect=RuntimeError("No IO during import")):
                    spec.loader.exec_module(module)
            finally:
                os.chdir(previous)
        return module

    def test_source_and_historical_observation_survive_export(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "cache.db"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE events(id TEXT PRIMARY KEY,source,item,payload,observed)")
                con.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                    "old", "cash-partition", "gh:1", json.dumps({
                        "kind": "METADATA_CHANGE_REQUIRES_HUMAN_VERIFICATION",
                        "dispatch_allowed": False}), 1000))
            events, coverage = module.export_events(db)
            self.assertEqual(events[0]["source"], "cash-partition")
            self.assertEqual(events[0]["detected_at_unix"], 1000)
            self.assertEqual(events[0]["processing_status"], "LEGACY_UNVERIFIED")
            self.assertFalse(events[0]["dispatch_allowed"])
            self.assertEqual(coverage["total_cached"], 1)

    def test_cache_save_and_trigger_request_are_not_worker_or_page_receipts(self):
        module = self.module()
        stages = module.handoff_stages({"id": "probe-1", "end": "2026-10-19T20:00:00Z",
                                        "new_events": 1, "github_run_id": "123"})
        self.assertEqual(stages["EVENT_DETECTED"]["at"], "2026-10-19T20:00:00Z")
        for name in ("EVENT_VALIDATED", "TICKET_READY", "WORKER_TRIGGER_REQUESTED",
                     "WORKER_STARTED", "ACTION_COMPLETED", "RECEIPT_PERSISTED"):
            self.assertIsNone(stages[name]["at"])
        self.assertEqual(stages["WORKER_STARTED"]["status"], "WORKER_TRIGGER_BLOCKED")
        self.assertEqual(stages["RECEIPT_PERSISTED"]["status"], "PENDING_CANONICAL_PAGE_WRITER")


if __name__ == "__main__":
    unittest.main()
