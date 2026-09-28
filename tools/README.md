# Narzędzia analizy tłumaczenia Mahoyo

Te skrypty czytają archiwa skryptów z własnego dumpa gry i budują dane pomocne przy tłumaczeniu. Nie modyfikują plików `.mrg`. Dumpy, RomFS/ExeFS, głosy i inne zasoby gry nie są częścią tego repozytorium.

## Wymagania i uruchomienie

Potrzebny jest Python 3.11+ oraz osobny checkout [loicfrance/mahoyo_tools](https://github.com/loicfrance/mahoyo_tools). `mahoyo_context.py` importuje stamtąd `mahoyo_tools.mzp` i `mahoyo_tools.mzx`; pliki tych modułów nie są kopiowane do tego repozytorium. Ustaw w `PYTHONPATH` **katalog nadrzędny** checkoutu upstream, tak aby Python widział katalog pakietu `mahoyo_tools`:

```bash
export PYTHONPATH="/ścieżka/do/katalogu/z/mahoyo_tools${PYTHONPATH:+:$PYTHONPATH}"
python tools/mahoyo_context.py 148 \
  --text /ścieżka/do/własnego/dumpa/script_text.mrg \
  --scripts /ścieżka/do/własnego/dumpa/allscr.mrg
```

Przykładowo, jeśli checkout upstream jest w `/mnt/Dane/Development/mahoyo_tools`, ustaw `PYTHONPATH=/mnt/Dane/Development`. Sam katalog `mahoyo_tools` nie wystarcza do importu `mahoyo_tools.mzp`. W środowisku z inną lokalizacją archiwów podaj jawnie `--text`, `--scripts` i, przy imporcie PL, `--pl-dir`; domyślne ścieżki w skryptach odnoszą się do lokalnego środowiska autora.

## `mahoyo_context.py`

Narzędzie wyszukuje `text_id` w `allscr.mrg` i pokazuje komendę `_ZM`, sąsiednie komendy, tekst JA/EN, kandydatów `_VPLY` i miejsce użycia. Dostępne są też `--script`, `--voice`, `--grep-command` i `--json` (szczegóły: `--help`). Indeksy komend liczone są od zera, a offsety dotyczą zdekompresowanego skryptu.

`voice_id` to pełny identyfikator zasobu z `_VPLY`, `voice_prefix` to jego trzy pierwsze znaki, a `voice_category` to kategoria wyznaczona z prefiksu. `speaker` i `speaker_ja` są **prawdopodobnym** przypisaniem z `voice_category_map` (`speaker_confidence: probable`), a nie technicznym nameplate. Gdy brak nowego kandydata `_VPLY`, pola głosu i speakera są puste; skrypt nie dziedziczy speakera z poprzedniej kwestii. Wiele kandydatów `_VPLY` pozostaje nierozstrzygniętych.

## `mahoyo_export_corpus.py`

`String` to jednostka tłumaczenia identyfikowana przez `text_id`. `Occurrence` to użycie tego stringa w komendzie skryptu, wskazujące go przez `text_id` i przechowujące miejsce użycia oraz kontekst. Jeden string może mieć wiele occurrences, ale ma jedną wartość PL. Wygeneruj projekt:

```bash
python tools/mahoyo_export_corpus.py project \
  --text /ścieżka/do/własnego/dumpa/script_text.mrg \
  --scripts /ścieżka/do/własnego/dumpa/allscr.mrg \
  --pl-dir /ścieżka/do/roboczych/Chapter-json \
  --output-dir /tmp/mahoyo_project
```

`--pl-dir` wskazuje katalog z `Chapter*.json` zawierającymi `ja`, `en`, `pl`. Eksporter dopasowuje całe sekwencje JA do tabeli z gry i dopiero potem przypisuje `text_id`; pusty lub zawierający tylko białe znaki PL staje się `null`. `--no-pl` pomija ten krok. Pliki źródłowe nie są zmieniane.

Projekt zawiera:

- `strings.jsonl`: jeden rekord na każde `text_id`, także nieużywane; pola `text_id`, `ja`, `en`, `pl` oraz puste początkowo `status`.
- `occurrences.jsonl`: jeden rekord na użycie w `_ZM`; zawiera `id` (`script:command_index:text_id`), `text_id`, lokalizację, metadane głosu i prawdopodobnego speakera oraz `context_before` i `context_after` jako listy sąsiednich `text_id`. Nie zawiera kopii `ja`, `en` ani `pl`; tekst pobiera się ze `strings.jsonl` przez `text_id`.
- `manifest.json`: liczniki stringów i occurrences, rozkład użyć, pokrycie PL, szerokość kontekstu oraz SHA-256 wejściowych archiwów. Nie zawiera bezwzględnych ścieżek źródeł.

Kontekst wynika z kolejności w źródłowym skrypcie (**source-order**), a nie z gwarantowanej kolejności wykonania w grze (**runtime-flow**). Domyślnie obejmuje trzy sąsiednie komendy `_ZM` z każdej strony; `--context N` zmienia tę liczbę.

Tryb `strings` eksportuje tylko `strings.jsonl`. Tryb bez podkomendy eksportuje pojedynczy plik occurrences, a `--legacy-inline-text` dodaje do niego kopie `ja/en/pl` dla starszych odbiorców. Szczegóły opcji: `python tools/mahoyo_export_corpus.py --help` i `python tools/mahoyo_export_corpus.py project --help`.

## Testy

```bash
PYTHONPATH="/ścieżka/do/katalogu/z/mahoyo_tools" \
  python -m unittest discover -s tools -p 'test_mahoyo_*.py'
```

Testy na prawdziwych archiwach uruchamiają się, jeśli domyślne lokalne ścieżki do dumpa są dostępne; pozostałe działają na danych przykładowych.
