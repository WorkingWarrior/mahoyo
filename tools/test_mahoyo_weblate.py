"""Focused checks for the JSONL/gettext bridge."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mahoyo_weblate import (context, load_corpus, render_catalog, parse_po, validate_po,
                            atomic_write)


class WeblateBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.strings_path = self.root / "strings.jsonl"
        self.translation_path = self.root / "translation.jsonl"
        self.occurrences_path = self.root / "occurrences.jsonl"
        self.po_path = self.root / "pl.po"
        self.uses = [
            {"id": "s:1:0", "text_id": 0, "script": "s", "scene": "s",
             "command_index": 1, "speaker": "aoko", "speaker_ja": "青子",
             "speaker_source": "voice_category_map", "speaker_confidence": "probable",
             "voice_id": "A10_test", "voice_prefix": "A10", "voice_category": 1},
            {"id": "s:2:0", "text_id": 0, "script": "s", "scene": "s",
             "command_index": 2, "speaker": None, "speaker_ja": None,
             "voice_id": None, "voice_prefix": None},
        ]
        sources = [
            {"text_id": 0, "ja": "「同じ」\n\"x\"\\", "en": "English\n\"x\"\\"},
            {"text_id": 1, "ja": "「同じ」\n\"x\"\\", "en": "Other"},
            {"text_id": 2, "ja": "", "en": ""},
        ]
        targets = [
            {**sources[0], "pl": "Polski", "occurrences": self.uses,
             "review": {"keep": True}},
            {**sources[1], "pl": None, "occurrences": []},
            {**sources[2], "pl": None, "occurrences": []},
        ]
        self.write_rows(self.strings_path, sources)
        self.write_rows(self.translation_path, targets)
        self.write_rows(self.occurrences_path, self.uses)

    @staticmethod
    def write_rows(path, rows):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                for row in rows), encoding="utf-8")

    def export(self):
        sources, targets, strings, translations, uses = load_corpus(
            self.strings_path, self.translation_path, self.occurrences_path)
        atomic_write(self.po_path, render_catalog(sources, translations, uses, "pl"))
        return strings, targets

    def change_translation(self, text_id, value):
        lines = self.po_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.startswith(f'msgctxt "text_id={text_id} |'):
                self.assertTrue(lines[index + 2].startswith("msgstr "))
                lines[index + 2] = f"msgstr {json.dumps(value, ensure_ascii=False)}"
                atomic_write(self.po_path, "\n".join(lines) + "\n")
                return
        self.fail(f"missing text_id {text_id}")

    def run_import(self):
        script = Path(__file__).with_name("mahoyo_weblate.py")
        return subprocess.run(
            [sys.executable, str(script), "import", "--strings", str(self.strings_path),
             "--translation", str(self.translation_path), "--occurrences",
             str(self.occurrences_path), "--po", str(self.po_path)],
            capture_output=True, text=True, check=True)

    def test_round_trip_and_metadata(self):
        strings, targets = self.export()
        entries, header = parse_po(self.po_path)
        self.assertIn("Language: pl\n", header)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["msgid"], entries[1]["msgid"])
        self.assertEqual([entry["msgctxt"] for entry in entries], [
            "text_id=0 | speaker=aoko", "text_id=1 | speaker=unknown",
            "text_id=2 | speaker=unknown"])
        catalog = self.po_path.read_text()
        for detail in ("Occurrence s:1:0", "#: s:1", "#: s:2", "speaker_ja=青子",
                       "speaker_source=voice_category_map", "speaker_confidence=probable",
                       "voice_id=A10_test", "voice_prefix=A10", "voice_category=1",
                       "scene=s"):
            self.assertIn(detail, catalog)
        self.assertEqual(validate_po(self.po_path, strings), {0: "Polski", 1: "", 2: ""})
        original = self.translation_path.read_bytes()
        self.assertIn("changed: 0", self.run_import().stdout)
        self.assertEqual(self.translation_path.read_bytes(), original)
        self.change_translation(1, "Nowe tłumaczenie")
        self.change_translation(0, "")
        values = validate_po(self.po_path, strings)
        self.assertEqual(values[1], "Nowe tłumaczenie")
        self.assertIn("changed: 2", self.run_import().stdout)
        before = [row for row, _ in targets]
        after = [json.loads(line) for line in self.translation_path.read_text().splitlines()]
        self.assertIsNone(after[0]["pl"])
        self.assertEqual(after[1]["pl"], "Nowe tłumaczenie")
        self.assertEqual(after[1]["occurrences"], before[1]["occurrences"])
        self.assertEqual(after[0]["review"], {"keep": True})
        self.assertEqual([{k: v for k, v in row.items() if k != "pl"} for row in after],
                         [{k: v for k, v in row.items() if k != "pl"} for row in before])
        self.assertEqual(after[2], before[2])

    def test_ambiguous_speakers_use_unknown_context(self):
        self.assertEqual(context(7, [{"speaker": "aoko"}, {"speaker": "alice"}]),
                         "text_id=7 | speaker=unknown")

    def test_rejects_extra_context_metadata(self):
        strings, _ = self.export()
        original = self.po_path.read_text(encoding="utf-8")
        atomic_write(self.po_path, original.replace(
            'msgctxt "text_id=0 | speaker=aoko"',
            'msgctxt "text_id=0 | speaker=aoko | voice=A10"'))
        with self.assertRaisesRegex(ValueError, "invalid msgctxt"):
            validate_po(self.po_path, strings)

    def test_rejects_changed_source_and_missing_id(self):
        strings, _ = self.export()
        original = self.po_path.read_text(encoding="utf-8")
        atomic_write(self.po_path, original.replace(
            'msgctxt "text_id=1 | speaker=unknown"\nmsgid ' +
            json.dumps(strings[1]["ja"], ensure_ascii=False),
            'msgctxt "text_id=1 | speaker=unknown"\nmsgid "wrong"'))
        with self.assertRaisesRegex(ValueError, "source has changed"):
            validate_po(self.po_path, strings)
        atomic_write(self.po_path, original[:original.index('msgctxt "text_id=2 | speaker=unknown"')])
        with self.assertRaisesRegex(ValueError, "missing text_id"):
            validate_po(self.po_path, strings)

    def test_rejects_english_catalog(self):
        strings, _ = self.export()
        atomic_write(self.po_path, self.po_path.read_text().replace("Language: pl",
                                                                    "Language: en"))
        with self.assertRaisesRegex(ValueError, "expected Polish PO"):
            validate_po(self.po_path, strings)

    def test_wrapped_po_and_fuzzy_rejection(self):
        strings, _ = self.export()
        original = self.po_path.read_text(encoding="utf-8")
        atomic_write(self.po_path, original.replace('msgstr "Polski"',
                                                    'msgstr ""\n"Pol"\n"ski"'))
        self.assertEqual(validate_po(self.po_path, strings)[0], "Polski")
        atomic_write(self.po_path, original.replace('msgctxt "text_id=1 | speaker=unknown"',
                                                    '#, fuzzy\nmsgctxt "text_id=1 | speaker=unknown"'))
        with self.assertRaisesRegex(ValueError, "fuzzy"):
            validate_po(self.po_path, strings)


if __name__ == "__main__":
    unittest.main()
