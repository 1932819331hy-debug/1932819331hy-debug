import unittest
import subprocess
from unittest.mock import patch
from publication_preflight import classify_publication_path, read_repo_metadata

class PublicationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.target = {"http_status": 200, "data": {"full_name": "upstream/project", "fork": False, "permissions": {"push": False}}}
        self.fork = {"http_status": 200, "data": {"full_name": "worker/project", "fork": True, "parent": {"full_name": "upstream/project"}, "permissions": {"push": True}}}

    def check(self, target=None, fork=None):
        return classify_publication_path("upstream/project", "worker", target or self.target, fork or self.fork)

    def test_existing_writable_fork_is_a_candidate_not_publication_proof(self):
        result = self.check()
        self.assertTrue(result["path_candidate_present"])
        self.assertEqual(result["delivery_repo"], "worker/project")
        self.assertFalse(result["pr_publication_verified"])
        self.assertFalse(result["dispatch_authorized"])
        self.assertEqual(result["public_mutations"], 0)

    def test_missing_fork_blocks_engineering_without_creating_one(self):
        result = self.check(fork={"http_status": 404})
        self.assertEqual(result["status"], "FORK_REQUIRED_NO_VERIFIED_PATH")
        self.assertFalse(result["path_candidate_present"])

    def test_access_denial_is_error_not_absence_or_no_change(self):
        for status in [401, 403, 429, 503]:
            result = self.check(fork={"http_status": status})
            self.assertEqual(result["status"], "FORK_METADATA_ERROR")
            self.assertEqual(result["http_status"], status)
            self.assertFalse(result["path_candidate_present"])

    def test_wrong_fork_network_is_not_a_delivery_path(self):
        self.fork["data"]["parent"]["full_name"] = "other/project"
        self.assertEqual(self.check()["status"], "FORK_NETWORK_MISMATCH")

    def test_similar_name_without_actual_fork_is_rejected(self):
        self.fork["data"]["fork"] = False
        self.assertEqual(self.check()["status"], "NOT_A_FORK")

    def test_other_actors_fork_does_not_transfer_ownership(self):
        self.fork["data"]["full_name"] = "someone-else/project"
        self.assertEqual(self.check()["status"], "FORK_OWNER_MISMATCH")

    def test_read_only_or_unknown_permissions_do_not_pass(self):
        for permissions in [{"push": False}, {}, {"push": "true"}]:
            self.fork["data"]["permissions"] = permissions
            self.assertEqual(self.check()["status"], "FORK_WRITE_NOT_REPORTED")

    def test_wrong_target_response_is_rejected(self):
        self.target["data"]["full_name"] = "different/project"
        self.assertEqual(self.check()["status"], "TARGET_IDENTITY_MISMATCH")

    def test_source_network_allows_an_existing_indirect_fork(self):
        self.fork["data"]["parent"] = {"full_name": "middle/project"}
        self.fork["data"]["source"] = {"full_name": "upstream/project"}
        self.assertTrue(self.check()["path_candidate_present"])

    def test_empty_network_metadata_does_not_match_an_unrelated_fork(self):
        self.target["data"]["source"] = {"full_name": ""}
        self.fork["data"]["parent"] = None
        self.assertEqual(self.check()["status"], "FORK_NETWORK_MISMATCH")

    def test_reported_upstream_write_does_not_authorize_branch_takeover(self):
        self.target["data"]["permissions"]["push"] = True
        result = self.check(fork={"http_status": 404})
        self.assertEqual(result["status"], "DIRECT_WRITE_REPORTED")
        self.assertFalse(result["dispatch_authorized"])
        self.assertFalse(result["pr_publication_verified"])

    def test_bad_target_and_unavailable_metadata_stay_blocked(self):
        self.assertEqual(classify_publication_path("../project", "worker", self.target, self.fork)["status"], "INVALID_REPO_OR_ACTOR")
        self.assertEqual(self.check(target={"http_status": None})["status"], "TARGET_METADATA_ERROR")
        self.assertEqual(self.check(target={"http_status": 404})["status"], "TARGET_METADATA_ERROR")

    def test_malformed_metadata_does_not_crash_or_grant_access(self):
        for value in [[1], "invalid", None]:
            self.assertFalse(classify_publication_path("upstream/project", "worker", value, self.fork)["path_candidate_present"])
        self.fork["data"]["permissions"] = ["push"]
        self.assertFalse(self.check()["path_candidate_present"])

    def test_client_preserves_real_http_denials_and_not_found(self):
        for code in [401, 403, 404, 429]:
            proc = subprocess.CompletedProcess([], 1, '{"message":"not available"}', f"gh: not available (HTTP {code})")
            with patch("publication_preflight.subprocess.run", return_value=proc):
                self.assertEqual(read_repo_metadata("worker/project")["http_status"], code)

    def test_client_failure_does_not_echo_auth_output(self):
        proc = subprocess.CompletedProcess([], 1, "NOT_JSON", "sensitive auth diagnostic")
        with patch("publication_preflight.subprocess.run", return_value=proc):
            result = read_repo_metadata("worker/project")
        self.assertNotIn("sensitive", str(result))
        self.assertEqual(result["error_class"], "INVALID_JSON")

if __name__ == "__main__":
    unittest.main()

