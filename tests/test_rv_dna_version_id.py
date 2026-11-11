#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rv_dna_version_id import (  # noqa: E402
    InvalidVersionIdConfigError,
    extract_version_id,
    load_version_id_paths,
    validate_step,
)

SHOTGRID_URL = (
    "https://shotgun.example.com/file_serve/version/6990/mp4"
    "?Content-Disposition=filename%3Dclip.mp4"
)


class ValidateStepTest(unittest.TestCase):
    def test_defaults_as_string(self):
        step = validate_step({"source": "property", "name": "File"})
        self.assertEqual(step["as"], "string")
        self.assertIsNone(step["match"])

    def test_rejects_bad_source(self):
        with self.assertRaises(InvalidVersionIdConfigError):
            validate_step({"source": "env", "name": "File"})

    def test_rejects_missing_name(self):
        with self.assertRaises(InvalidVersionIdConfigError):
            validate_step({"source": "metadata"})


class ExtractVersionIdTest(unittest.TestCase):
    def test_extracts_shotgrid_file_serve_url(self):
        version_id = extract_version_id(properties={"File": SHOTGRID_URL})
        self.assertEqual(version_id, "6990")

    def test_falls_back_to_metadata_version_id(self):
        version_id = extract_version_id(metadata={"version_id": 42})
        self.assertEqual(version_id, "42")

    def test_falls_back_to_property_version_id(self):
        version_id = extract_version_id(properties={"version_id": "55"})
        self.assertEqual(version_id, "55")

    def test_first_matching_step_wins(self):
        version_id = extract_version_id(
            properties={"File": SHOTGRID_URL, "version_id": "1"},
            metadata={"version_id": 2},
        )
        self.assertEqual(version_id, "6990")

    def test_returns_none_when_nothing_matches(self):
        self.assertIsNone(
            extract_version_id(properties={"File": "/local/path/clip.mov"})
        )

    def test_case_insensitive_key_lookup(self):
        version_id = extract_version_id(metadata={"Version_ID": "77"})
        self.assertEqual(version_id, "77")

    def test_custom_paths(self):
        paths = [
            {
                "source": "property",
                "name": "sg_url",
                "match": r"versions/(\d+)",
                "as": "string",
            }
        ]
        version_id = extract_version_id(
            properties={"sg_url": "https://x/versions/123/media"},
            paths=paths,
        )
        self.assertEqual(version_id, "123")

    def test_int_coerce_rejects_non_numeric(self):
        paths = [{"source": "metadata", "name": "version_id", "as": "int"}]
        self.assertIsNone(
            extract_version_id(metadata={"version_id": "abc"}, paths=paths)
        )


class LoadVersionIdPathsTest(unittest.TestCase):
    def test_loads_packaged_default(self):
        paths = load_version_id_paths()
        self.assertGreaterEqual(len(paths), 2)
        self.assertEqual(paths[0]["name"], "File")

    def test_rejects_non_array(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"source": "property"}, handle)
            path = handle.name
        try:
            with self.assertRaises(InvalidVersionIdConfigError):
                load_version_id_paths(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
