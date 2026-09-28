"""Read-only, source-order diagnostics for Mahoyo Switch script archives."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import re
import sys

# mzp.py and mzx.py use package-relative imports. Support the requested direct CLI.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mahoyo_tools.mzp import MzpArchive
from mahoyo_tools.mzx import mzx_decompress


DEFAULT_TEXT = Path("/mnt/Dane/Development/mahoyo/NEW/script_text.mrg")
DEFAULT_SCRIPTS = Path("/mnt/Dane/Development/mahoyo/NEW/allscr.mrg")
REFERENCE_RE = re.compile(r"\$([0-9]{6})(?![0-9])")
ZM_RE = re.compile(r"^\s*_ZM")
VOICE_RE = re.compile(r"^\s*_VPLY\(([^,)]*)")
VOICE_CATEGORIES = {
    "ZZZ": 0, "BA0": 0, "C70": 0,
    "A10": 1, "A11": 1, "A40": 2, "A30": 3,
    "B70": 4, "B10": 5, "B11": 5, "B50": 6,
    "B60": 7, "B40": 8, "B41": 8, "B30": 9,
    "B20": 10, "A20": 11, "A21": 11, "A50": 12,
    "B80": 13, "B81": 13, "B90": 14,
}
# Inferred from the order of the game's voice settings, not a nameplate lookup.
PROBABLE_SPEAKERS = {
    1: ("aoko", "青子"), 2: ("alice", "有珠"),
    3: ("soujuurou", "草十郎"), 4: ("tobimaru", "鳶丸"),
    5: ("kojika", "金鹿"), 6: ("kinomi", "木乃美"),
    7: ("yamashiro", "山城教諭"), 8: ("eiri", "詠梨神父"),
    9: ("ritsuka", "律架"), 10: ("yuika", "唯架"),
    11: ("touko", "橙子"), 12: ("beo", "ベオ"),
    13: ("liddell", "リデル"), 14: ("yurihiko", "由里彦"),
}


@dataclass(frozen=True)
class Command:
    script: str
    command_index: int  # Zero-based, within the decompressed script.
    byte_offset: int
    command: str  # Delimiting semicolon is excluded; all other content is kept.
    text_ids: tuple[int, ...] = ()


@dataclass
class Script:
    name: str
    commands: list[Command]
    zm_indices: list[int]


def open_archive(path: Path) -> MzpArchive:
    """Pass bytes to MzpArchive so its path constructor cannot open rb+."""
    return MzpArchive(path.read_bytes())


def parse_text_table(offset_bytes: bytes, text_bytes: bytes) -> list[str]:
    if len(offset_bytes) % 4:
        raise ValueError("Text offset table length is not divisible by four")
    offsets = [int.from_bytes(offset_bytes[i:i + 4], "big")
               for i in range(0, len(offset_bytes), 4)]
    if not offsets or offsets[-1] != 0xFFFFFFFF:
        raise ValueError("Text offset table has no 0xFFFFFFFF terminator")
    offsets.pop()
    if not offsets or offsets[0] != 0 or offsets[-1] > len(text_bytes):
        raise ValueError("Text offset table is outside its text entry")
    if any(left > right for left, right in zip(offsets, offsets[1:])):
        raise ValueError("Text offsets are not in order")
    ends = offsets[1:] + [len(text_bytes)]
    records = []
    for start, end in zip(offsets, ends):
        record = text_bytes[start:end]
        if record.endswith(b"\r\n"):
            record = record[:-2]
        records.append(record.decode("utf-8"))
    return records


def load_texts(path: Path) -> list[dict[str, object]]:
    archive = open_archive(path)
    if archive.nb_entries < 4:
        raise ValueError("script_text archive has fewer than four entries")
    ja = parse_text_table(archive[0].data, archive[1].data)
    en = parse_text_table(archive[2].data, archive[3].data)
    if len(ja) != len(en):
        raise ValueError("Japanese and English tables have different lengths")
    return [{"text_id": i, "ja": jp, "en": eng}
            for i, (jp, eng) in enumerate(zip(ja, en))]


def extract_zm_references(command: str) -> tuple[int, ...]:
    """Return every six-digit decimal reference in a _ZM command, in order."""
    if not ZM_RE.match(command):
        return ()
    return tuple(int(match.group(1)) for match in REFERENCE_RE.finditer(command))


def split_commands(script: str, data: bytes) -> list[Command]:
    commands: list[Command] = []
    start = 0
    while start < len(data):
        end = data.find(b";", start)
        if end < 0:
            end = len(data)
        content = data[start:end].decode("utf-8")
        commands.append(Command(script, len(commands), start, content,
                                extract_zm_references(content)))
        start = end + 1
    return commands


def load_scripts(path: Path, script_filter: str | None = None) -> list[Script]:
    archive = open_archive(path)
    if archive.nb_entries != 252:
        raise ValueError(f"Expected 252 allscr entries, found {archive.nb_entries}")
    names_data = archive[0].data
    if len(names_data) % 32:
        raise ValueError("Script name table is not aligned to 32-byte records")
    names = [names_data[i:i + 32].split(b"\0", 1)[0].decode("utf-8")
             for i in range(0, len(names_data), 32)]
    names = [name for name in names if name]
    if len(names) != archive.nb_entries - 3:
        raise ValueError("Script name count does not match entries 3..251")
    if script_filter is not None and script_filter not in names:
        raise ValueError(f"Script {script_filter!r} does not exist")
    scripts = []
    for entry_index, name in enumerate(names, start=3):
        if script_filter is not None and name != script_filter:
            continue
        data = mzx_decompress(BytesIO(archive[entry_index].data),
                              invert_bytes=True).getvalue()
        commands = split_commands(name, data)
        scripts.append(Script(name, commands,
                              [c.command_index for c in commands
                               if ZM_RE.match(c.command)]))
    return scripts


def command_summary(command: Command, texts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "command_index": command.command_index,
        "byte_offset": command.byte_offset,
        "command": command.command,
        "text_ids": list(command.text_ids),
        "texts": [texts[i] if i < len(texts) else
                  {"text_id": i, "error": "text_id absent from script_text"}
                  for i in command.text_ids],
    }


def index_text_references(scripts: list[Script]) -> dict[int, list[tuple[Script, Command]]]:
    """Index every command occurrence under each distinct ID it references."""
    index: dict[int, list[tuple[Script, Command]]] = {}
    for script in scripts:
        for command in script.commands:
            for text_id in dict.fromkeys(command.text_ids):
                index.setdefault(text_id, []).append((script, command))
    return index


def nearest_zm(script: Script, command_index: int, direction: int) -> Command | None:
    position = bisect_left(script.zm_indices, command_index)
    if direction < 0:
        position -= 1
    elif position < len(script.zm_indices) and script.zm_indices[position] == command_index:
        position += 1
    if 0 <= position < len(script.zm_indices):
        return script.commands[script.zm_indices[position]]
    return None


def voice_id(command: str) -> str | None:
    match = VOICE_RE.match(command)
    return match.group(1) if match else None


def get_voice_prefix(asset_id: str | None) -> str | None:
    return asset_id[:3] if asset_id is not None else None


def get_voice_category(prefix: str | None) -> int | None:
    return VOICE_CATEGORIES.get(prefix, 0) if prefix is not None else None


def get_probable_speaker(category: int | None) -> tuple[str, str] | None:
    return PROBABLE_SPEAKERS.get(category)


def voice_metadata(asset_id: str | None) -> dict[str, object]:
    prefix = get_voice_prefix(asset_id)
    category = get_voice_category(prefix)
    probable_speaker = get_probable_speaker(category)
    return {
        "voice_id": asset_id,
        "voice_prefix": prefix,
        "voice_category": category,
        "speaker": probable_speaker[0] if probable_speaker else None,
        "speaker_ja": probable_speaker[1] if probable_speaker else None,
        "speaker_source": "voice_category_map" if probable_speaker else None,
        "speaker_confidence": "probable" if probable_speaker else None,
    }


def command_location(command: Command) -> dict[str, object]:
    return {"script": command.script, "command_index": command.command_index,
            "byte_offset": command.byte_offset, "command": command.command}


def occurrence(script: Script, current: Command,
               texts: list[dict[str, object]]) -> dict[str, object]:
    previous = nearest_zm(script, current.command_index, -1)
    following = nearest_zm(script, current.command_index, 1)
    before = script.commands[(previous.command_index + 1 if previous else 0):
                             current.command_index]
    after = script.commands[current.command_index + 1:
                             following.command_index if following else len(script.commands)]
    candidate_voices = [dict(command_location(cmd), voice_id=voice_id(cmd.command))
                        for cmd in before if voice_id(cmd.command) is not None]
    result: dict[str, object] = dict(
        command_location(current),
        text_ids=list(current.text_ids),
        texts=[texts[i] if i < len(texts) else
               {"text_id": i, "error": "text_id absent from script_text"}
               for i in current.text_ids],
        previous_zm=command_summary(previous, texts) if previous else None,
        next_zm=command_summary(following, texts) if following else None,
        commands_between_previous_and_current=[command_location(cmd) for cmd in before],
        commands_between_current_and_next=[command_location(cmd) for cmd in after],
        candidate_voice_commands=candidate_voices,
        voice_correlation="unverified source-order candidate",
    )
    if len(candidate_voices) == 1:
        result["candidate_voice_id"] = candidate_voices[0]["voice_id"]
    result.update(voice_metadata(result.get("candidate_voice_id")))
    return result


def text_query(text_id: int, texts: list[dict[str, object]],
               scripts: list[Script],
               reference_index: dict[int, list[tuple[Script, Command]]] | None = None
               ) -> dict[str, object]:
    if reference_index is None:
        reference_index = index_text_references(scripts)
    hits = [occurrence(script, cmd, texts)
            for script, cmd in reference_index.get(text_id, [])]
    messages = []
    if text_id >= len(texts):
        messages.append(f"text_id {text_id} does not exist in script_text")
    if not hits:
        messages.append(f"text_id {text_id} is not used in the selected allscr scripts")
    return {"mode": "text_id", "source_order": True,
            "text_id": text_id, "text": texts[text_id] if text_id < len(texts) else None,
            "occurrences": hits, "messages": messages}


def search_query(mode: str, term: str, texts: list[dict[str, object]],
                 scripts: list[Script]) -> dict[str, object]:
    pattern = re.compile(term) if mode == "grep_command" else None
    hits = []
    for script in scripts:
        for cmd in script.commands:
            if ((mode == "voice" and voice_id(cmd.command) == term) or
                    (pattern is not None and pattern.search(cmd.command))):
                previous = nearest_zm(script, cmd.command_index, -1)
                following = nearest_zm(script, cmd.command_index, 1)
                hit = dict(command_location(cmd),
                           nearest_previous_zm=command_summary(previous, texts) if previous else None,
                           nearest_next_zm=command_summary(following, texts) if following else None)
                if mode == "voice":
                    hit.update(voice_metadata(term))
                hits.append(hit)
    return {"mode": mode, "source_order": True, "query": term,
            "occurrences": hits,
            "messages": [] if hits else [f"No {mode} matches for {term!r}"]}


def format_zm(zm: dict[str, object] | None) -> str:
    if zm is None:
        return "none"
    lines = [f"#{zm['command_index']} @ byte {zm['byte_offset']}: {zm['command']}"]
    for record in zm["texts"]:
        lines.append(f"    [{record['text_id']}] JA: {record.get('ja', record.get('error'))}")
        if "en" in record:
            lines.append(f"             EN: {record['en']}")
    return "\n".join(lines)


def format_voice_metadata(hit: dict[str, object]) -> list[str]:
    speaker = hit["speaker"]
    return [
        f"voice_id: {hit['voice_id'] if hit['voice_id'] is not None else 'null'}",
        f"voice_prefix: {hit['voice_prefix'] if hit['voice_prefix'] is not None else 'null'}",
        f"voice_category: {hit['voice_category'] if hit['voice_category'] is not None else 'null'}",
        f"speaker: {speaker} ({hit['speaker_ja']})" if speaker else "speaker: UNKNOWN",
        f"speaker_source: {hit['speaker_source'] if hit['speaker_source'] is not None else 'null'}",
        f"speaker_confidence: {hit['speaker_confidence'] if hit['speaker_confidence'] is not None else 'null'}",
    ]


def format_result(result: dict[str, object]) -> str:
    lines = ["Source order (not runtime order). Command indices are zero-based; offsets are bytes."]
    if result["mode"] == "text_id":
        lines.append(f"text_id {result['text_id']}")
        if result["text"]:
            lines.extend([f"JA: {result['text']['ja']}", f"EN: {result['text']['en']}"])
        for hit in result["occurrences"]:
            lines.extend([
                "", f"Script {hit['script']} | command #{hit['command_index']} | byte {hit['byte_offset']}",
                f"ZM: {hit['command']}", f"text_ids: {hit['text_ids']}",
            ])
            for record in hit["texts"]:
                lines.append(f"  [{record['text_id']}] JA: {record.get('ja', record.get('error'))}")
                if "en" in record:
                    lines.append(f"           EN: {record['en']}")
            lines.extend(["previous_zm: " + format_zm(hit["previous_zm"]),
                          "commands_between_previous_and_current:"])
            lines.extend(f"  #{c['command_index']} @ byte {c['byte_offset']}: {c['command']}"
                         for c in hit["commands_between_previous_and_current"])
            if not hit["commands_between_previous_and_current"]:
                lines.append("  (none)")
            lines.append("next_zm: " + format_zm(hit["next_zm"]))
            lines.append("commands_between_current_and_next:")
            lines.extend(f"  #{c['command_index']} @ byte {c['byte_offset']}: {c['command']}"
                         for c in hit["commands_between_current_and_next"])
            if not hit["commands_between_current_and_next"]:
                lines.append("  (none)")
            lines.append("candidate_voice_commands (unverified source-order correlation):")
            lines.extend(f"  #{c['command_index']}: {c['command']} -> {c['voice_id']}"
                         for c in hit["candidate_voice_commands"])
            if not hit["candidate_voice_commands"]:
                lines.append("  (none)")
            lines.extend(format_voice_metadata(hit))
    else:
        lines.append(f"{result['mode']}: {result['query']}")
        for hit in result["occurrences"]:
            lines.extend(["", f"Script {hit['script']} | command #{hit['command_index']} | byte {hit['byte_offset']}",
                          hit["command"],
                          "nearest previous ZM: " + format_zm(hit["nearest_previous_zm"]),
                          "nearest next ZM: " + format_zm(hit["nearest_next_zm"])])
            if result["mode"] == "voice":
                lines.extend(format_voice_metadata(hit))
    lines.extend(result["messages"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text_id", nargs="?", type=int, help="decimal text ID")
    parser.add_argument("--text", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--scripts", type=Path, default=DEFAULT_SCRIPTS)
    parser.add_argument("--script", help="search only this script name")
    parser.add_argument("--voice", help="find this _VPLY voice ID")
    parser.add_argument("--grep-command", metavar="REGEX", help="search full commands")
    parser.add_argument("--json", action="store_true", help="emit complete JSON result")
    args = parser.parse_args(argv)
    if sum(value is not None for value in
           (args.text_id, args.voice, args.grep_command)) != 1:
        parser.error("provide exactly one of text_id, --voice, or --grep-command")
    if args.text_id is not None and args.text_id < 0:
        parser.error("text_id must be nonnegative")
    try:
        texts = load_texts(args.text)
        scripts = load_scripts(args.scripts, args.script)
        if args.text_id is not None:
            result = text_query(args.text_id, texts, scripts,
                                index_text_references(scripts))
        elif args.voice is not None:
            result = search_query("voice", args.voice, texts, scripts)
        else:
            result = search_query("grep_command", args.grep_command, texts, scripts)
    except (OSError, ValueError, UnicodeError, re.error) as exc:
        parser.exit(2, f"mahoyo_context: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else
          format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
