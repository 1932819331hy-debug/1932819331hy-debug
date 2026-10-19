import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("reviews", Path(__file__).with_name("review_metadata.py"))
reviews = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviews)

class ReviewCursorTests(unittest.TestCase):
    def response(self):
        return {"data": {"search": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}
    def test_restart_does_not_repeat_query_before_due(self):
        with tempfile.TemporaryDirectory() as folder:
            reviews.run(folder, self.response, 1000)
            result = reviews.run(folder, lambda: self.fail("duplicate query"), 1100)
            self.assertEqual(result["status"], "NOT_DUE")
    def test_graphql_error_preserves_saved_cursor(self):
        with tempfile.TemporaryDirectory() as folder:
            reviews.run(folder, self.response, 1000)
            before = (Path(folder) / "reviews.json").read_bytes()
            result = reviews.run(folder, lambda: {"errors": [{"message": "denied"}]}, 5000)
            self.assertEqual(result["status"], "ERROR")
            self.assertTrue(result["previous_cursor_preserved"])
            self.assertEqual((Path(folder) / "reviews.json").read_bytes(), before)
    def test_search_pagination_is_partial(self):
        data = self.response()
        data["data"]["search"]["pageInfo"]["hasNextPage"] = True
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(reviews.run(folder, lambda: data, 1000)["status"], "PARTIAL")
