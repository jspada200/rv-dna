#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
import io
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rv_dna_in_review import in_review_url, set_in_review  # noqa: E402


class _FakeResponse:
    def __init__(self, status=200, body=b'{"in_review":"6990"}'):
        self.status = status
        self._body = body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class InReviewUrlTest(unittest.TestCase):
    def test_builds_metadata_url(self):
        self.assertEqual(
            in_review_url("http://localhost:8000/", "45"),
            "http://localhost:8000/playlists/45/metadata",
        )


class SetInReviewTest(unittest.TestCase):
    def test_puts_json_body_and_auth_header(self):
        seen = {}

        def opener(request, timeout=10):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["headers"] = dict(request.header_items())
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(200)

        result = set_in_review(
            "http://localhost:8000",
            "45",
            "6990",
            {"Authorization": "Bearer tok"},
            opener=opener,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.url, "http://localhost:8000/playlists/45/metadata")
        self.assertEqual(result.request_body, '{"in_review": "6990"}')
        self.assertEqual(result.response_body, '{"in_review":"6990"}')
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(seen["body"], {"in_review": "6990"})
        self.assertEqual(seen["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(seen["headers"]["Content-type"], "application/json")

    def test_http_error_returns_false_with_body(self):
        def opener(request, timeout=10):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "bad",
                hdrs=None,
                fp=io.BytesIO(b'{"detail":"not on playlist"}'),
            )

        result = set_in_review(
            "http://localhost:8000",
            "45",
            "999",
            {},
            opener=opener,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 400)
        self.assertIn("not on playlist", result.response_body)

    def test_network_error_returns_false(self):
        def opener(request, timeout=10):
            raise urllib.error.URLError("down")

        result = set_in_review(
            "http://localhost:8000",
            "45",
            "6990",
            {},
            opener=opener,
        )
        self.assertFalse(result.ok)
        self.assertIsNone(result.status)
        self.assertIn("down", result.response_body)


if __name__ == "__main__":
    unittest.main()
