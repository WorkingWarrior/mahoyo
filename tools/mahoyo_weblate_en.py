#!/usr/bin/env python3
"""Build and verify an English-source Weblate component from a JA-source snapshot.

The snapshot must be downloaded from Weblate after editing is locked. The
existing JSONL corpus is used to verify identities, never as the source of PL.
Requires polib (see tools/requirements-weblate.txt).
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import polib

from mahoyo_weblate import atomic_write, load_corpus

ROOT = Path(__file__).resolve().parents[1]
TEXT_ID = re.compile(r"^text_id=(0|[1-9][0-9]*)(?: \| .*)?$")


def text_id(entry: polib.POEntry, path: Path) -> int:
    match = TEXT_ID.fullmatch(entry.msgctxt or "")
    if not match:
        raise ValueError(f"{path}: invalid msgctxt {entry.msgctxt!r}")
    return int(match.group(1))


def indexed_po(path: Path) -> tuple[polib.POFile, dict[int, polib.POEntry]]:
    catalog = polib.pofile(str(path), check_for_duplicates=False)
    rows = {}
    for entry in catalog:
        number = text_id(entry, path)
        if number in rows:
            raise ValueError(f"{path}: duplicate text_id {number}")
        if entry.msgid_plural or entry.msgstr_plural:
            raise ValueError(f"{path}: plural entry at text_id {number}")
        rows[number] = entry
    return catalog, rows


def source_rows(strings_path: Path, translation_path: Path, occurrences_path: Path):
    sources, targets, strings, translations, uses = load_corpus(
        strings_path, translation_path, occurrences_path)
    return sources, targets, strings, translations, uses


def inspect_snapshot(snapshot: Path, strings: dict[int, dict],
                     translations: dict[int, dict], uses: dict[int, list[dict]],
                     *, exclude_empty_unused: bool):
    catalogs = {}
    indexed = {}
    for language in ("ja", "en", "pl"):
        path = snapshot / f"{language}.po"
        catalogs[language], indexed[language] = indexed_po(path)
        if set(indexed[language]) != set(strings):
            missing = sorted(set(strings) - set(indexed[language]))
            extra = sorted(set(indexed[language]) - set(strings))
            raise ValueError(f"{path}: text_id mismatch: missing={missing[:10]}, unknown={extra[:10]}")

    empty_en = []
    format_flags = []
    for number, source in strings.items():
        ja, en, pl = (indexed[language][number] for language in ("ja", "en", "pl"))
        if not (ja.msgctxt == en.msgctxt == pl.msgctxt):
            raise ValueError(f"text_id {number}: msgctxt differs across JA/EN/PL")
        if any(entry.msgid != source["ja"] for entry in (ja, en, pl)):
            raise ValueError(f"text_id {number}: Japanese msgid differs from strings.jsonl")
        if en.msgstr != source["en"]:
            raise ValueError(f"text_id {number}: English differs from strings.jsonl")
        if "fuzzy" in en.flags:
            raise ValueError(f"text_id {number}: English is marked fuzzy")
        for flag in pl.flags:
            if flag.endswith("-format") or flag.startswith("no-") and flag.endswith("-format"):
                format_flags.append((number, flag))
        if not en.msgstr:
            empty_en.append(number)
            if (source["ja"] or pl.msgstr or uses[number] or
                    translations[number]["pl"] is not None):
                raise ValueError(f"text_id {number}: English source is missing for a used or translated string")
    if empty_en and not exclude_empty_unused:
        raise ValueError(f"empty English msgid is reserved; unused empty text_id values: {empty_en}. "
                         "Pass --exclude-empty-unused only after approving their exclusion.")
    if format_flags:
        raise ValueError(f"format flags require review against English source: {format_flags[:20]}")
    return catalogs, indexed, empty_en


def clone_header(source: polib.POFile, language: str | None) -> polib.POFile:
    catalog = polib.POFile()
    catalog.encoding = "utf-8"
    catalog.wrapwidth = 0
    catalog.metadata = dict(source.metadata)
    catalog.metadata["X-Source-Language"] = "en"
    catalog.metadata["Content-Type"] = "text/plain; charset=UTF-8"
    if language:
        catalog.metadata["Language"] = language
    else:
        catalog.metadata.pop("Language", None)
        catalog.metadata.pop("Plural-Forms", None)
    catalog.header = source.header
    return catalog


def build_catalogs(old_catalogs: dict[str, polib.POFile],
                   indexed: dict[str, dict[int, polib.POEntry]],
                   ordered_ids: list[int], excluded: list[int]) -> dict[str, polib.POFile]:
    excluded_set = set(excluded)
    result = {
        "pl": clone_header(old_catalogs["pl"], "pl"),
        "ja": clone_header(old_catalogs["ja"], "ja"),
        "mahoyo": clone_header(old_catalogs["en"], None),
    }
    for number in ordered_ids:
        if number in excluded_set:
            continue
        english = indexed["en"][number].msgstr
        japanese = indexed["ja"][number].msgid
        pl = deepcopy(indexed["pl"][number])
        pl.msgid = english
        ja = deepcopy(indexed["ja"][number])
        ja.msgid, ja.msgstr = english, japanese
        pot = deepcopy(indexed["en"][number])
        pot.msgid, pot.msgstr = english, ""
        pot.flags = [flag for flag in pot.flags if flag != "fuzzy"]
        result["pl"].append(pl)
        result["ja"].append(ja)
        result["mahoyo"].append(pot)
    return result


def verify_catalogs(catalogs: dict[str, polib.POFile],
                    indexed: dict[str, dict[int, polib.POEntry]],
                    expected: set[int]) -> dict[str, int]:
    counts = {}
    for language, catalog in catalogs.items():
        rows = {}
        for entry in catalog:
            number = text_id(entry, Path(language))
            if number in rows:
                raise ValueError(f"{language}: duplicate text_id {number}")
            rows[number] = entry
            old = indexed["pl" if language == "pl" else "ja" if language == "ja" else "en"][number]
            if entry.msgid != indexed["en"][number].msgstr:
                raise ValueError(f"{language}: wrong English msgid at text_id {number}")
            if entry.msgctxt != old.msgctxt or entry.occurrences != old.occurrences:
                raise ValueError(f"{language}: lost context or locations at text_id {number}")
            if entry.comment != old.comment or entry.tcomment != old.tcomment:
                raise ValueError(f"{language}: lost comments at text_id {number}")
            expected_value = (indexed["pl"][number].msgstr if language == "pl" else
                              indexed["ja"][number].msgid if language == "ja" else "")
            if entry.msgstr != expected_value:
                raise ValueError(f"{language}: wrong translation at text_id {number}")
            if language == "pl" and entry.flags != old.flags:
                raise ValueError(f"pl: lost flags at text_id {number}")
        if set(rows) != expected:
            raise ValueError(f"{language}: incorrect text_id set")
        if catalog.metadata.get("X-Source-Language") != "en":
            raise ValueError(f"{language}: incorrect source-language header")
        if language in ("pl", "ja") and catalog.metadata.get("Language") != language:
            raise ValueError(f"{language}: incorrect Language header")
        counts[language] = len(rows)
    return counts


def export(snapshot: Path, output_dir: Path, strings_path: Path,
           translation_path: Path, occurrences_path: Path,
           *, exclude_empty_unused: bool) -> dict:
    sources, _, strings, translations, uses = source_rows(
        strings_path, translation_path, occurrences_path)
    old, indexed, excluded = inspect_snapshot(snapshot, strings, translations, uses,
                                              exclude_empty_unused=exclude_empty_unused)
    ordered_ids = [row["text_id"] for row, _ in sources]
    catalogs = build_catalogs(old, indexed, ordered_ids, excluded)
    expected = set(strings) - set(excluded)
    verify_catalogs(catalogs, indexed, expected)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mahoyo-en-", dir=output_dir) as staging:
        staging = Path(staging)
        paths = {"mahoyo": "mahoyo.pot", "ja": "ja.po", "pl": "pl.po"}
        for language, filename in paths.items():
            catalogs[language].save(str(staging / filename))
        parsed = {language: polib.pofile(str(staging / filename))
                  for language, filename in paths.items()}
        verify_catalogs(parsed, indexed, expected)
        for filename in paths.values():
            os.chmod(staging / filename, 0o644)
            os.replace(staging / filename, output_dir / filename)
    result = {
        "source": "Weblate JA/EN/PL snapshot",
        "source_language": "en",
        "string_count": len(strings),
        "po_entry_count": len(expected),
        "excluded_unused_empty_ids": excluded,
        "snapshot_sha256": {
            language: hashlib.sha256((snapshot / f"{language}.po").read_bytes()).hexdigest()
            for language in ("ja", "en", "pl")
        },
        "pl_translated": sum(bool(indexed["pl"][i].msgstr) for i in expected),
        "pl_fuzzy": sum("fuzzy" in indexed["pl"][i].flags for i in expected),
    }
    atomic_write(output_dir / "migration-manifest.json",
                 json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def validate(snapshot: Path, output_dir: Path, strings_path: Path,
             translation_path: Path, occurrences_path: Path,
             *, exclude_empty_unused: bool) -> dict:
    _, _, strings, translations, uses = source_rows(
        strings_path, translation_path, occurrences_path)
    _, indexed, excluded = inspect_snapshot(snapshot, strings, translations, uses,
                                            exclude_empty_unused=exclude_empty_unused)
    paths = {"mahoyo": "mahoyo.pot", "ja": "ja.po", "pl": "pl.po"}
    catalogs = {language: polib.pofile(str(output_dir / filename))
                for language, filename in paths.items()}
    return verify_catalogs(catalogs, indexed, set(strings) - set(excluded))


def import_polish(po_path: Path, strings_path: Path, translation_path: Path,
                  occurrences_path: Path, *, exclude_empty_unused: bool) -> int:
    _, targets, strings, translations, uses = source_rows(
        strings_path, translation_path, occurrences_path)
    catalog, rows = indexed_po(po_path)
    if catalog.metadata.get("Language") != "pl":
        raise ValueError(f"{po_path}: expected Language: pl")
    if catalog.metadata.get("X-Source-Language") != "en":
        raise ValueError(f"{po_path}: expected X-Source-Language: en")
    excluded = {number for number, source in strings.items() if not source["en"]}
    if excluded and not exclude_empty_unused:
        raise ValueError(f"empty English text_id values {sorted(excluded)} require --exclude-empty-unused")
    for number in excluded:
        if (strings[number]["ja"] or uses[number] or rows.get(number)
                or translations[number]["pl"] is not None):
            raise ValueError(f"text_id {number}: cannot exclude a used or represented string")
    if set(rows) != set(strings) - excluded:
        raise ValueError(f"{po_path}: text_id set differs from English-source corpus")
    for number, entry in rows.items():
        if entry.msgid != strings[number]["en"]:
            raise ValueError(f"{po_path}: changed English msgid at text_id {number}")
        if "fuzzy" in entry.flags:
            raise ValueError(f"{po_path}: fuzzy PL at text_id {number} cannot be imported to JSONL")
        if entry.msgstr and not entry.msgstr.strip():
            raise ValueError(f"{po_path}: whitespace-only PL at text_id {number}")
    changed, output = 0, []
    for row, original in targets:
        number = row["text_id"]
        value = row["pl"] if number in excluded else rows[number].msgstr or None
        if row["pl"] == value:
            output.append(original)
        else:
            updated = dict(row, pl=value)
            output.append(json.dumps(updated, ensure_ascii=False, separators=(",", ":")) + "\n")
            changed += 1
    if changed:
        atomic_write(translation_path, "".join(output))
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "validate", "import"))
    parser.add_argument("--snapshot-dir", type=Path,
                        help="Directory with fresh JA-source ja.po, en.po and pl.po from Weblate")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "weblate-en")
    parser.add_argument("--po", type=Path, help="English-source Polish catalog for import")
    parser.add_argument("--strings", type=Path, default=ROOT / "strings.jsonl")
    parser.add_argument("--translation", type=Path, default=ROOT / "translation.jsonl")
    parser.add_argument("--occurrences", type=Path, default=ROOT / "occurrences.jsonl")
    parser.add_argument("--exclude-empty-unused", action="store_true",
                        help="Exclude only IDs empty in JA/EN/PL and unused in scripts")
    args = parser.parse_args()
    try:
        if args.command in ("export", "validate") and args.snapshot_dir is None:
            parser.error("--snapshot-dir is required for export and validate")
        if args.command == "export":
            result = export(args.snapshot_dir, args.output_dir, args.strings,
                            args.translation, args.occurrences,
                            exclude_empty_unused=args.exclude_empty_unused)
            print(f"written: {args.output_dir}; entries={result['po_entry_count']}; "
                  f"excluded={result['excluded_unused_empty_ids']}; "
                  f"PL={result['pl_translated']}; fuzzy={result['pl_fuzzy']}")
        elif args.command == "validate":
            counts = validate(args.snapshot_dir, args.output_dir, args.strings,
                              args.translation, args.occurrences,
                              exclude_empty_unused=args.exclude_empty_unused)
            print(f"validated: {counts}")
        else:
            changed = import_polish(args.po or args.output_dir / "pl.po",
                                    args.strings, args.translation, args.occurrences,
                                    exclude_empty_unused=args.exclude_empty_unused)
            print(f"imported: changed={changed}; file={args.translation}")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
