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

from rv_dna_in_review import (  # noqa: E402
    ensure_session_for_playlist,
    ensure_session_url,
    in_review_url,
    set_in_review,
)


class _FakeResponse:
    def __init__(self, status=200, body=b'{"id":"sess-1"}'):
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


def _recording_opener(seen, response):
    def opener(request, timeout=10):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return response

    return opener


def _http_error(status, body):
    def opener(request, timeout=10):
        raise urllib.error.HTTPError(
            request.full_url, status, "err", hdrs=None, fp=io.BytesIO(body)
        )

    return opener


class UrlTest(unittest.TestCase):
    def test_in_review_url(self):
        self.assertEqual(
            in_review_url("http://localhost:8000/", "sess-1"),
            "http://localhost:8000/sessions/sess-1/in-review",
        )

    def test_ensure_session_url(self):
        self.assertEqual(
            ensure_session_url("http://localhost:8000/"),
            "http://localhost:8000/sessions/ensure-for-playlist",
        )


class SetInReviewTest(unittest.TestCase):
    def test_puts_session_in_review_with_auth_header(self):
        seen = {}
        result = set_in_review(
            "http://localhost:8000",
            "sess-1",
            "45",
            "6990",
            {"Authorization": "Bearer tok"},
            opener=_recording_opener(seen, _FakeResponse(200, b"{}")),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.method, "PUT")
        self.assertEqual(result.url, "http://localhost:8000/sessions/sess-1/in-review")
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(
            seen["body"],
            {"in_review_playlist_id": "45", "in_review_version_id": "6990"},
        )
        self.assertEqual(seen["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(seen["headers"]["Content-type"], "application/json")

    def test_pinned_conflict_returns_false_with_body(self):
        result = set_in_review(
            "http://localhost:8000",
            "sess-1",
            "45",
            "999",
            {},
            opener=_http_error(409, b'{"detail":"pinned"}'),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 409)
        self.assertIn("pinned", result.response_body)

    def test_network_error_returns_false(self):
        def opener(request, timeout=10):
            raise urllib.error.URLError("down")

        result = set_in_review(
            "http://localhost:8000", "sess-1", "45", "6990", {}, opener=opener
        )
        self.assertFalse(result.ok)
        self.assertIsNone(result.status)
        self.assertIn("down", result.response_body)


class EnsureSessionTest(unittest.TestCase):
    def test_posts_project_and_playlist_and_returns_id(self):
        seen = {}
        session_id, result = ensure_session_for_playlist(
            "http://localhost:8000",
            "1",
            "45",
            {"Authorization": "Bearer tok"},
            opener=_recording_opener(
                seen, _FakeResponse(200, b'{"id":"sess-1","playlist_ids":["45"]}')
            ),
        )
        self.assertEqual(session_id, "sess-1")
        self.assertTrue(result.ok)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(
            seen["url"], "http://localhost:8000/sessions/ensure-for-playlist"
        )
        self.assertEqual(seen["body"], {"project_id": "1", "playlist_id": "45"})

    def test_reads_mongo_style_id(self):
        session_id, result = ensure_session_for_playlist(
            "http://localhost:8000",
            "1",
            "45",
            {},
            opener=lambda request, timeout=10: _FakeResponse(
                200, b'{"_id":"6af66ae771fa73675377d655","playlist_ids":["45"]}'
            ),
        )
        self.assertEqual(session_id, "6af66ae771fa73675377d655")
        self.assertTrue(result.ok)

    def test_http_error_returns_none(self):
        session_id, result = ensure_session_for_playlist(
            "http://localhost:8000", "1", "45", {}, opener=_http_error(401, b"no")
        )
        self.assertIsNone(session_id)
        self.assertEqual(result.status, 401)

    def test_response_without_id_returns_none(self):
        session_id, result = ensure_session_for_playlist(
            "http://localhost:8000",
            "1",
            "45",
            {},
            opener=lambda request, timeout=10: _FakeResponse(200, b"{}"),
        )
        self.assertIsNone(session_id)
        self.assertFalse(result.ok)
        self.assertIn("no session id", result.response_body)


if __name__ == "__main__":
    unittest.main()
