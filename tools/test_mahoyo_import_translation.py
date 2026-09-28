"""Validation and round-trip tests for the translation importer."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mahoyo_context import open_archive, parse_text_table
from mahoyo_import_translation import (
    RECORD_COUNT, import_translation, parse_archive, validate_output,
)
from mahoyo_tools.mzp import MzpArchive


def table(records):
    offsets = bytearray()
    data = bytearray()
    for record in records:
        offsets.extend(len(data).to_bytes(4, "big"))
        data.extend(record.encode("utf-8") + b"\r\n")
    offsets.extend(b"\xff" * 4)
    return bytes(offsets), bytes(data)


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.source = cls.root / "source.mrg"
        cls.strings = cls.root / "strings.jsonl"
        cls.manifest = cls.root / "manifest.json"
        cls.ja = [f"日本語 {i}" for i in range(RECORD_COUNT)]
        cls.en = [f"English {i}" for i in range(RECORD_COUNT)]
        archive = MzpArchive()
        for records in (cls.ja, cls.en,
                        ["中文"] * RECORD_COUNT,
                        ["繁體"] * RECORD_COUNT,
                        ["한국어"] * RECORD_COUNT):
            for entry in table(records):
                archive.add_entry(entry)
        archive.mzp_write(str(cls.source))
        cls.source_hash = hashlib.sha256(cls.source.read_bytes()).hexdigest()
        cls.strings.write_text("".join(json.dumps({"text_id": i, "ja": cls.ja[i],
                                                    "en": cls.en[i]}, ensure_ascii=False)
                                       + "\n" for i in range(RECORD_COUNT)),
                               encoding="utf-8")
        cls.manifest.write_text(json.dumps({"format_version": 3,
                                            "strings": RECORD_COUNT,
                                            "source_sha256": {
                                                "script_text.mrg": cls.source_hash}}),
                                encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.translation = self.root / "translation.jsonl"
        self.output = self.root / "build" / "script_text.mrg"
        self.output.unlink(missing_ok=True)
        self.translation.write_text("", encoding="utf-8")

    def write_translation(self, rows):
        self.translation.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                            for row in rows), encoding="utf-8")

    def run_import(self, **kwargs):
        return import_translation(self.source, self.strings, self.translation,
                                  self.manifest, self.output, **kwargs)

    def assert_logical_output(self, patches):
        source, before = parse_archive(self.source.read_bytes())
        after, tables = parse_archive(self.output.read_bytes())
        self.assertEqual(after.nb_entries, source.nb_entries)
        for index in (0, 2, 3, 4):
            self.assertEqual(tables[index], before[index])
        self.assertEqual(tables[1], [patches.get(i, value)
                                     for i, value in enumerate(before[1])])

    def test_dry_run_has_no_output(self):
        result = self.run_import(dry_run=True)
        self.assertEqual(result["strings_validated"], RECORD_COUNT)
        self.assertFalse(self.output.exists())

    def test_empty_translation_is_byte_identical(self):
        result = self.run_import()
        self.assert_logical_output({})
        self.assertEqual(result["output_sha256"], self.source_hash)
        self.assertEqual(self.output.read_bytes(), self.source.read_bytes())

    def test_single_patch_changes_only_en_148(self):
        self.write_translation([{"text_id": 148, "pl": "TEST PL"}])
        self.run_import()
        self.assert_logical_output({148: "TEST PL"})

    def test_multiple_utf8_patch_lengths(self):
        patches = {2: "x", 148: "Dłuższy polski tekst. Żółć i 🎭",
                   23000: "ąęłńóśźż 漢字"}
        self.write_translation([{"text_id": i, "pl": text}
                                for i, text in patches.items()])
        self.run_import()
        self.assert_logical_output(patches)

    def test_duplicate_id_fails(self):
        self.write_translation([{"text_id": 1, "pl": "A"},
                                {"text_id": 1, "pl": "B"}])
        with self.assertRaisesRegex(ValueError, "duplicate text_id"):
            self.run_import()
        self.assertFalse(self.output.exists())

    def test_unknown_id_fails(self):
        self.write_translation([{"text_id": RECORD_COUNT, "pl": "A"}])
        with self.assertRaisesRegex(ValueError, "unknown text_id"):
            self.run_import()

    def test_whitespace_only_fails(self):
        self.write_translation([{"text_id": 1, "pl": " \t \n"}])
        with self.assertRaisesRegex(ValueError, "empty or invalid PL"):
            self.run_import()

    def test_source_hash_mismatch_and_force(self):
        wrong = self.root / "wrong_manifest.json"
        wrong.write_text(json.dumps({"format_version": 3, "strings": RECORD_COUNT,
                                     "source_sha256": {"script_text.mrg": "0" * 64}}))
        with self.assertRaisesRegex(ValueError, "source SHA-256 mismatch"):
            import_translation(self.source, self.strings, self.translation,
                               wrong, self.output)
        result = import_translation(self.source, self.strings, self.translation,
                                    wrong, self.output, dry_run=True,
                                    force_source_mismatch=True)
        self.assertEqual(result["source_verified"], "forced")

    def test_strings_source_mismatch(self):
        wrong = self.root / "wrong_strings.jsonl"
        wrong.write_text(self.strings.read_text(encoding="utf-8").replace(
            '"en": "English 148"', '"en": "WRONG"', 1), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "strings/source mismatch"):
            import_translation(self.source, wrong, self.translation,
                               self.manifest, self.output)

    def test_source_as_output_rejected(self):
        with self.assertRaisesRegex(ValueError, "in-place"):
            import_translation(self.source, self.strings, self.translation,
                               self.manifest, self.source)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         self.source_hash)

    def test_post_write_parser_catches_corruption(self):
        self.run_import()
        rebuilt = open_archive(self.output)
        rebuilt[0].data = b"broken"
        rebuilt.mzp_write(str(self.output))
        source, tables = parse_archive(self.source.read_bytes())
        with self.assertRaises(ValueError):
            validate_output(self.output, source, tables, {})

    def test_failed_post_write_validation_keeps_existing_output(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_bytes(b"previous output")
        with patch("mahoyo_import_translation.validate_output",
                   side_effect=ValueError("post-write failure")):
            with self.assertRaisesRegex(ValueError, "post-write failure"):
                self.run_import()
        self.assertEqual(self.output.read_bytes(), b"previous output")
        self.assertFalse(list(self.output.parent.glob(".script_text.mrg.*.tmp")))

    def test_complete_translator_view_with_nulls(self):
        self.write_translation({"text_id": i, "ja": self.ja[i],
                                "en": self.en[i], "pl": "PL" if i == 148 else None,
                                "occurrences": []} for i in range(RECORD_COUNT))
        result = self.run_import()
        self.assertEqual(result["translations_loaded"], 1)
        self.assert_logical_output({148: "PL"})


if __name__ == "__main__":
    unittest.main()
