"""Data-only transport controls; no fake bounty or public activity."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name("worker_report.py")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def fixture():
    text = "real check output\n"
    report = {"schema_version": 1, "report_id": "worker-result-1",
              "task_id": "example/project#1:fix", "repo": "example/project",
              "branch": "fix/example", "owner": "owner", "builder": "builder",
              "publisher": "publisher", "head_sha": "a" * 40, "tested_sha": "a" * 40,
              "outcome": "BLOCKED", "report_kind": "WORKER_RESULT",
              "evidence": [{"path": "logs/check.txt", "text": text,
                            "sha256": hashlib.sha256(text.encode()).hexdigest()}],
              "checks": [{"argv": ["python3", "check.py"], "exit_code": 1,
                          "tested_sha": "a" * 40, "log_path": "logs/check.txt"}]}
    context = {k: report[k] for k in ("task_id", "repo", "branch", "owner", "builder", "publisher", "head_sha")}
    context.update(ticket_state="WAITING_EXTERNAL_OWNER", accepted_reports={},
                   report_sha256=hashlib.sha256(encoded(report)).hexdigest())
    return report, context


class WorkerReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE.exists():
            spec = importlib.util.spec_from_file_location("worker_report", MODULE)
            cls.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.mod)

    def test_transport_entry_exists(self):
        self.assertTrue(MODULE.is_file(), "existing consumer has no executable worker-report validation entry")

    def verify(self, report, context):
        return self.mod.verify_report(encoded(report), context)

    def rejected_mutation(self, mutate):
        report, context = fixture()
        mutate(report)
        context["report_sha256"] = hashlib.sha256(encoded(report)).hexdigest()
        with self.assertRaises(ValueError):
            self.verify(report, context)

    def test_legal_blocked_control_is_preserved_without_grant(self):
        report, context = fixture()
        result = self.verify(report, context)
        self.assertEqual(result["status"], "VALIDATED")
        self.assertEqual(result["ticket_state"], "WAITING_EXTERNAL_OWNER")
        self.assertEqual(result["reported_outcome"], "BLOCKED")
        self.assertEqual(result["reported_checks"][0]["exit_code"], 1)
        self.assertFalse(result["dispatch_authorized"])
        self.assertFalse(result["test_execution_attested"])
        self.assertEqual(result["public_mutations"], 0)

    def test_all_canonical_identity_mismatches_rejected(self):
        for key in ("task_id", "repo", "branch", "owner", "builder", "publisher", "head_sha"):
            with self.subTest(key=key):
                self.rejected_mutation(lambda report: report.update({key: "b" * 40 if key == "head_sha" else "different"}))

    def test_tested_head_mismatch_rejected(self):
        self.rejected_mutation(lambda report: report.update(tested_sha="b" * 40))
        self.rejected_mutation(lambda report: report["checks"][0].update(tested_sha="b" * 40))

    def test_corrupted_evidence_rejected(self):
        self.rejected_mutation(lambda report: report["evidence"][0].update(text="tampered"))

    def test_trusted_file_digest_required(self):
        report, context = fixture()
        context["report_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.verify(report, context)

    def test_duplicate_same_report_is_no_op(self):
        report, context = fixture()
        context["accepted_reports"][report["report_id"]] = context["report_sha256"]
        result = self.verify(report, context)
        self.assertEqual(result["status"], "ALREADY_CONSUMED")
        self.assertEqual(result["page_acknowledgement"], "NOT_REQUIRED_ALREADY_CONSUMED")

    def test_duplicate_conflicting_report_rejected(self):
        report, context = fixture()
        context["accepted_reports"][report["report_id"]] = "0" * 64
        with self.assertRaises(ValueError):
            self.verify(report, context)

    def test_duplicate_evidence_and_bad_paths_rejected(self):
        self.rejected_mutation(lambda report: report["evidence"].append(copy.deepcopy(report["evidence"][0])))
        for path in (".", "../escape", "/absolute", "a/../b", "a\\b", ".git/hooks/pre-commit", "a//b", "a/./b"):
            with self.subTest(path=path):
                self.rejected_mutation(lambda report: report["evidence"][0].update(path=path))

    def test_missing_log_and_invalid_exit_rejected(self):
        self.rejected_mutation(lambda report: report["checks"][0].update(log_path="missing"))
        self.rejected_mutation(lambda report: report["checks"][0].update(exit_code=True))

    def test_unknown_fields_and_empty_checks_rejected(self):
        self.rejected_mutation(lambda report: report.update(ready=True))
        self.rejected_mutation(lambda report: report.update(checks=[]))

    def test_duplicate_json_key_and_oversize_rejected(self):
        report, context = fixture()
        raw = encoded(report).replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
        context["report_sha256"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ValueError):
            self.mod.verify_report(raw, context)
        self.rejected_mutation(lambda report: report["evidence"][0].update(text="x" * 262145))

    def test_private_historical_control_not_delivery(self):
        report, context = fixture()
        report["report_kind"] = "PRIVATE_HISTORICAL_TRANSPORT_CONTROL"
        context["report_sha256"] = hashlib.sha256(encoded(report)).hexdigest()
        result = self.verify(report, context)
        self.assertEqual(result["report_kind"], "PRIVATE_HISTORICAL_TRANSPORT_CONTROL")
        self.assertFalse(result["natural_delivery_attested"])

    def test_cli_reads_only_and_never_executes_report_command(self):
        report, context = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "MUST_NOT_EXIST"
            report["checks"][0]["argv"] = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('bad')"]
            raw = encoded(report)
            context["report_sha256"] = hashlib.sha256(raw).hexdigest()
            (root / "report.json").write_bytes(raw)
            (root / "context.json").write_bytes(encoded(context))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            proc = subprocess.run([sys.executable, str(MODULE), "--report", str(root / "report.json"), "--context", str(root / "context.json")], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["status"], "VALIDATED")
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


if __name__ == "__main__":
    unittest.main()
