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
    DnaSessionInfo,
    choose_playlist,
    ensure_session_for_playlist,
    get_playlist_version_ids,
    get_session,
    in_review_url,
    list_project_sessions,
    parse_sg_context,
    set_in_review,
    suggest_session,
)

API = "http://localhost:8000"


class _FakeResponse:
    def __init__(self, status=200, body=b"{}"):
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


def _opener(seen, status=200, body=b"{}"):
    def opener(request, timeout=10):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8")) if request.data else None
        return _FakeResponse(status, body)

    return opener


def _http_error(status, body=b""):
    def opener(request, timeout=10):
        raise urllib.error.HTTPError(
            request.full_url, status, "err", hdrs=None, fp=io.BytesIO(body)
        )

    return opener


def _session(session_id="s1", playlist_ids=("45", "46"), name="Monday", **kw):
    return DnaSessionInfo(
        id=session_id, name=name, playlist_ids=tuple(playlist_ids), **kw
    )


class ParseSgContextTest(unittest.TestCase):
    def test_parses_playlist(self):
        context = parse_sg_context("771|Playlist|TEST_REVIEW_001|157|valid")
        self.assertTrue(context.is_playlist)
        self.assertEqual(context.entity_id, "771")
        self.assertEqual(context.name, "TEST_REVIEW_001")
        self.assertEqual(context.project_id, "157")

    def test_name_may_contain_separator(self):
        context = parse_sg_context("771|Playlist|A|B|157|valid")
        self.assertEqual(context.name, "A|B")
        self.assertEqual(context.project_id, "157")

    def test_cleared_or_invalid_context_is_none(self):
        self.assertIsNone(parse_sg_context("-1||||"))
        self.assertIsNone(parse_sg_context("771|Playlist|X|157|invalid"))
        self.assertIsNone(parse_sg_context(""))
        self.assertIsNone(parse_sg_context("garbage"))

    def test_other_entity_types_are_not_playlists(self):
        self.assertFalse(parse_sg_context("12|Shot|TST_010|157|valid").is_playlist)


class DnaSessionInfoTest(unittest.TestCase):
    def test_from_api_reads_mongo_id(self):
        info = DnaSessionInfo.from_api(
            {"_id": "abc", "name": "Monday", "playlist_ids": [45, "46"]}
        )
        self.assertEqual(info.id, "abc")
        self.assertEqual(info.playlist_ids, ("45", "46"))

    def test_implicit_session_detection(self):
        self.assertTrue(_session(playlist_ids=("45",), name="Playlist 45").is_implicit)
        self.assertFalse(_session(playlist_ids=("45",), name="Monday").is_implicit)
        self.assertFalse(
            _session(playlist_ids=("45",), name="Playlist 45", external_id="x").is_implicit
        )

    def test_suggest_session_skips_implicit(self):
        implicit = _session("i", ("45",), name="Playlist 45")
        named = _session("n", ("44", "45"), name="Monday")
        self.assertEqual(suggest_session([implicit, named], "45"), named)
        self.assertIsNone(suggest_session([implicit], "45"))


class ChoosePlaylistTest(unittest.TestCase):
    def has(self, mapping):
        return lambda playlist_id: mapping.get(playlist_id, False)

    def test_no_session_uses_playlist_when_version_is_on_it(self):
        target = choose_playlist("7", "45", True, None, self.has({"45": True}))
        self.assertEqual(target.playlist_id, "45")

    def test_no_session_skips_version_not_on_playlist(self):
        target = choose_playlist("7", "45", True, None, self.has({}))
        self.assertIsNone(target.playlist_id)
        self.assertIn("not on playlist 45", target.skip_reason)
        self.assertFalse(target.notify)

    def test_rv_playlist_must_be_in_session(self):
        target = choose_playlist("7", "99", True, _session(), self.has({"99": True}))
        self.assertIsNone(target.playlist_id)
        self.assertIn("not in DNA session Monday", target.skip_reason)
        self.assertTrue(target.notify)

    def test_rv_playlist_in_session(self):
        target = choose_playlist("7", "46", True, _session(), self.has({"46": True}))
        self.assertEqual(target.playlist_id, "46")

    def test_without_rv_playlist_searches_session(self):
        target = choose_playlist(
            "7", "45", False, _session(), self.has({"45": False, "46": True})
        )
        self.assertEqual(target.playlist_id, "46")

    def test_search_prefers_playlist_already_in_review(self):
        session = _session(in_review_playlist_id="46")
        target = choose_playlist(
            "7", "45", False, session, self.has({"45": True, "46": True})
        )
        self.assertEqual(target.playlist_id, "46")

    def test_search_ignores_sign_in_playlist_outside_session(self):
        seen = []

        def has(playlist_id):
            seen.append(playlist_id)
            return False

        target = choose_playlist("7", "99", False, _session(), has)
        self.assertIsNone(target.playlist_id)
        self.assertNotIn("99", seen)

    def test_lookup_failure_skips(self):
        target = choose_playlist("7", "45", True, None, lambda _p: None)
        self.assertIsNone(target.playlist_id)
        self.assertIn("could not read", target.skip_reason)


class HttpTest(unittest.TestCase):
    def test_set_in_review(self):
        seen = {}
        result = set_in_review(
            API, "s1", "45", "6990", {"Authorization": "Bearer t"}, opener=_opener(seen)
        )
        self.assertTrue(result.ok)
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(seen["url"], in_review_url(API, "s1"))
        self.assertEqual(
            seen["body"],
            {"in_review_playlist_id": "45", "in_review_version_id": "6990"},
        )
        self.assertEqual(seen["headers"]["Authorization"], "Bearer t")

    def test_pinned_conflict(self):
        result = set_in_review(API, "s1", "45", "1", {}, opener=_http_error(409, b"pinned"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 409)

    def test_network_error(self):
        def opener(request, timeout=10):
            raise urllib.error.URLError("down")

        result = set_in_review(API, "s1", "45", "1", {}, opener=opener)
        self.assertIsNone(result.status)
        self.assertIn("down", result.response_body)

    def test_list_project_sessions(self):
        seen = {}
        body = json.dumps(
            [{"_id": "a", "name": "Monday", "playlist_ids": ["45"]}, {"bad": 1}]
        ).encode()
        sessions, result = list_project_sessions(API, "157", {}, opener=_opener(seen, body=body))
        self.assertEqual([s.id for s in sessions], ["a"])
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["url"], f"{API}/projects/157/sessions")
        self.assertIsNone(seen["body"])

    def test_get_session_404(self):
        session, result = get_session(API, "gone", {}, opener=_http_error(404))
        self.assertIsNone(session)
        self.assertEqual(result.status, 404)

    def test_get_playlist_version_ids(self):
        seen = {}
        body = json.dumps([{"id": 7056}, {"id": "7057"}, {"code": "x"}]).encode()
        ids, _ = get_playlist_version_ids(API, "574", {}, opener=_opener(seen, body=body))
        self.assertEqual(ids, frozenset({"7056", "7057"}))
        self.assertEqual(seen["url"], f"{API}/playlists/574/versions")

    def test_get_playlist_version_ids_bad_body(self):
        ids, result = get_playlist_version_ids(API, "574", {}, opener=_opener({}, body=b"{}"))
        self.assertIsNone(ids)
        self.assertFalse(result.ok)

    def test_ensure_session_for_playlist(self):
        seen = {}
        session_id, _ = ensure_session_for_playlist(
            API, "157", "574", {}, opener=_opener(seen, body=b'{"_id":"abc"}')
        )
        self.assertEqual(session_id, "abc")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"], {"project_id": "157", "playlist_id": "574"})

    def test_ensure_session_without_id(self):
        session_id, result = ensure_session_for_playlist(
            API, "157", "574", {}, opener=_opener({}, body=b"{}")
        )
        self.assertIsNone(session_id)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
