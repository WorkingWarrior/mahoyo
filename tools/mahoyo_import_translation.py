"""Import Polish translations into the English slot of a source script_text.mrg."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from mahoyo_context import parse_text_table


RECORD_COUNT = 24136
FORMAT_VERSION = 3
LANGUAGES = ("JA", "EN", "ZH", "ZH2", "KO")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number}: expected a JSON object")
            yield number, value


def load_strings(path: Path) -> list[dict]:
    by_id = {}
    for number, row in read_jsonl(path):
        text_id = row.get("text_id")
        if (set(row) != {"text_id", "ja", "en"} or type(text_id) is not int
                or not 0 <= text_id < RECORD_COUNT
                or not isinstance(row["ja"], str)
                or not isinstance(row["en"], str)):
            raise ValueError(f"{path}:{number}: expected text_id, ja and en strings")
        if text_id in by_id:
            raise ValueError(f"{path}:{number}: duplicate text_id {text_id}")
        by_id[text_id] = row
    if len(by_id) != RECORD_COUNT:
        missing = next((i for i in range(RECORD_COUNT) if i not in by_id), None)
        raise ValueError(f"{path}: expected {RECORD_COUNT} records; got {len(by_id)}; "
                         f"first missing text_id: {missing}")
    return [by_id[i] for i in range(RECORD_COUNT)]


def load_translation(path: Path, strings: list[dict]) -> dict[int, str]:
    """Accept sparse PL rows and the repository's complete translator view."""
    translated = {}
    seen = set()
    full_format = None
    for number, row in read_jsonl(path):
        text_id = row.get("text_id")
        if type(text_id) is not int or not 0 <= text_id < len(strings):
            raise ValueError(f"{path}:{number}: unknown text_id {text_id!r}")
        if text_id in seen:
            raise ValueError(f"{path}:{number}: duplicate text_id {text_id}")
        seen.add(text_id)
        full = set(row) == {"text_id", "ja", "en", "pl", "occurrences"}
        if not full and set(row) != {"text_id", "pl"}:
            raise ValueError(f"{path}:{number}: invalid translation fields")
        if full_format is not None and full != full_format:
            raise ValueError(f"{path}:{number}: mixed translation formats")
        full_format = full
        if full:
            if (row["ja"] != strings[text_id]["ja"]
                    or row["en"] != strings[text_id]["en"]
                    or not isinstance(row["occurrences"], list)):
                raise ValueError(f"{path}:{number}: translator view disagrees with strings")
        pl = row["pl"]
        if full and pl is None:
            continue
        if not isinstance(pl, str) or not pl.strip():
            raise ValueError(f"{path}:{number}: empty or invalid PL")
        try:
            pl.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{path}:{number}: PL cannot be encoded as UTF-8") from exc
        translated[text_id] = pl
    if full_format and len(seen) != len(strings):
        raise ValueError(f"{path}: complete translator view has missing text_id")
    return translated


def parse_archive(data: bytes):
    # The existing parser and writer are supplied by the pinned mahoyo_tools submodule.
    from mahoyo_tools.mzp import MzpArchive

    archive = MzpArchive(data)
    if archive.nb_entries != 10:
        raise ValueError(f"script_text.mrg: expected 10 entries, got {archive.nb_entries}")
    for index, entry in enumerate(archive):
        if len(entry.data) != entry.size:
            raise ValueError(f"script_text.mrg: entry {index} is truncated")
    tables = []
    for index, language in enumerate(LANGUAGES):
        records = parse_text_table(archive[2 * index].data,
                                   archive[2 * index + 1].data)
        if len(records) != RECORD_COUNT:
            raise ValueError(f"{language}: expected {RECORD_COUNT} records, got {len(records)}")
        tables.append(records)
    return archive, tables


def en_segments(offsets: bytes, data: bytes) -> list[bytes]:
    positions = [int.from_bytes(offsets[i:i + 4], "big")
                 for i in range(0, len(offsets) - 4, 4)]
    ends = positions[1:] + [len(data)]
    return [data[start:end] for start, end in zip(positions, ends)]


def build_en(archive, translated: dict[int, str]) -> None:
    original = en_segments(archive[2].data, archive[3].data)
    if len(original) != RECORD_COUNT:
        raise ValueError("EN offset table has incorrect length")
    offsets = bytearray()
    contents = bytearray()
    for text_id, old in enumerate(original):
        if len(contents) >= 0xFFFFFFFF:
            raise ValueError("EN text data exceeds uint32 offset limit")
        offsets.extend(len(contents).to_bytes(4, "big"))
        contents.extend(translated[text_id].encode("utf-8") + b"\r\n"
                        if text_id in translated else old)
    offsets.extend(b"\xff" * 4)
    archive[2].data = bytes(offsets)
    archive[3].data = bytes(contents)


def validate_rebuilt(output_bytes: bytes, source_archive, source_tables,
                     translated: dict[int, str]) -> str:
    rebuilt, rebuilt_tables = parse_archive(output_bytes)
    if rebuilt.nb_entries != source_archive.nb_entries:
        raise ValueError("post-write: entry count changed")
    for index in range(source_archive.nb_entries):
        if index not in (2, 3) and rebuilt[index].data != source_archive[index].data:
            raise ValueError(f"post-write: entry {index} changed")
    for language_index, language in enumerate(LANGUAGES):
        expected = ([translated.get(i, value) for i, value in
                     enumerate(source_tables[language_index])]
                    if language == "EN" else source_tables[language_index])
        if rebuilt_tables[language_index] != expected:
            mismatch = next(i for i, (left, right) in enumerate(
                zip(rebuilt_tables[language_index], expected)) if left != right)
            raise ValueError(f"post-write: {language}[{mismatch}] differs")
    return sha256(output_bytes)


def validate_output(path: Path, source_archive, source_tables,
                    translated: dict[int, str]) -> str:
    return validate_rebuilt(path.read_bytes(), source_archive,
                            source_tables, translated)


def import_translation(source: Path, strings_path: Path, translation_path: Path,
                       manifest_path: Path, output: Path, *, dry_run: bool = False,
                       force_source_mismatch: bool = False) -> dict:
    if source.resolve() == output.resolve():
        raise ValueError("output must differ from source; in-place writes are forbidden")
    source_bytes = source.read_bytes()
    source_hash = sha256(source_bytes)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"manifest: expected format_version {FORMAT_VERSION}")
    hashes = manifest.get("source_sha256")
    expected_hash = hashes.get("script_text.mrg") if isinstance(hashes, dict) else None
    if (not isinstance(expected_hash, str) or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)):
        raise ValueError("manifest: missing script_text.mrg SHA-256")
    verified = source_hash == expected_hash
    if not verified and not force_source_mismatch:
        raise ValueError(f"source SHA-256 mismatch: got {source_hash}; "
                         f"manifest expects {expected_hash}")
    strings = load_strings(strings_path)
    translations = load_translation(translation_path, strings)
    archive, source_tables = parse_archive(source_bytes)
    # mzp_write relocates entries. Materialize every lazy source entry first,
    # otherwise an unchanged entry can be read from its new offset in the old file.
    for entry in archive:
        entry.data = entry.data
    if manifest.get("strings") != RECORD_COUNT:
        raise ValueError(f"manifest: expected strings count {RECORD_COUNT}")
    for text_id, row in enumerate(strings):
        for key, table in (("ja", source_tables[0]), ("en", source_tables[1])):
            if row[key] != table[text_id]:
                raise ValueError(f"strings/source mismatch: {key.upper()}[{text_id}]")
    build_en(archive, translations)
    result = {
        "source_sha256": source_hash,
        "source_verified": "yes" if verified else "forced",
        "strings_validated": RECORD_COUNT,
        "translations_loaded": len(translations),
        "en_replaced": len(translations),
        "en_preserved": RECORD_COUNT - len(translations),
        "output": str(output),
        "output_sha256": None,
        "post_write_validation": "pending",
    }
    if dry_run:
        output_bytes = archive.mzp_write().getvalue()
        result["output_sha256"] = validate_rebuilt(
            output_bytes, parse_archive(source_bytes)[0], source_tables, translations)
        result["post_write_validation"] = "passed (in memory; no output written)"
        return result
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent,
                                         prefix=f".{output.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
        archive.mzp_write(str(temp_path))
        result["output_sha256"] = validate_output(temp_path,
                                                  parse_archive(source_bytes)[0],
                                                  source_tables, translations)
        os.replace(temp_path, output)
        result["post_write_validation"] = "passed"
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--strings", type=Path, required=True)
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-source-mismatch", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = import_translation(args.source, args.strings, args.translation,
                                    args.manifest, args.output, dry_run=args.dry_run,
                                    force_source_mismatch=args.force_source_mismatch)
    except (OSError, ValueError, AssertionError) as exc:
        parser.exit(2, f"mahoyo_import_translation: {exc}\n")
    for key, value in result.items():
        print(f"{key.replace('_', ' ')}: {value if value is not None else 'n/a'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
