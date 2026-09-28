"""Focused parser tests plus checks against the supplied Switch archives."""

import unittest

from mahoyo_context import (
    DEFAULT_SCRIPTS,
    DEFAULT_TEXT,
    Script,
    extract_zm_references,
    format_result,
    get_probable_speaker,
    get_voice_category,
    get_voice_prefix,
    load_scripts,
    load_texts,
    occurrence,
    parse_text_table,
    search_query,
    split_commands,
    text_query,
    voice_metadata,
)


class ParserTests(unittest.TestCase):
    def test_voice_category_and_probable_speaker_mapping(self):
        cases = {
            "A10_1_2_0000": ("A10", 1, "aoko", "青子"),
            "A40_example": ("A40", 2, "alice", "有珠"),
            "A30_example": ("A30", 3, "soujuurou", "草十郎"),
            "B90_example": ("B90", 14, "yurihiko", "由里彦"),
            "BA0_example": ("BA0", 0, None, None),
            "XYZ_example": ("XYZ", 0, None, None),
        }
        for asset_id, (prefix, category, speaker, speaker_ja) in cases.items():
            with self.subTest(asset_id=asset_id):
                self.assertEqual(get_voice_prefix(asset_id), prefix)
                self.assertEqual(get_voice_category(prefix), category)
                metadata = voice_metadata(asset_id)
                self.assertEqual(metadata["voice_category"], category)
                self.assertEqual(metadata["speaker"], speaker)
                self.assertEqual(metadata["speaker_ja"], speaker_ja)
                self.assertEqual(get_probable_speaker(category),
                                 (speaker, speaker_ja) if speaker else None)
                self.assertEqual(metadata["speaker_source"],
                                 "voice_category_map" if speaker else None)
                self.assertEqual(metadata["speaker_confidence"],
                                 "probable" if speaker else None)

    def test_no_candidate_does_not_inherit_previous_voice(self):
        commands = split_commands("example", b"_VPLY(A10_1_2_0000,1);_ZM($000001);_ZM($000002);")
        script = Script("example", commands, [1, 2])
        texts = [{"text_id": 0}, {"text_id": 1}, {"text_id": 2}]
        first = occurrence(script, commands[1], texts)
        second = occurrence(script, commands[2], texts)
        self.assertEqual(first["voice_id"], "A10_1_2_0000")
        for field in ("voice_id", "voice_prefix", "voice_category", "speaker",
                      "speaker_ja", "speaker_source", "speaker_confidence"):
            self.assertIsNone(second[field], field)
        self.assertNotIn("candidate_voice_id", second)
        self.assertIn("voice_id: null", format_result({
            "mode": "text_id", "text_id": 2, "text": None,
            "occurrences": [second], "messages": [],
        }))

    def test_multiple_candidates_remain_ambiguous(self):
        commands = split_commands("example", b"_VPLY(A10_1,1);_VPLY(A40_2,2);_ZM($000001);")
        script = Script("example", commands, [2])
        hit = occurrence(script, commands[2], [{"text_id": 0}, {"text_id": 1}])
        self.assertEqual([c["voice_id"] for c in hit["candidate_voice_commands"]],
                         ["A10_1", "A40_2"])
        self.assertNotIn("candidate_voice_id", hit)
        for field in ("voice_id", "voice_prefix", "voice_category", "speaker",
                      "speaker_ja", "speaker_source", "speaker_confidence"):
            self.assertIsNone(hit[field], field)

    def test_multiple_decimal_references_in_one_zm(self):
        self.assertEqual(extract_zm_references("_ZM02923($000178^$000179)"),
                         (178, 179))
        self.assertEqual(extract_zm_references("_ZM($000001,$000001,$999999)"),
                         (1, 1, 999999))

    def test_ignores_non_zm_and_wrong_reference_width(self):
        self.assertEqual(extract_zm_references("_VPLY($000178)"), ())
        self.assertEqual(extract_zm_references("_ZM($00017,$0001789,$abcdef,$000179)"),
                         (179,))

    def test_text_offsets_and_record_separator(self):
        offsets = b"\x00\x00\x00\x00\x00\x00\x00\x06\xff\xff\xff\xff"
        self.assertEqual(parse_text_table(offsets, b"A\r\nB\r\nC\r\n"),
                         ["A\r\nB", "C"])

    def test_command_byte_offsets_and_source_text(self):
        commands = split_commands("example", "_ZM($000178^$000179);_VPLY(a,1);".encode())
        self.assertEqual([(c.command_index, c.byte_offset, c.command, c.text_ids)
                          for c in commands],
                         [(0, 0, "_ZM($000178^$000179)", (178, 179)),
                          (1, 21, "_VPLY(a,1)", ())])


@unittest.skipUnless(DEFAULT_TEXT.is_file() and DEFAULT_SCRIPTS.is_file(),
                     "Supplied Mahoyo archives are unavailable")
class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = load_texts(DEFAULT_TEXT)
        cls.scripts = load_scripts(DEFAULT_SCRIPTS)

    def test_requested_ids(self):
        expected_scripts = {148: "1_2", 1118: "1DOT5_1", 15762: "B_4"}
        for text_id in (0, 148, 178, 179, 1118, 15762):
            with self.subTest(text_id=text_id):
                result = text_query(text_id, self.texts, self.scripts)
                self.assertEqual(result["messages"], [])
                self.assertTrue(result["occurrences"])
                self.assertEqual(result["text"]["text_id"], text_id)
                if text_id in expected_scripts:
                    self.assertTrue(any(hit["script"] == expected_scripts[text_id]
                                        for hit in result["occurrences"]))

    def test_shared_zm_and_voice_candidate(self):
        hits178 = text_query(178, self.texts, self.scripts)["occurrences"]
        hits179 = text_query(179, self.texts, self.scripts)["occurrences"]
        shared = {(hit["script"], hit["command_index"], hit["command"])
                  for hit in hits178} & {(hit["script"], hit["command_index"], hit["command"])
                                      for hit in hits179}
        self.assertTrue(any(command == "_ZM02923($000178^$000179)"
                            for _, _, command in shared))
        shared178 = next(hit for hit in hits178
                         if hit["command"] == "_ZM02923($000178^$000179)")
        shared179 = next(hit for hit in hits179
                         if hit["script"] == shared178["script"]
                         and hit["command_index"] == shared178["command_index"])
        for hit in (shared178, shared179):
            self.assertEqual(hit["text_ids"], [178, 179])
            self.assertEqual(hit["candidate_voice_id"], "A10_1_2_0003")
            self.assertEqual(hit["voice_id"], "A10_1_2_0003")
            self.assertEqual(hit["voice_prefix"], "A10")
            self.assertEqual(hit["voice_category"], 1)
            self.assertEqual((hit["speaker"], hit["speaker_ja"]), ("aoko", "青子"))
        hits148 = text_query(148, self.texts, self.scripts)["occurrences"]
        self.assertTrue(any(hit.get("candidate_voice_id") == "A10_1_2_0000"
                            for hit in hits148))

    def test_search_modes_and_missing_id(self):
        voice = search_query("voice", "A10_1_2_0000", self.texts, self.scripts)
        self.assertTrue(any(hit["script"] == "1_2" for hit in voice["occurrences"]))
        grep = search_query("grep_command", r"_ZM02923", self.texts, self.scripts)
        self.assertTrue(any("$000178^$000179" in hit["command"]
                            for hit in grep["occurrences"]))
        missing = text_query(len(self.texts) + 1, self.texts, self.scripts)
        self.assertIsNone(missing["text"])
        self.assertEqual(len(missing["messages"]), 2)
        filtered = text_query(148, self.texts, load_scripts(DEFAULT_SCRIPTS, "1_0"))
        self.assertEqual(filtered["occurrences"], [])
        self.assertIn("not used", filtered["messages"][0])


if __name__ == "__main__":
    unittest.main()
