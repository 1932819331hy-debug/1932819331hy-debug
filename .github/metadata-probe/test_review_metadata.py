"""Review collector regressions with real file persistence and paginated API fixtures."""
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("reviews", Path(__file__).with_name("review_metadata.py"))
reviews = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviews)
ACCOUNT = "1932819331hy-debug"


def page(nodes=(), after=None):
    return {"nodes": list(nodes), "totalCount": len(nodes),
            "pageInfo": {"hasNextPage": after is not None, "endCursor": after}}


def actor(login="reviewer", kind="User"):
    return {"login": login, "__typename": kind}


def pr(number=1):
    return {"id": "PR_" + str(number), "url": "https://github.com/owner/repo/pull/" + str(number),
            "number": number, "repository": {"nameWithOwner": "owner/repo"},
            "headRefOid": "head", "state": "OPEN", "author": actor(ACCOUNT),
            "reviews": page(), "reviewThreads": page(), "comments": page(),
            "commits": {"nodes": [{"commit": {"oid": "head", "statusCheckRollup": None}}]}}


def response(items=(), after=None):
    return {"data": {"user": {"pullRequests": page(items, after)}}}


class ReviewCursorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def run_probe(self, transport, now=1000, **kwargs):
        return reviews.run(self.path, transport, now, **kwargs)

    def seed(self, items=()):
        result = self.run_probe(lambda *args: response(items))
        self.assertEqual(result["status"], "OK")
        return (self.path / "reviews.json").read_bytes()

    def test_restart_does_not_repeat_query_before_due(self):
        self.seed()
        result = self.run_probe(lambda *args: self.fail("duplicate query"), 1100)
        self.assertEqual(result["status"], "NOT_DUE")

    def test_legacy_no_argument_fixture_remains_supported(self):
        result = self.run_probe(lambda: {"data": {"search": page()}})
        self.assertEqual(result["status"], "OK")

    def test_graphql_error_preserves_saved_cursor_and_records_failure(self):
        before = self.seed()
        result = self.run_probe(lambda *args: {"errors": [{"message": "denied"}]}, 5000)
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["previous_cursor_preserved"])
        self.assertEqual((self.path / "reviews.json").read_bytes(), before)
        self.assertEqual(json.loads((self.path / "reviews.attempt.json").read_text())["status"], "ERROR")
        self.assertTrue((self.path / "reviews.retry.json").exists())

    def test_partial_does_not_overwrite_verified_snapshot(self):
        before = self.seed([pr()])
        result = self.run_probe(lambda: {"data": {"search": page([pr(2)], "cursor")}}, 5000)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual((self.path / "reviews.json").read_bytes(), before)
        self.assertTrue(result["remaining_cursors"])

    def test_account_open_prs_are_paginated_beyond_search_cap(self):
        first = response([pr()], "open-next")
        last = response([pr(2)])
        first["data"]["user"]["pullRequests"]["totalCount"] = 2
        last["data"]["user"]["pullRequests"]["totalCount"] = 2
        pages = iter([first, last])
        result = self.run_probe(lambda query, variables: next(pages))
        self.assertEqual(result["status"], "OK")
        self.assertEqual(len(result["current"]), 2)
        self.assertEqual(result["coverage"]["open_pr_pages"], 2)
        self.assertIn("pullRequests", reviews.QUERY)
        self.assertNotIn("search(", reviews.QUERY)

    def test_all_nested_connections_and_thread_replies_are_paginated(self):
        item = pr()
        item["reviews"] = page([], "reviews-next")
        item["comments"] = page([], "comments-next")
        item["reviewThreads"] = page([{"id": "THREAD_1", "isResolved": False,
                                              "isOutdated": False,
                                              "comments": page([], "replies-next")}], "threads-next")
        item["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = {
            "id": "ROLLUP_1", "state": "PENDING", "contexts": page([], "ci-next")}
        continuation = {
            "reviews-next": {"reviews": page([{"id": "REVIEW_1", "state": "APPROVED", "author": actor()}])},
            "comments-next": {"comments": page([{"id": "COMMENT_1", "body": "human text", "author": actor()}])},
            "threads-next": {"reviewThreads": dict(page([]), totalCount=1)},
            "replies-next": {"comments": page([{"id": "REPLY_1", "body": "fix this", "author": actor()}])},
            "ci-next": {"contexts": page([{"id": "CHECK_1", "name": "unit", "conclusion": "SUCCESS"}])}}

        def transport(query, variables):
            if "after" in variables and variables["after"]:
                return {"data": {"node": continuation[variables["after"]]}}
            return response([item])

        result = self.run_probe(transport)
        self.assertEqual(result["status"], "OK")
        current = result["current"][item["url"]]
        self.assertEqual(current["reviews"]["nodes"][0]["id"], "REVIEW_1")
        self.assertEqual(current["comments"]["nodes"][0]["body"], "human text")
        self.assertEqual(current["threads"]["nodes"][0]["comments"]["nodes"][0]["id"], "REPLY_1")
        self.assertEqual(current["ci"]["contexts"]["nodes"][0]["id"], "CHECK_1")
        self.assertEqual(result["coverage"]["requests"], 6)

    def test_budget_is_partial_and_restart_resumes_pending_cursor(self):
        item = pr()
        item["reviews"] = page([], "reviews-next")
        result = self.run_probe(lambda *args: response([item]), max_requests=1)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse((self.path / "reviews.json").exists())
        self.assertEqual(result["remaining_cursors"][0]["after"], "reviews-next")

        def resume(query, variables):
            self.assertEqual(variables["after"], "reviews-next")
            return {"data": {"node": {"reviews": page()}}}

        result = self.run_probe(resume, 1001, max_requests=1)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["resumed"])
        self.assertFalse((self.path / "reviews.retry.json").exists())

    def test_missing_cursor_never_blesses_incomplete_snapshot(self):
        data = response()
        data["data"]["user"]["pullRequests"]["pageInfo"] = {"hasNextPage": True}
        result = self.run_probe(lambda *args: data)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse((self.path / "reviews.json").exists())

    def test_previously_tracked_merged_pr_is_directly_refreshed(self):
        item = pr()
        self.seed([item])
        item["state"] = "MERGED"

        def transport(query, variables):
            if variables.get("id"):
                self.assertEqual(variables["id"], "PR_1")
                return {"data": {"node": item}}
            return response()

        result = self.run_probe(transport, 5000)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["current"][item["url"]]["state"], "MERGED")
        self.assertEqual(result["missing_from_open_search"], [item["url"]])

    def test_explicit_settled_tracking_input_can_stop_closed_pr_refresh(self):
        self.seed([pr()])
        result = self.run_probe(lambda *args: response(), 5000, tracked_prs=[])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["current"], {})

    def test_page_ticket_url_can_track_closed_pr_not_in_prior_cache(self):
        item = pr(7)
        item["state"] = "CLOSED"

        def transport(query, variables):
            if variables.get("number"):
                self.assertEqual((variables["owner"], variables["name"], variables["number"]),
                                 ("owner", "repo", 7))
                return {"data": {"repository": {"pullRequest": item}}}
            return response()

        result = self.run_probe(transport, tracked_prs=[item["url"]])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["current"][item["url"]]["state"], "CLOSED")

    def test_timestamp_only_changes_do_not_emit_repeat_review_work(self):
        item = pr()
        item["comments"] = page([{"id": "COMMENT_1", "body": "please test null", "updatedAt": "old", "author": actor()}])
        self.seed([item])
        item["comments"]["nodes"][0]["updatedAt"] = "new"
        result = self.run_probe(lambda *args: response([item]), 5000)
        self.assertEqual(result["changed_prs"], [])
        item["comments"]["nodes"][0]["body"] = "please test denied authorization"
        result = self.run_probe(lambda *args: response([item]), 9000)
        self.assertEqual(result["changed_prs"], [item["url"]])

    def test_bot_resolved_outdated_and_old_head_are_not_current_actionable_review(self):
        item = pr()
        item["reviews"] = page([
            {"id": "BOT", "state": "CHANGES_REQUESTED", "body": "P0 suggestion", "author": actor("helper[bot]", "Bot"), "commit": {"oid": "head"}},
            {"id": "OLD", "state": "CHANGES_REQUESTED", "body": "previous head", "author": actor(), "commit": {"oid": "old"}},
            {"id": "HUMAN", "state": "CHANGES_REQUESTED", "body": "test tenant isolation", "author": actor(), "commit": {"oid": "head"}}])
        item["reviewThreads"] = page([
            {"id": "RESOLVED", "isResolved": True, "isOutdated": False, "comments": page([{"id": "R", "body": "done", "author": actor()}])},
            {"id": "OUTDATED", "isResolved": False, "isOutdated": True, "comments": page([{"id": "O", "body": "old", "author": actor()}])}])
        result = self.run_probe(lambda *args: response([item]))
        candidates = result["current"][item["url"]]["review_candidates"]
        self.assertEqual([candidate["event_id"] for candidate in candidates if candidate["actionable"]], ["HUMAN"])
        by_id = {candidate["event_id"]: candidate for candidate in candidates}
        self.assertEqual(by_id["BOT"]["classification"], "BOT_ADVISORY")
        self.assertEqual(by_id["OLD"]["classification"], "STALE_HEAD_REQUIRES_REVALIDATION")
        self.assertTrue(all(candidate.get("priority") != "P0" for candidate in candidates))
        self.assertEqual(by_id["HUMAN"]["body"], "test tenant isolation")
        self.assertEqual(by_id["HUMAN"]["review_commit_sha"], "head")

    def test_commented_review_does_not_silence_outstanding_changes_request(self):
        item = pr()
        item["reviews"] = page([
            {"id": "REQUEST", "state": "CHANGES_REQUESTED", "body": "fix permission", "author": actor(), "commit": {"oid": "head"}},
            {"id": "COMMENT", "state": "COMMENTED", "body": "one more thought", "author": actor(), "commit": {"oid": "head"}}])
        result = self.run_probe(lambda *args: response([item]))
        active = [candidate["event_id"] for candidate in result["current"][item["url"]]["review_candidates"] if candidate["actionable"]]
        self.assertEqual(active, ["REQUEST"])

    def test_migration_does_not_reclassify_old_metadata_as_new_work(self):
        item = pr()
        (self.path / "reviews.json").write_text(json.dumps({
            "status": "OK", "checked_at_unix": 0, "current": {item["url"]: {"head_sha": "head"}},
            "fingerprints": {item["url"]: "legacy-normalization"}}))
        result = self.run_probe(lambda *args: response([item]), 5000)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["baseline"])
        self.assertEqual(result["changed_prs"], [])

    def test_fresh_legacy_ok_snapshot_does_not_suppress_complete_collection(self):
        (self.path / "reviews.json").write_text(json.dumps({
            "status": "OK", "checked_at_unix": 900, "current": {}, "fingerprints": {}}))
        result = self.run_probe(lambda *args: response([pr()]), 1000)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["baseline"])

    def test_unverified_current_schema_snapshot_does_not_suppress_collection(self):
        (self.path / "reviews.json").write_text(json.dumps({
            "status": "OK", "schema": reviews.SCHEMA, "checked_at_unix": 900,
            "current": {}, "fingerprints": {}}))
        result = self.run_probe(lambda *args: response([pr()]), 1000)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["baseline"])

    def test_total_count_mismatch_is_incomplete_even_with_false_page_info(self):
        item = pr()
        item["reviews"]["totalCount"] = 7
        result = self.run_probe(lambda *args: response([item]))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse((self.path / "reviews.json").exists())

    def test_partial_connection_failure_can_retry_without_duplicate_jobs(self):
        item = pr()
        item["reviews"] = page([], "reviews-next")
        item["comments"] = page([], "comments-next")
        first = self.run_probe(lambda *args: response([item]), max_requests=1)
        self.assertEqual(first["status"], "PARTIAL")
        failed = self.run_probe(lambda *args: {"errors": [{"message": "rate limited"}]}, 1001)
        self.assertEqual(failed["status"], "ERROR")
        after = []
        def transport(query, variables):
            after.append(variables["after"])
            field = "reviews" if variables["after"] == "reviews-next" else "comments"
            return {"data": {"node": {field: page()}}}
        result = self.run_probe(transport, 1002)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(after, ["reviews-next", "comments-next"])

    def test_resumed_completion_keeps_oldest_read_freshness(self):
        item = pr()
        item["reviews"] = page([], "reviews-next")
        result = self.run_probe(lambda *args: response([item]), 1000, max_requests=1)
        self.assertEqual(result["status"], "PARTIAL")
        result = self.run_probe(lambda *args: {"data": {"node": {"reviews": page()}}}, 4599)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["checked_at_unix"], 1000)
        self.assertEqual(result["completed_at_unix"], 4599)
        result = self.run_probe(lambda *args: response([pr()]), 4600)
        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["resumed"])

    def test_root_total_count_mismatch_preserves_verified_snapshot(self):
        before = self.seed([pr()])
        data = response()
        data["data"]["user"]["pullRequests"]["totalCount"] = 19
        result = self.run_probe(lambda *args: data, 5000)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual((self.path / "reviews.json").read_bytes(), before)

    def test_resumed_root_pagination_checks_unique_count(self):
        first = response([pr()], "open-next")
        first["data"]["user"]["pullRequests"]["totalCount"] = 3
        result = self.run_probe(lambda *args: first, max_requests=1)
        self.assertEqual(result["status"], "PARTIAL")
        last = response()
        last["data"]["user"]["pullRequests"]["totalCount"] = 3
        result = self.run_probe(lambda *args: last, 1001)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("OPEN_PR_COUNT_MISMATCH", result["warnings"])

    def test_root_count_change_during_pagination_is_partial(self):
        first = response([pr()], "open-next")
        first["data"]["user"]["pullRequests"]["totalCount"] = 3
        last = response([pr(2)])
        last["data"]["user"]["pullRequests"]["totalCount"] = 2
        responses = iter([first, last])
        result = self.run_probe(lambda *args: next(responses))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("OPEN_PR_COUNT_CHANGED_DURING_COLLECTION", result["warnings"])

    def test_private_pr_bodies_and_urls_never_enter_public_artifacts(self):
        item = pr(999)
        item["url"] = "https://github.com/private-owner/SECRET_PRIVATE_REPOSITORY/pull/999"
        item["repository"] = {"nameWithOwner": "private-owner/SECRET_PRIVATE_REPOSITORY", "isPrivate": True}
        item["comments"] = page([{"id": "PRIVATE_COMMENT", "body": "SECRET_PRIVATE_BODY", "author": actor()}])
        result = self.run_probe(lambda *args: response([item]))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["coverage"]["private_prs_skipped"], 1)
        self.assertEqual(result["coverage"]["scope"], "PUBLIC_ONLY")
        self.assertIn("PRIVATE_PR_REQUIRES_PRIVATE_CONTROLLER_FALLBACK", result["warnings"])
        for artifact in self.path.iterdir():
            raw = artifact.read_text()
            self.assertNotIn("SECRET_PRIVATE_REPOSITORY", raw)
            self.assertNotIn("SECRET_PRIVATE_BODY", raw)
            self.assertNotIn("PRIVATE_COMMENT", raw)

    def test_unverified_tracked_selector_is_redacted_when_budget_ends(self):
        selector = "https://github.com/PRIVATE_SELECTOR_SENTINEL/repo/pull/7"
        result = self.run_probe(lambda *args: response(), max_requests=1, tracked_prs=[selector])
        self.assertEqual(result["status"], "PARTIAL")
        for artifact in self.path.iterdir():
            self.assertNotIn("PRIVATE_SELECTOR_SENTINEL", artifact.read_text())
        self.assertNotIn("PRIVATE_SELECTOR_SENTINEL", json.dumps(result))
        item = pr(7)
        item["url"] = selector
        item["repository"] = {"nameWithOwner": "PRIVATE_SELECTOR_SENTINEL/repo", "isPrivate": True}
        result = self.run_probe(lambda *args: {"data": {"repository": {"pullRequest": item}}},
                                1001, tracked_prs=[selector])
        self.assertEqual(result["status"], "PARTIAL")
        for artifact in self.path.iterdir():
            self.assertNotIn("PRIVATE_SELECTOR_SENTINEL", artifact.read_text())

    def test_unverified_tracked_lookup_error_is_redacted_from_public_receipt(self):
        selector = "https://github.com/PRIVATE_ERROR_SENTINEL/repo/pull/7"
        def transport(query, variables):
            if variables.get("number"):
                raise RuntimeError("Lookup denied PRIVATE_ERROR_SENTINEL repository")
            return response()
        result = self.run_probe(transport, tracked_prs=[selector])
        self.assertEqual(result["status"], "ERROR")
        self.assertNotIn("PRIVATE_ERROR_SENTINEL", json.dumps(result))
        for artifact in self.path.iterdir():
            self.assertNotIn("PRIVATE_ERROR_SENTINEL", artifact.read_text())

    def test_query_stays_below_github_nested_node_limit(self):
        # A 100 PR x 100 thread x 100 reply query is rejected before execution.
        root = int(re.search(r"pullRequests\([^)]*first:\s*(\d+)", reviews.QUERY).group(1))
        threads = int(re.search(r"reviewThreads\(first:\s*(\d+)", reviews.QUERY).group(1))
        replies_fragment = reviews.QUERY.split("fragment ThreadPage ", 1)[1]
        replies = int(re.search(r"comments\(first:\s*(\d+)", replies_fragment).group(1))
        # Leave room for reply authors, review commits, replyTo and CI nodes.
        self.assertLessEqual(root * threads * replies, 100000)

    def test_ci_from_different_commit_is_not_verified_complete(self):
        item = pr()
        item["commits"]["nodes"][0]["commit"]["oid"] = "old"
        result = self.run_probe(lambda *args: response([item]))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse((self.path / "reviews.json").exists())


if __name__ == "__main__":
    unittest.main()
