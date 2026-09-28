"""Export canonical Mahoyo strings and source-order script occurrences."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Iterator

from mahoyo_context import (
    DEFAULT_SCRIPTS,
    DEFAULT_TEXT,
    Script,
    load_scripts,
    load_texts,
    occurrence,
    voice_metadata,
)


DEFAULT_PL_DIR = Path("/mnt/Dane/Development/mahoyo/H/text")
DEFAULT_OUTPUT = Path("/tmp/mahoyo_corpus.jsonl")
DEFAULT_STRINGS_OUTPUT = Path("/tmp/mahoyo_strings.jsonl")
DEFAULT_PROJECT_DIR = Path("/tmp/mahoyo_project")


def load_pl_translations(pl_dir: Path, texts: list[dict[str, object]]) -> dict[int, str]:
    """Load PL by explicit IDs, or match complete JA spans in legacy files.

    Legacy filenames do not encode reliable offsets. Their complete JA sequence
    must match the indexed game table before any PL is assigned.
    """
    files = sorted(path for path in pl_dir.glob("Chapter*.json")
                   if not path.name.endswith("_AUTOTRANSLATED.json"))
    if not files:
        raise ValueError(f"No Chapter*.json files found in {pl_dir}")
    ja_locations: dict[str, list[int]] = {}
    for text in texts:
        ja_locations.setdefault(text["ja"], []).append(text["text_id"])

    translated: dict[int, str] = {}
    covered: dict[int, str] = {}
    for path in files:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{path.name}: expected a nonempty list of records")
        indexed = ["text_id" in row for row in rows if isinstance(row, dict)]
        if len(indexed) != len(rows) or (any(indexed) and not all(indexed)):
            raise ValueError(f"{path.name}: mixed or invalid chapter records")
        if all(indexed):
            for row_index, row in enumerate(rows):
                text_id = row["text_id"]
                if (type(text_id) is not int or not 0 <= text_id < len(texts)
                        or not isinstance(row.get("ja"), str)
                        or not isinstance(row.get("en"), str)
                        or row.get("pl") is not None and not isinstance(row["pl"], str)):
                    raise ValueError(f"{path.name}[{row_index}]: invalid indexed record")
                if text_id in covered:
                    raise ValueError(f"Overlapping chapter ranges at text_id {text_id}: "
                                     f"{covered[text_id]} and {path.name}")
                covered[text_id] = path.name
                if row["pl"] and row["pl"].strip():
                    translated[text_id] = row["pl"]
            continue
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                                                for key in ("ja", "en", "pl")):
                raise ValueError(f"{path.name}[{row_index}]: expected string ja, en, pl fields")

        anchor = min(range(len(rows)),
                     key=lambda i: len(ja_locations.get(rows[i]["ja"], ())))
        starts = []
        for match_id in ja_locations.get(rows[anchor]["ja"], ()):
            start = match_id - anchor
            if (start >= 0 and start + len(rows) <= len(texts)
                    and all(row["ja"] == texts[start + i]["ja"]
                            for i, row in enumerate(rows))):
                starts.append(start)
        if len(starts) != 1:
            raise ValueError(f"{path.name}: JA sequence has {len(starts)} possible ranges; "
                             "cannot assign text_id safely")

        start = starts[0]
        for row_index, row in enumerate(rows):
            text_id = start + row_index
            if text_id in covered:
                previous = translated.get(text_id)
                current = row["pl"] if row["pl"].strip() else None
                if previous and current and previous != current:
                    raise ValueError(f"Conflicting PL for text_id {text_id}: "
                                     f"{covered[text_id]} and {path.name}")
                raise ValueError(f"Overlapping chapter ranges at text_id {text_id}: "
                                 f"{covered[text_id]} and {path.name}")
            covered[text_id] = path.name
            if row["pl"].strip():
                translated[text_id] = row["pl"]

    missing = sorted(set(range(max(covered) + 1)) - covered.keys())
    if missing:
        raise ValueError(f"Missing chapter range starting at text_id {missing[0]}; "
                         f"{len(missing)} IDs missing below {max(covered)}")
    return translated


def load_translation_jsonl(path: Path, texts: list[dict[str, object]]) -> dict[int, str]:
    """Load PL from a complete translation table or the earlier sparse format."""
    translated: dict[int, str] = {}
    seen: set[int] = set()
    full_format: bool | None = None
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            row = json.loads(line)
            if not isinstance(row, dict) or not {"text_id", "pl"} <= set(row):
                raise ValueError(f"{path.name}:{line_number}: expected text_id and pl")
            is_full = {"ja", "en", "occurrences"} <= set(row)
            if set(row) != ({"text_id", "ja", "en", "pl", "occurrences"}
                            if is_full else {"text_id", "pl"}):
                raise ValueError(f"{path.name}:{line_number}: invalid record fields")
            if full_format is not None and is_full != full_format:
                raise ValueError(f"{path.name}:{line_number}: mixed record formats")
            full_format = is_full
            text_id, pl = row["text_id"], row["pl"]
            if type(text_id) is not int or not 0 <= text_id < len(texts):
                raise ValueError(f"{path.name}:{line_number}: invalid text_id")
            if text_id in seen:
                raise ValueError(f"{path.name}:{line_number}: duplicate text_id {text_id}")
            seen.add(text_id)
            if is_full:
                if (not isinstance(row["ja"], str) or not isinstance(row["en"], str)
                        or not isinstance(row["occurrences"], list)):
                    raise ValueError(f"{path.name}:{line_number}: invalid full record")
                if pl is not None and (not isinstance(pl, str) or not pl.strip()):
                    raise ValueError(f"{path.name}:{line_number}: invalid PL")
            elif not isinstance(pl, str) or not pl.strip():
                raise ValueError(f"{path.name}:{line_number}: empty or invalid PL")
            if pl is not None:
                translated[text_id] = pl
    if full_format and seen != set(range(len(texts))):
        raise ValueError(f"{path.name}: full translation table has missing text_id")
    return translated


def iter_string_records(texts: list[dict[str, object]]) -> Iterator[dict[str, object]]:
    """Yield one JA/EN record for every table index."""
    for text_id, text in enumerate(texts):
        if text["text_id"] != text_id:
            raise ValueError(f"script_text index {text_id} has text_id {text['text_id']}")
        yield {"text_id": text_id, "ja": text["ja"], "en": text["en"]}


def iter_translation_records(texts: list[dict[str, object]],
                             pl_by_id: dict[int, str],
                             occurrences_by_id: dict[int, list[dict[str, object]]]
                             ) -> Iterator[dict[str, object]]:
    """Yield one translator-ready record per string, including every use."""
    for string in iter_string_records(texts):
        text_id = string["text_id"]
        pl = pl_by_id.get(text_id)
        if pl is not None and (not isinstance(pl, str) or not pl.strip()):
            raise ValueError(f"Invalid translation for text_id {text_id}")
        yield {**string, "pl": pl,
               "occurrences": occurrences_by_id.get(text_id, [])}


def iter_corpus_records(texts: list[dict[str, object]], scripts: list[Script],
                        pl_by_id: dict[int, str], context: int = 3,
                        include_unused: bool = False,
                        legacy_inline_text: bool = False) -> Iterator[dict[str, object]]:
    """Yield one record per text reference, in archive and command source order."""
    if context < 0:
        raise ValueError("context must be nonnegative")
    used_text_ids: set[int] = set()
    occurrence_ids: set[str] = set()
    for script in scripts:
        zms = [script.commands[i] for i in script.zm_indices]
        for position, command in enumerate(zms):
            if not command.text_ids:
                continue
            hit = occurrence(script, command, texts)
            before = [text_id for neighbor in zms[max(0, position - context):position]
                      for text_id in neighbor.text_ids]
            after = [text_id for neighbor in zms[position + 1:position + 1 + context]
                     for text_id in neighbor.text_ids]
            for text_id in command.text_ids:
                if text_id >= len(texts):
                    raise ValueError(f"{script.name} command #{command.command_index}: "
                                     f"text_id {text_id} absent from script_text")
                record_id = f"{script.name}:{command.command_index}:{text_id}"
                if record_id in occurrence_ids:
                    raise ValueError(f"Duplicate occurrence id {record_id}")
                occurrence_ids.add(record_id)
                used_text_ids.add(text_id)
                record = {
                    "id": record_id,
                    "text_id": text_id,
                    "scene": script.name,
                    "script": script.name,
                    "command_index": command.command_index,
                    "command_offset": command.byte_offset,
                    "zm_command": command.command,
                    **{key: hit[key] for key in voice_metadata(None)},
                    "speaker_id": None,
                    "voice_source": (f"preceding {hit['candidate_voice_commands'][0]['command']}; "
                                     "association unverified")
                    if hit.get("candidate_voice_id") is not None else None,
                    "context_before": before,
                    "context_after": after,
                    "context_order": "script source",
                }
                if legacy_inline_text:
                    record.update(ja=texts[text_id]["ja"], en=texts[text_id]["en"],
                                  pl=pl_by_id.get(text_id))
                yield record
    if include_unused:
        for text_id, text in enumerate(texts):
            if text_id in used_text_ids:
                continue
            record = {
                "id": f"unused:{text_id}",
                "text_id": text_id,
                "script": None,
                "command_index": None,
                "command_offset": None,
                "zm_command": None,
                **voice_metadata(None),
                "context_before": [],
                "context_after": [],
            }
            if legacy_inline_text:
                record.update(ja=text["ja"], en=text["en"], pl=pl_by_id.get(text_id))
            yield record


def export_corpus(output: Path, texts: list[dict[str, object]], scripts: list[Script],
                  pl_by_id: dict[int, str], context: int = 3,
                  include_unused: bool = False,
                  legacy_inline_text: bool = False) -> dict[str, object]:
    """Write atomically and return compact corpus statistics."""
    used_ids = {text_id for script in scripts for command in script.commands
                for text_id in command.text_ids}
    counts: Counter[str] = Counter()
    prefixes: set[str] = set()
    speaker_counts: Counter[str] = Counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=output.parent, prefix=f".{output.name}.",
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            for record in iter_corpus_records(texts, scripts, pl_by_id, context,
                                              include_unused, legacy_inline_text):
                stream.write(json.dumps(record, ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
                counts["occurrences"] += 1
                counts["with_voice" if record["voice_id"] is not None else "without_voice"] += 1
                counts["with_speaker" if record["speaker"] is not None else "without_speaker"] += 1
                counts["with_pl" if record["text_id"] in pl_by_id else "without_pl"] += 1
                if record["voice_prefix"] is not None:
                    prefixes.add(record["voice_prefix"])
                if record["speaker"] is not None:
                    speaker_counts[record["speaker"]] += 1
        os.replace(temp_path, output)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return {
        "scripts": len(scripts),
        "ZM commands": sum(len(script.zm_indices) for script in scripts),
        "occurrences": counts["occurrences"],
        "unique text_ids": len(used_ids),
        "unused text_ids": len(texts) - len(used_ids),
        "with voice": counts["with_voice"],
        "without voice": counts["without_voice"],
        "with probable speaker": counts["with_speaker"],
        "without speaker": counts["without_speaker"],
        "with PL": counts["with_pl"],
        "without PL": counts["without_pl"],
        "voice prefixes": len(prefixes),
        "speaker occurrences": dict(sorted(speaker_counts.items())),
        "output": str(output),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_strings(output: Path, texts: list[dict[str, object]]) -> set[int]:
    """Write the complete indexed JA/EN table; return its unique IDs."""
    output.parent.mkdir(parents=True, exist_ok=True)
    string_ids: set[int] = set()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=output.parent, prefix=f".{output.name}.",
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            for record in iter_string_records(texts):
                text_id = record["text_id"]
                if text_id in string_ids:
                    raise ValueError(f"Duplicate string text_id {text_id}")
                string_ids.add(text_id)
                stream.write(json.dumps(record, ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
        os.replace(temp_path, output)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return string_ids


def write_translation(output: Path, texts: list[dict[str, object]],
                      pl_by_id: dict[int, str], string_ids: set[int],
                      occurrences_path: Path) -> None:
    """Write the complete translator-ready table, atomically."""
    if not pl_by_id.keys() <= string_ids:
        raise ValueError("PL contains text_id absent from script_text")
    occurrences_by_id: dict[int, list[dict[str, object]]] = {}
    with occurrences_path.open(encoding="utf-8") as stream:
        for line in stream:
            occurrence_record = json.loads(line)
            occurrences_by_id.setdefault(occurrence_record["text_id"], []).append(
                occurrence_record)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=output.parent, prefix=f".{output.name}.",
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            for record in iter_translation_records(texts, pl_by_id, occurrences_by_id):
                stream.write(json.dumps(record, ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
        os.replace(temp_path, output)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def audit_occurrences(output: Path, string_ids: set[int],
                      pl_by_id: dict[int, str]) -> dict[str, int]:
    """Validate project references and count 0/1/N uses per canonical string."""
    use_counts: Counter[int] = Counter()
    occurrence_ids: set[str] = set()
    translated_occurrences = 0
    with output.open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            text_id = record["text_id"]
            if text_id not in string_ids:
                raise ValueError(f"Occurrence {record['id']} references absent text_id {text_id}")
            if record["id"] in occurrence_ids:
                raise ValueError(f"Duplicate occurrence id {record['id']}")
            occurrence_ids.add(record["id"])
            use_counts[text_id] += 1
            translated_occurrences += text_id in pl_by_id
    translated_used = len(pl_by_id.keys() & use_counts.keys())
    return {
        "zero": len(string_ids) - len(use_counts),
        "one": sum(count == 1 for count in use_counts.values()),
        "more_than_one": sum(count > 1 for count in use_counts.values()),
        "max_occurrences": max(use_counts.values(), default=0),
        "nonempty_pl_text_ids_total": len(pl_by_id),
        "nonempty_pl_text_ids_used": translated_used,
        "nonempty_pl_text_ids_unused": len(pl_by_id) - translated_used,
        "occurrences_referencing_translated_text_ids": translated_occurrences,
        "occurrences": len(occurrence_ids),
    }


def export_project(output_dir: Path, texts: list[dict[str, object]],
                   scripts: list[Script], pl_by_id: dict[int, str],
                   text_path: Path, scripts_path: Path,
                   context: int = 3) -> dict[str, object]:
    """Write canonical strings, context occurrences and a path-free manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    string_ids = write_strings(output_dir / "strings.jsonl", texts)
    occurrence_stats = export_corpus(output_dir / "occurrences.jsonl", texts,
                                     scripts, pl_by_id, context)
    audit = audit_occurrences(output_dir / "occurrences.jsonl", string_ids, pl_by_id)
    if audit["occurrences"] != occurrence_stats["occurrences"]:
        raise ValueError("Occurrence count changed between export and audit")
    if audit["zero"] + audit["one"] + audit["more_than_one"] != len(string_ids):
        raise ValueError("Occurrence distribution does not cover all strings")
    write_translation(output_dir / "translation.jsonl", texts, pl_by_id,
                      string_ids, output_dir / "occurrences.jsonl")
    manifest = {
        "format_version": 3,
        "strings": len(string_ids),
        "occurrences": audit["occurrences"],
        "translation_records": len(string_ids),
        "translated_strings": len(pl_by_id),
        "unused_text_ids": audit["zero"],
        "source_order_context": True,
        "context_zm_commands_per_side": context,
        "scripts": occurrence_stats["scripts"],
        "zm_commands": occurrence_stats["ZM commands"],
        "occurrence_distribution": {
            key: audit[key] for key in ("zero", "one", "more_than_one", "max_occurrences")
        },
        "pl": {
            key: audit[key] for key in (
                "nonempty_pl_text_ids_total", "nonempty_pl_text_ids_used",
                "nonempty_pl_text_ids_unused", "occurrences_referencing_translated_text_ids")
        },
        "voice": {
            "with_voice": occurrence_stats["with voice"],
            "with_probable_speaker": occurrence_stats["with probable speaker"],
            "unique_prefixes": occurrence_stats["voice prefixes"],
        },
        "source_sha256": {
            "script_text.mrg": sha256_file(text_path),
            "allscr.mrg": sha256_file(scripts_path),
        },
        "output_sha256": {
            name: sha256_file(output_dir / name) for name in
            ("strings.jsonl", "occurrences.jsonl", "translation.jsonl")
        },
    }
    manifest_path = output_dir / "manifest.json"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=output_dir, prefix=".manifest.json.",
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                    indent=2) + "\n")
        os.replace(temp_path, manifest_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return manifest


def corpus_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--scripts", type=Path, default=DEFAULT_SCRIPTS)
    parser.add_argument("--pl-dir", type=Path, default=DEFAULT_PL_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--context", type=int, default=3, metavar="N")
    parser.add_argument("--no-pl", action="store_true")
    parser.add_argument("--include-unused", action="store_true")
    parser.add_argument("--legacy-inline-text", action="store_true",
                        help="also copy ja/en/pl into occurrence records")
    args = parser.parse_args(argv)
    if args.context < 0:
        parser.error("--context must be nonnegative")
    try:
        texts = load_texts(args.text)
        scripts = load_scripts(args.scripts)
        pl_by_id = {} if args.no_pl else load_pl_translations(args.pl_dir, texts)
        stats = export_corpus(args.output, texts, scripts, pl_by_id,
                              args.context, args.include_unused,
                              args.legacy_inline_text)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"mahoyo_export_corpus: {exc}\n")
    for key, value in stats.items():
        if key != "speaker occurrences":
            print(f"{key}: {value}")
    print("speaker occurrences: " + ", ".join(
        f"{speaker}={count}" for speaker, count in stats["speaker occurrences"].items()))
    return 0


def project_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Export strings, occurrences, translation and manifest")
    parser.add_argument("--text", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--scripts", type=Path, default=DEFAULT_SCRIPTS)
    translation_source = parser.add_mutually_exclusive_group()
    translation_source.add_argument("--pl-dir", type=Path)
    translation_source.add_argument("--translation", type=Path,
                                    help="existing translation.jsonl (complete or earlier sparse format)")
    translation_source.add_argument("--no-pl", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--context", type=int, default=3, metavar="N")
    args = parser.parse_args(argv)
    if args.context < 0:
        parser.error("--context must be nonnegative")
    try:
        texts = load_texts(args.text)
        scripts = load_scripts(args.scripts)
        if args.no_pl:
            pl_by_id = {}
        elif args.translation:
            pl_by_id = load_translation_jsonl(args.translation, texts)
        else:
            pl_by_id = load_pl_translations(args.pl_dir or DEFAULT_PL_DIR, texts)
        manifest = export_project(args.output_dir, texts, scripts, pl_by_id,
                                  args.text, args.scripts, args.context)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"mahoyo_export_corpus project: {exc}\n")
    print(f"strings: {manifest['strings']}")
    print(f"occurrences: {manifest['occurrences']}")
    print(f"occurrence distribution: {manifest['occurrence_distribution']}")
    print(f"PL: {manifest['pl']}")
    print(f"output: {args.output_dir}")
    return 0


def strings_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Export canonical JA/EN strings")
    parser.add_argument("--text", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_STRINGS_OUTPUT)
    args = parser.parse_args(argv)
    try:
        texts = load_texts(args.text)
        string_ids = write_strings(args.output, texts)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"mahoyo_export_corpus strings: {exc}\n")
    print(f"strings: {len(string_ids)}")
    print(f"output: {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "project":
        return project_main(args[1:])
    if args and args[0] == "strings":
        return strings_main(args[1:])
    return corpus_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
