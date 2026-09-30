"""English-source migration tests; install requirements-weblate.txt to run."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

if importlib.util.find_spec("polib") is not None:
    import polib
    from mahoyo_weblate_en import export, import_polish, validate


@unittest.skipUnless(importlib.util.find_spec("polib"), "polib is optional for JA-source tooling")
class EnglishSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        self.output = self.root / "weblate-en"
        self.strings = self.root / "strings.jsonl"
        self.translation = self.root / "translation.jsonl"
        self.occurrences = self.root / "occurrences.jsonl"
        self.sources = [
            {"text_id": 0, "ja": "「こんにちは」\n", "en": '  "Hello"\\\n'},
            {"text_id": 1, "ja": "", "en": ""},
        ]
        self.targets = [
            {**self.sources[0], "pl": "Older local PL", "occurrences": [],
             "review_metadata": {"keep": True}},
            {**self.sources[1], "pl": None, "occurrences": []},
        ]
        self.write_jsonl(self.strings, self.sources)
        self.write_jsonl(self.translation, self.targets)
        self.occurrences.write_text("", encoding="utf-8")
        for language in ("ja", "en", "pl"):
            catalog = polib.POFile()
            catalog.metadata = {"Language": language, "X-Source-Language": "ja",
                                "Content-Type": "text/plain; charset=UTF-8"}
            for row in self.sources:
                number = row["text_id"]
                value = {"ja": "", "en": row["en"],
                         "pl": "Latest Weblate PL" if number == 0 else ""}[language]
                catalog.append(polib.POEntry(
                    msgctxt=f"text_id={number} | speaker=aoko",
                    msgid=row["ja"], msgstr=value,
                    comment=f"Occurrence scene:1:{number}",
                    occurrences=[("scene", str(number))],
                    flags=["fuzzy"] if language == "pl" and number == 0 else []))
            catalog.save(str(self.snapshot / f"{language}.po"))

    @staticmethod
    def write_jsonl(path, rows):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                for row in rows), encoding="utf-8")

    def test_export_preserves_weblate_pl_and_fuzzy(self):
        with self.assertRaisesRegex(ValueError, "empty English msgid"):
            export(self.snapshot, self.output, self.strings, self.translation,
                   self.occurrences, exclude_empty_unused=False)
        report = export(self.snapshot, self.output, self.strings, self.translation,
                        self.occurrences, exclude_empty_unused=True)
        self.assertEqual(report["excluded_unused_empty_ids"], [1])
        self.assertEqual(report["pl_fuzzy"], 1)
        self.assertEqual(validate(self.snapshot, self.output, self.strings,
                                  self.translation, self.occurrences,
                                  exclude_empty_unused=True),
                         {"mahoyo": 1, "ja": 1, "pl": 1})
        pl = polib.pofile(str(self.output / "pl.po"))[0]
        ja = polib.pofile(str(self.output / "ja.po"))[0]
        self.assertEqual(pl.msgid, self.sources[0]["en"])
        self.assertEqual(pl.msgstr, "Latest Weblate PL")
        self.assertEqual(pl.flags, ["fuzzy"])
        self.assertEqual(ja.msgstr, self.sources[0]["ja"])
        self.assertEqual(pl.comment, "Occurrence scene:1:0")
        self.assertEqual(pl.occurrences, [("scene", "0")])
        self.assertEqual(polib.pofile(str(self.output / "pl.po")).metadata["X-Source-Language"],
                         "en")
        with self.assertRaisesRegex(ValueError, "fuzzy PL"):
            import_polish(self.output / "pl.po", self.strings, self.translation,
                          self.occurrences, exclude_empty_unused=True)

        catalog = polib.pofile(str(self.output / "pl.po"))
        catalog[0].flags = []
        catalog.save(str(self.output / "pl.po"))
        changed = import_polish(self.output / "pl.po", self.strings, self.translation,
                                self.occurrences, exclude_empty_unused=True)
        self.assertEqual(changed, 1)
        updated = [json.loads(line) for line in self.translation.read_text().splitlines()]
        self.assertEqual(updated[0]["pl"], "Latest Weblate PL")
        self.assertEqual(updated[0]["review_metadata"], {"keep": True})
        self.assertEqual(updated[1], self.targets[1])

    def test_rejects_stale_english(self):
        catalog = polib.pofile(str(self.snapshot / "en.po"))
        catalog[0].msgstr = "Stale English"
        catalog.save(str(self.snapshot / "en.po"))
        with self.assertRaisesRegex(ValueError, "English differs"):
            export(self.snapshot, self.output, self.strings, self.translation,
                   self.occurrences, exclude_empty_unused=True)


if __name__ == "__main__":
    unittest.main()
