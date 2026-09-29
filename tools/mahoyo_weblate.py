#!/usr/bin/env python3
"""Bridge Mahoyo JSONL and gettext, with Japanese as source and English as secondary."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_ID = re.compile(r"^text_id=(0|[1-9][0-9]*) \| speaker=([a-z][a-z0-9_]*)$")
PO_FIELD = re.compile(r'^(msgctxt|msgid|msgstr)(?:\[([0-9]+)\])?\s+(".*")$')


def read_jsonl(path: Path) -> list[tuple[dict, str]]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"{path}:{number}: blank JSONL line")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{number}: expected JSON object")
            rows.append((row, line))
    return rows


def index_rows(rows: list[tuple[dict, str]], path: Path) -> dict[int, dict]:
    indexed = {}
    for number, (row, _) in enumerate(rows, 1):
        text_id = row.get("text_id")
        if type(text_id) is not int or text_id < 0:
            raise ValueError(f"{path}:{number}: invalid text_id {text_id!r}")
        if text_id in indexed:
            raise ValueError(f"{path}:{number}: duplicate text_id {text_id}")
        indexed[text_id] = row
    return indexed


def load_corpus(strings_path: Path, translation_path: Path, occurrences_path: Path):
    sources = read_jsonl(strings_path)
    targets = read_jsonl(translation_path)
    strings = index_rows(sources, strings_path)
    translations = index_rows(targets, translation_path)
    if not strings:
        raise ValueError(f"{strings_path}: empty corpus")
    if set(strings) != set(translations):
        missing = sorted(set(strings) - set(translations))
        extra = sorted(set(translations) - set(strings))
        raise ValueError(f"{translation_path}: text_id mismatch; missing={missing[:10]}, unknown={extra[:10]}")
    occurrences = defaultdict(list)
    seen_occurrences = set()
    for number, (row, _) in enumerate(read_jsonl(occurrences_path), 1):
        text_id, occurrence_id = row.get("text_id"), row.get("id")
        if type(text_id) is not int or text_id not in strings:
            raise ValueError(f"{occurrences_path}:{number}: unknown text_id {text_id!r}")
        if not isinstance(occurrence_id, str) or not occurrence_id or occurrence_id in seen_occurrences:
            raise ValueError(f"{occurrences_path}:{number}: missing or duplicate occurrence id {occurrence_id!r}")
        seen_occurrences.add(occurrence_id)
        if (not isinstance(row.get("script"), str) or not row["script"]
                or type(row.get("command_index")) is not int or row["command_index"] < 0):
            raise ValueError(f"{occurrences_path}:{number}: invalid source location")
        occurrences[text_id].append(row)
    for number, (source, _) in enumerate(sources, 1):
        text_id = source["text_id"]
        if not isinstance(source.get("ja"), str) or not isinstance(source.get("en"), str):
            raise ValueError(f"{strings_path}:{number}: ja and en must be strings")
        target = translations[text_id]
        if (target.get("ja") != source["ja"] or target.get("en") != source["en"]
                or target.get("occurrences") != occurrences[text_id]):
            raise ValueError(f"{translation_path}: text_id {text_id}: JA/EN/occurrences disagree with source files")
        pl = target.get("pl")
        if "pl" not in target or (pl is not None and
                                  (not isinstance(pl, str) or not pl.strip())):
            raise ValueError(f"{translation_path}: text_id {text_id}: pl must be null or nonblank text")
    return sources, targets, strings, translations, occurrences


def po_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def unique(rows: list[dict], key: str) -> list[str]:
    return list(dict.fromkeys(str(row[key]) for row in rows if row.get(key) is not None))


def context(text_id: int, uses: list[dict]) -> str:
    speakers = unique(uses, "speaker")
    speaker = speakers[0] if len(speakers) == 1 else "unknown"
    return f"text_id={text_id} | speaker={speaker}"


def comment_value(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def render_catalog(sources, translations, occurrences, language: str | None) -> str:
    headers = ["MIME-Version: 1.0\n", "Content-Type: text/plain; charset=UTF-8\n",
               "Content-Transfer-Encoding: 8bit\n", "X-Source-Language: ja\n"]
    if language:
        headers.append(f"Language: {language}\n")
    lines = ['msgid ""', 'msgstr ""'] + [po_quote(h) for h in headers] + [""]
    for source, _ in sources:
        text_id = source["text_id"]
        uses = occurrences[text_id]
        for use in uses:
            fields = ("speaker", "speaker_ja", "speaker_source", "speaker_confidence",
                      "voice_id", "voice_prefix", "voice_category", "scene")
            details = "; ".join(f"{key}={comment_value(use[key])}"
                                for key in fields if use.get(key) is not None)
            lines.append(f"#. Occurrence {comment_value(use['id'])}" +
                         (f": {details}" if details else ""))
            lines.append(f"#: {use['script']}:{use['command_index']}")
        lines.append(f"msgctxt {po_quote(context(text_id, uses))}")
        lines.append(f"msgid {po_quote(source['ja'])}")
        value = "" if language is None else (source["en"] if language == "en"
                                           else translations[text_id]["pl"] or "")
        lines.append(f"msgstr {po_quote(value)}")
        lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=f".{path.name}.",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_po(path: Path) -> tuple[list[dict], str]:
    """Read singular PO entries, including Weblate comments and wrapped strings."""
    entries, entry = [], {}
    header = ""
    current = None
    fuzzy = obsolete = False
    start = 1

    def finish() -> None:
        nonlocal entry, current, fuzzy, obsolete, header
        if entry and not obsolete:
            if "msgctxt" in entry:
                if set(entry) != {"msgctxt", "msgid", "msgstr"}:
                    raise ValueError(f"{path}:{start}: incomplete PO entry")
                if fuzzy:
                    raise ValueError(f"{path}:{start}: fuzzy translation must be resolved before import")
                entries.append(entry)
            elif entry.get("msgid") != "" or "msgstr" not in entry:
                raise ValueError(f"{path}:{start}: missing msgctxt")
            else:
                header = entry["msgstr"]
        entry, current, fuzzy, obsolete = {}, None, False, False

    with path.open("r", encoding="utf-8-sig") as stream:
        for number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line:
                finish()
                start = number + 1
                continue
            if line.startswith("#~"):
                obsolete = True
                continue
            if line.startswith("#,"):
                fuzzy |= "fuzzy" in [flag.strip() for flag in line[2:].split(",")]
                continue
            if line.startswith("#") or obsolete:
                continue
            match = PO_FIELD.fullmatch(line)
            if match:
                field, index, quoted = match.groups()
                if index is not None:
                    raise ValueError(f"{path}:{number}: plural entries are unsupported")
                if field in entry:
                    raise ValueError(f"{path}:{number}: duplicate {field}")
                current = field
            elif line.startswith('"') and line.endswith('"') and current:
                quoted, field = line, current
            else:
                raise ValueError(f"{path}:{number}: unsupported PO syntax")
            try:
                fragment = ast.literal_eval(quoted)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"{path}:{number}: invalid PO string") from exc
            if not isinstance(fragment, str):
                raise ValueError(f"{path}:{number}: invalid PO string")
            entry[field] = entry.get(field, "") + fragment
    finish()
    return entries, header


def validate_po(path: Path, strings: dict[int, dict]) -> dict[int, str]:
    values = {}
    entries, header = parse_po(path)
    languages = re.findall(r"(?m)^Language:\s*([^\s]+)", header)
    if languages and (len(languages) != 1 or
                      not re.fullmatch(r"pl(?:[_-][A-Za-z0-9]+)*", languages[0])):
        raise ValueError(f"{path}: expected Polish PO, got Language: {languages!r}")
    for entry in entries:
        match = CONTEXT_ID.fullmatch(entry["msgctxt"])
        if not match:
            raise ValueError(f"{path}: invalid msgctxt {entry['msgctxt']!r}")
        text_id = int(match.group(1))
        if text_id not in strings:
            raise ValueError(f"{path}: unknown text_id {text_id}")
        if text_id in values:
            raise ValueError(f"{path}: duplicate text_id {text_id}")
        if entry["msgid"] != strings[text_id]["ja"]:
            raise ValueError(f"{path}: text_id {text_id}: Japanese source has changed")
        value = entry["msgstr"]
        if value and not value.strip():
            raise ValueError(f"{path}: text_id {text_id}: whitespace-only Polish translation")
        values[text_id] = value
    missing = sorted(set(strings) - set(values))
    if missing:
        raise ValueError(f"{path}: missing text_id values: {missing[:10]}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "export", "import"))
    parser.add_argument("--strings", type=Path, default=ROOT / "strings.jsonl")
    parser.add_argument("--translation", type=Path, default=ROOT / "translation.jsonl")
    parser.add_argument("--occurrences", type=Path, default=ROOT / "occurrences.jsonl")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "weblate")
    parser.add_argument("--po", type=Path, help="Polish PO to validate/import (default: output-dir/pl.po)")
    args = parser.parse_args()
    try:
        sources, target_rows, strings, translations, occurrences = load_corpus(
            args.strings, args.translation, args.occurrences)
        po_path = args.po or args.output_dir / "pl.po"
        if args.command == "validate":
            if args.po:
                validate_po(po_path, strings)
            print(f"validated: {len(strings)} strings, {sum(map(len, occurrences.values()))} occurrences"
                  + (f", {po_path}" if args.po else ""))
        elif args.command == "export":
            for filename, language in (("mahoyo.pot", None), ("en.po", "en"), ("pl.po", "pl")):
                path = args.output_dir / filename
                atomic_write(path, render_catalog(sources, translations, occurrences, language))
                print(f"written: {path}")
        else:
            values = validate_po(po_path, strings)
            changed, output = 0, []
            for row, original in target_rows:
                new_pl = values[row["text_id"]] or None
                if row["pl"] == new_pl:
                    output.append(original)
                else:
                    updated = dict(row)
                    updated["pl"] = new_pl
                    output.append(json.dumps(updated, ensure_ascii=False, separators=(",", ":")) + "\n")
                    changed += 1
            if changed:
                atomic_write(args.translation, "".join(output))
            print(f"imported: {len(values)} strings; changed: {changed}; file: {args.translation}")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
