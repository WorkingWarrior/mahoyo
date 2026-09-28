"""Corpus export tests for occurrence order, PL indexing and real archives."""

import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from collections import Counter

from mahoyo_context import DEFAULT_SCRIPTS, DEFAULT_TEXT, Script, load_scripts, load_texts, split_commands
from mahoyo_export_corpus import (
    DEFAULT_PL_DIR,
    export_project,
    iter_corpus_records,
    iter_string_records,
    load_pl_translations,
)


def sample_script(source: bytes) -> Script:
    commands = split_commands("sample", source)
    return Script("sample", commands,
                  [command.command_index for command in commands
                   if command.command.startswith("_ZM")])


class ExportUnitTests(unittest.TestCase):
    def test_shared_zm_and_source_order_context(self):
        texts = [{"text_id": i, "ja": f"JA {i}", "en": f"EN {i}"} for i in range(5)]
        script = sample_script(
            b"_ZM($000000);_VPLY(A10_1_2_0003,1);"
            b"_ZM($000001^$000002);_ZM($000003);_ZM($000004);")
        rows = list(iter_corpus_records(texts, [script], {}, context=1))
        self.assertEqual([row["id"] for row in rows],
                         ["sample:0:0", "sample:2:1", "sample:2:2",
                          "sample:3:3", "sample:4:4"])
        first, second = rows[1:3]
        for field in ("script", "command_index", "command_offset", "zm_command",
                      "voice_id", "voice_category", "speaker"):
            self.assertEqual(first[field], second[field], field)
        self.assertEqual(first["voice_id"], "A10_1_2_0003")
        self.assertEqual(first["speaker"], "aoko")
        self.assertEqual(first["context_before"], [0])
        self.assertEqual(first["context_after"], [3])
        self.assertEqual(rows[3]["context_before"], [1, 2])
        self.assertIsNone(rows[3]["voice_id"])
        self.assertIsNone(rows[3]["speaker"])

    def test_unused_and_no_pl(self):
        texts = [{"text_id": i, "ja": f"JA {i}", "en": f"EN {i}"} for i in range(2)]
        rows = list(iter_corpus_records(texts,
                    [sample_script(b"_ZM($000000);")], {}, include_unused=True))
        self.assertEqual([row["id"] for row in rows], ["sample:0:0", "unused:1"])
        self.assertIsNone(rows[1]["script"])
        self.assertIsNone(rows[1]["voice_category"])
        self.assertNotIn("pl", rows[1])

    def test_string_is_unique_and_text_stays_out_of_occurrences(self):
        texts = [{"text_id": i, "ja": f"JA {i}", "en": f"EN {i}"} for i in range(3)]
        strings = list(iter_string_records(texts, {0: "PL 0"}))
        self.assertEqual([row["text_id"] for row in strings], [0, 1, 2])
        self.assertEqual(len(strings), len({row["text_id"] for row in strings}))
        self.assertEqual(strings[0]["pl"], "PL 0")
        self.assertIsNone(strings[1]["pl"])
        self.assertIsNone(strings[0]["status"])
        occurrences = list(iter_corpus_records(
            texts, [sample_script(b"_ZM($000000);_ZM($000000);")], {0: "PL 0"}))
        self.assertEqual(len(occurrences), 2)
        self.assertTrue(all(row["text_id"] in {s["text_id"] for s in strings}
                            for row in occurrences))
        self.assertTrue(all(not {"ja", "en", "pl"} & row.keys()
                            for row in occurrences))
        legacy = list(iter_corpus_records(
            texts, [sample_script(b"_ZM($000000);")], {0: "PL 0"},
            legacy_inline_text=True))
        self.assertEqual(legacy[0]["pl"], "PL 0")

    def test_project_manifest_and_determinism(self):
        texts = [{"text_id": i, "ja": f"JA {i}", "en": f"EN {i}"} for i in range(3)]
        scripts = [sample_script(b"_ZM($000000);_ZM($000001);_ZM($000000);")]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            text_source = root / "script_text.mrg"
            script_source = root / "allscr.mrg"
            text_source.write_bytes(b"text source")
            script_source.write_bytes(b"script source")
            outputs = [root / "first", root / "second"]
            manifests = [export_project(path, texts, scripts, {0: "PL 0"},
                                        text_source, script_source) for path in outputs]
            self.assertEqual(manifests[0], manifests[1])
            manifest = manifests[0]
            self.assertEqual((manifest["strings"], manifest["occurrences"]), (3, 3))
            self.assertEqual(manifest["occurrence_distribution"],
                             {"zero": 1, "one": 1, "more_than_one": 1,
                              "max_occurrences": 2})
            self.assertEqual(manifest["pl"], {
                "nonempty_pl_text_ids_total": 1,
                "nonempty_pl_text_ids_used": 1,
                "nonempty_pl_text_ids_unused": 0,
                "occurrences_referencing_translated_text_ids": 2,
            })
            self.assertEqual(manifest["source_sha256"]["script_text.mrg"],
                             hashlib.sha256(b"text source").hexdigest())
            self.assertNotIn(str(root), (outputs[0] / "manifest.json").read_text())
            for name in ("strings.jsonl", "occurrences.jsonl", "manifest.json"):
                self.assertEqual((outputs[0] / name).read_bytes(),
                                 (outputs[1] / name).read_bytes())
            string_rows = [json.loads(line) for line in
                           (outputs[0] / "strings.jsonl").read_text().splitlines()]
            occurrence_rows = [json.loads(line) for line in
                               (outputs[0] / "occurrences.jsonl").read_text().splitlines()]
            self.assertEqual(len(string_rows), manifest["strings"])
            self.assertEqual(len(occurrence_rows), manifest["occurrences"])
            self.assertEqual({r["text_id"] for r in occurrence_rows}, {0, 1})

    def test_pl_loader_uses_validated_contiguous_indices(self):
        texts = [{"text_id": i, "ja": f"JA {i}", "en": f"EN {i}"} for i in range(3)]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            # The lexical order is deliberately different from table order.
            (root / "ChapterB.json").write_text(json.dumps([
                {"ja": "JA 0", "en": "edited EN 0", "pl": "PL 0"},
                {"ja": "JA 1", "en": "edited EN 1", "pl": ""},
            ]), encoding="utf-8")
            (root / "ChapterA.json").write_text(json.dumps([
                {"ja": "JA 2", "en": "edited EN 2", "pl": "PL 2"},
            ]), encoding="utf-8")
            self.assertEqual(load_pl_translations(root, texts), {0: "PL 0", 2: "PL 2"})

    def test_pl_conflict_is_rejected(self):
        texts = [{"text_id": 0, "ja": "JA 0", "en": "EN 0"}]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, translation in (("ChapterA.json", "first"),
                                      ("ChapterB.json", "second")):
                (root / name).write_text(json.dumps([
                    {"ja": "JA 0", "en": "EN 0", "pl": translation},
                ]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Conflicting PL for text_id 0"):
                load_pl_translations(root, texts)

    def test_pl_gap_and_missing_fields_are_rejected(self):
        texts = [{"text_id": i, "ja": f"JA {i}", "en": f"EN {i}"} for i in range(3)]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "ChapterA.json"
            file.write_text(json.dumps([{"ja": "JA 2", "en": "EN 2", "pl": "PL 2"}]),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing chapter range"):
                load_pl_translations(root, texts)
            file.write_text(json.dumps([{"ja": "JA 0", "pl": "PL 0"}]),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected string ja, en, pl"):
                load_pl_translations(root, texts)


@unittest.skipUnless(DEFAULT_TEXT.is_file() and DEFAULT_SCRIPTS.is_file(),
                     "Supplied Mahoyo archives are unavailable")
class RealArchiveExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = load_texts(DEFAULT_TEXT)
        cls.scripts = load_scripts(DEFAULT_SCRIPTS)
        cls.rows = list(iter_corpus_records(cls.texts, cls.scripts, {}))
        cls.by_text_id = {}
        for row in cls.rows:
            cls.by_text_id.setdefault(row["text_id"], []).append(row)

    def test_full_export_ids_unique_and_source_order(self):
        ids = [row["id"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)))
        order = {script.name: i for i, script in enumerate(self.scripts)}
        keys = [(order[row["script"]], row["command_index"])
                for row in self.rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(self.rows), 23550)
        self.assertTrue(all(not {"ja", "en", "pl"} & row.keys()
                            for row in self.rows))

    def test_real_strings_and_occurrence_relation(self):
        strings = list(iter_string_records(self.texts, {}))
        string_ids = {row["text_id"] for row in strings}
        self.assertEqual(len(strings), len(string_ids))
        self.assertEqual(len(strings), 24136)
        self.assertTrue(all(row["text_id"] in string_ids for row in self.rows))
        counts = Counter(row["text_id"] for row in self.rows)
        self.assertEqual(len(string_ids - counts.keys()), 595)
        self.assertEqual(sum(n == 1 for n in counts.values()), 23532)
        self.assertEqual(sum(n > 1 for n in counts.values()), 9)
        self.assertEqual(max(counts.values()), 2)
        self.assertEqual(counts[14], 0)
        self.assertEqual(counts[3376], 2)

    def test_requested_real_occurrences(self):
        r148 = self.by_text_id[148][0]
        self.assertEqual((r148["script"], r148["voice_id"],
                          r148["voice_category"], r148["speaker"]),
                         ("1_2", "A10_1_2_0000", 1, "aoko"))
        r178, r179 = self.by_text_id[178][0], self.by_text_id[179][0]
        self.assertEqual((r178["script"], r178["command_index"],
                          r178["command_offset"], r178["zm_command"],
                          r178["voice_id"]),
                         (r179["script"], r179["command_index"],
                          r179["command_offset"], r179["zm_command"],
                          r179["voice_id"]))
        self.assertEqual(r178["voice_id"], "A10_1_2_0003")
        r1118 = self.by_text_id[1118][0]
        self.assertEqual((r1118["voice_id"], r1118["voice_category"],
                          r1118["speaker"]),
                         ("A30_1DOT5_1_0000", 3, "soujuurou"))
        for text_id in (15646, 6597):
            with self.subTest(text_id=text_id):
                self.assertIsNone(self.by_text_id[text_id][0]["voice_id"])
                self.assertIsNone(self.by_text_id[text_id][0]["speaker"])

    @unittest.skipUnless(DEFAULT_PL_DIR.is_dir(), "Working PL chapters are unavailable")
    def test_real_pl_range_and_empty_translation(self):
        pl = load_pl_translations(DEFAULT_PL_DIR, self.texts)
        self.assertEqual(pl[0], "  Działo się to zimowego dnia, przed ośmioma laty.")
        self.assertNotIn(2118, pl)
        self.assertTrue(all(0 <= text_id <= 10434 for text_id in pl))
        self.assertEqual(len(pl), 2110)
        counts = Counter(row["text_id"] for row in self.rows)
        self.assertEqual(sum(text_id in counts for text_id in pl), 2085)
        self.assertEqual(sum(text_id not in counts for text_id in pl), 25)
        self.assertEqual(sum(counts[text_id] for text_id in pl), 2085)


if __name__ == "__main__":
    unittest.main()
