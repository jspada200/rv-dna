#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rv_dna_auth import (  # noqa: E402
    DnaSession,
    InvalidPayloadError,
    SessionStore,
    normalize_dna_url,
    parse_auth_payload,
    same_origin,
    signin_url,
    url_origin,
)

DNA_URL = "http://localhost:5173"


def make_payload(**overrides):
    payload = {
        "version": 2,
        "token": "artist@studio.com",
        "user": {
            "id": "artist@studio.com",
            "email": "artist@studio.com",
            "name": "artist",
        },
        "authProvider": "none",
        "apiBaseUrl": "http://localhost:8000",
        "project": {"id": "1", "name": "Demo Show"},
        "playlist": {"id": "45", "name": "Monday Dailies"},
    }
    payload.update(overrides)
    return json.dumps(payload)


def make_session(**overrides):
    return parse_auth_payload(make_payload(**overrides), DNA_URL)


class FakeSettings:
    def __init__(self):
        self.values = {}

    def read(self, name, default):
        return self.values.get(name, default)

    def write(self, name, value):
        self.values[name] = value


class NormalizeDnaUrlTest(unittest.TestCase):
    def test_keeps_valid_url_and_drops_trailing_slash(self):
        self.assertEqual(
            normalize_dna_url(" https://DNA.example.com/ "), "https://dna.example.com"
        )

    def test_keeps_path_prefix(self):
        self.assertEqual(
            normalize_dna_url("https://studio.com/dna/?x=1#y"),
            "https://studio.com/dna",
        )

    def test_defaults_local_hosts_to_http(self):
        self.assertEqual(normalize_dna_url("localhost:5173"), "http://localhost:5173")
        self.assertEqual(normalize_dna_url("127.0.0.1:5173"), "http://127.0.0.1:5173")

    def test_defaults_remote_hosts_to_https(self):
        self.assertEqual(normalize_dna_url("dna.example.com"), "https://dna.example.com")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            normalize_dna_url("  ")
        with self.assertRaises(ValueError):
            normalize_dna_url(None)

    def test_rejects_other_schemes(self):
        with self.assertRaises(ValueError):
            normalize_dna_url("ftp://dna.example.com")
        with self.assertRaises(ValueError):
            normalize_dna_url("file:///tmp/index.html")


class SigninUrlTest(unittest.TestCase):
    def test_appends_route(self):
        self.assertEqual(
            signin_url("http://localhost:5173/"), "http://localhost:5173/desktop-signin"
        )


class OriginTest(unittest.TestCase):
    def test_strips_default_ports(self):
        self.assertEqual(url_origin("https://dna.example.com:443/a"), "https://dna.example.com")
        self.assertEqual(url_origin("http://dna.example.com:80"), "http://dna.example.com")

    def test_keeps_custom_port(self):
        self.assertEqual(url_origin("http://localhost:5173/x"), "http://localhost:5173")

    def test_ipv6_host(self):
        self.assertEqual(url_origin("http://[::1]:5173/"), "http://[::1]:5173")

    def test_invalid_port_is_ignored(self):
        self.assertEqual(url_origin("http://localhost:abc/"), "http://localhost")

    def test_same_origin(self):
        self.assertTrue(
            same_origin("http://localhost:5173/desktop-signin", "http://LOCALHOST:5173")
        )
        self.assertFalse(same_origin("http://localhost:5173", "http://localhost:5174"))
        self.assertFalse(same_origin("http://localhost:5173", "https://localhost:5173"))


class ParseAuthPayloadTest(unittest.TestCase):
    def test_parses_valid_payload(self):
        session = make_session()
        self.assertEqual(session.token, "artist@studio.com")
        self.assertEqual(session.email, "artist@studio.com")
        self.assertEqual(session.user_id, "artist@studio.com")
        self.assertEqual(session.name, "artist")
        self.assertIsNone(session.picture)
        self.assertEqual(session.auth_provider, "none")
        self.assertEqual(session.api_base_url, "http://localhost:8000")
        self.assertEqual(session.dna_url, DNA_URL)
        self.assertEqual(session.project.id, "1")
        self.assertEqual(session.project.name, "Demo Show")
        self.assertEqual(session.playlist.id, "45")
        self.assertEqual(session.playlist.name, "Monday Dailies")

    def test_user_id_falls_back_to_email(self):
        session = make_session(user={"email": "a@b.com"})
        self.assertEqual(session.user_id, "a@b.com")

    def test_rejects_bad_json(self):
        with self.assertRaises(InvalidPayloadError):
            parse_auth_payload("{nope", DNA_URL)
        with self.assertRaises(InvalidPayloadError):
            parse_auth_payload(None, DNA_URL)

    def test_rejects_non_object(self):
        with self.assertRaises(InvalidPayloadError):
            parse_auth_payload("[]", DNA_URL)

    def test_rejects_wrong_version(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(version=1)

    def test_rejects_missing_user(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(user="artist")

    def test_rejects_missing_email(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(user={"id": "1"})

    def test_rejects_empty_token(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(token="  ")

    def test_rejects_unknown_provider(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(authProvider="okta")

    def test_rejects_bad_api_url(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(apiBaseUrl="ftp://x")
        with self.assertRaises(InvalidPayloadError):
            make_session(apiBaseUrl=None)

    def test_rejects_missing_project(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(project=None)

    def test_rejects_missing_playlist_id(self):
        with self.assertRaises(InvalidPayloadError):
            make_session(playlist={"name": "Dailies"})


class DnaSessionTest(unittest.TestCase):
    def test_auth_headers(self):
        self.assertEqual(
            make_session().auth_headers(), {"Authorization": "Bearer artist@studio.com"}
        )

    def test_display_name(self):
        self.assertEqual(make_session().display_name, "artist")
        self.assertEqual(
            make_session(user={"email": "a@b.com"}).display_name, "a@b.com"
        )

    def test_json_round_trip(self):
        session = make_session()
        self.assertEqual(DnaSession.from_json(session.to_json()), session)


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.settings = FakeSettings()
        self.store = SessionStore(self.settings.read, self.settings.write)

    def test_url_is_none_by_default(self):
        self.assertIsNone(self.store.url)

    def test_set_url_normalizes(self):
        self.assertEqual(self.store.set_url("localhost:5173/"), DNA_URL)
        self.assertEqual(self.store.url, DNA_URL)

    def test_invalid_stored_url_reads_as_none(self):
        self.settings.values["url"] = "ftp://x"
        self.assertIsNone(self.store.url)

    def test_set_url_rejects_invalid(self):
        with self.assertRaises(ValueError):
            self.store.set_url("ftp://x")

    def test_save_and_load_session(self):
        self.store.set_url(DNA_URL)
        session = make_session()
        self.store.save_session(session)
        self.assertEqual(self.store.load_session(), session)

    def test_no_session_by_default(self):
        self.assertIsNone(self.store.load_session())

    def test_clear_session(self):
        self.store.set_url(DNA_URL)
        self.store.save_session(make_session())
        self.store.clear_session()
        self.assertIsNone(self.store.load_session())

    def test_changing_url_signs_out(self):
        self.store.set_url(DNA_URL)
        self.store.save_session(make_session())
        self.store.set_url("https://dna.example.com")
        self.assertIsNone(self.store.load_session())

    def test_setting_same_url_keeps_session(self):
        self.store.set_url(DNA_URL)
        self.store.save_session(make_session())
        self.store.set_url("http://localhost:5173/")
        self.assertIsNotNone(self.store.load_session())

    def test_session_for_other_url_is_ignored(self):
        self.store.set_url("https://dna.example.com")
        self.settings.values["session"] = make_session().to_json()
        self.assertIsNone(self.store.load_session())

    def test_corrupt_session_is_ignored(self):
        self.store.set_url(DNA_URL)
        self.settings.values["session"] = "{not json"
        self.assertIsNone(self.store.load_session())
        self.settings.values["session"] = json.dumps({"token": "x"})
        self.assertIsNone(self.store.load_session())


if __name__ == "__main__":
    unittest.main()
