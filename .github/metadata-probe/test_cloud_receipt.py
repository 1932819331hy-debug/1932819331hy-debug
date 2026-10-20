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

    def test_first_observations_reach_qualification_without_dispatch_authority(self):
        module = self.module()
        manifest = {"sources": [{"id": "issues", "qualification_source": True},
                                 {"id": "own-prs"}]}
        events = [{"event_id": str(index), "source_id": "issues", "schema_version": 2,
                   "processing_status": status, "dispatch_allowed": True}
                  for index, status in enumerate(("BASELINE_UNTRIAGED",
                      "FIRST_OBSERVED_UNVERIFIED", "VALIDATION_REQUIRED"))]
        events += [{"source_id": "own-prs", "schema_version": 2,
                    "processing_status": "VALIDATION_REQUIRED"},
                   {"source_id": "issues", "processing_status": "LEGACY_UNVERIFIED"}]
        result = module.qualification_handoff(events, manifest, limit=2)
        self.assertEqual([x["event_id"] for x in result["observations"]], ["0", "1"])
        self.assertEqual(result["coverage"]["total"], 3)
        self.assertTrue(result["coverage"]["truncated"])
        self.assertEqual(result["coverage"]["full_snapshot"], "events.jsonl")
        for observation in result["observations"]:
            self.assertTrue(observation["qualification_required"])
            self.assertFalse(observation["dispatch_allowed"])
            self.assertFalse(observation["publish_allowed"])
        self.assertEqual(events[0]["dispatch_allowed"], True)

    def test_manifest_explicitly_exposes_repository_and_reward_discovery(self):
        module = self.module()
        manifest = json.loads(Path(__file__).with_name("sources.json").read_text())
        qualified = {x["id"] for x in manifest["sources"] if x.get("qualification_source")}
        self.assertEqual(qualified, {"squash-fast", "cash-discovery-partitions",
                                     "known-repository-issues"})
        result = module.qualification_handoff([], manifest)
        self.assertEqual(result["observations"], [])
        self.assertFalse(result["coverage"]["truncated"])


if __name__ == "__main__":
    unittest.main()
