# Narzędzia analizy tłumaczenia Mahoyo

Te skrypty czytają archiwa skryptów z dumpa gry i budują publiczny corpus tłumaczeniowy. Nie modyfikują plików `.mrg`. Archiwa `.mrg`, RomFS/ExeFS, głosy i inne zasoby binarne gry nie są częścią tego repozytorium.

## Wymagania i uruchomienie

Potrzebny jest Python 3.11+ oraz submoduł [loicfrance/mahoyo_tools](https://github.com/loicfrance/mahoyo_tools) przypięty w katalogu `mahoyo_tools/`. `mahoyo_context.py` importuje z niego `mahoyo_tools.mzp` i `mahoyo_tools.mzx`. To repozytorium zapisuje adres i commit submodułu, nie kopię jego plików. Przypięta wersja upstream nie zawiera pliku `LICENSE`; dodanie submodułu nie zmienia tego stanu.

Przy nowym klonowaniu użyj `git clone --recurse-submodules https://github.com/WorkingWarrior/mahoyo.git`. W już sklonowanym repo uruchom:

```bash
git submodule update --init
```

Z katalogu głównego repozytorium:

```bash
python tools/mahoyo_context.py 148 \
  --text /ścieżka/do/własnego/dumpa/script_text.mrg \
  --scripts /ścieżka/do/własnego/dumpa/allscr.mrg
```

`PYTHONPATH` nie trzeba ustawiać. Podaj jawnie `--text`, `--scripts` i, przy imporcie PL ze starych plików, `--pl-dir`; domyślne ścieżki w skryptach odnoszą się do lokalnego środowiska autora.

## `mahoyo_context.py`

Narzędzie wyszukuje `text_id` w `allscr.mrg` i pokazuje komendę `_ZM`, sąsiednie komendy, tekst JA/EN, kandydatów `_VPLY` i miejsce użycia. Dostępne są też `--script`, `--voice`, `--grep-command` i `--json` (szczegóły: `--help`). Indeksy komend liczone są od zera, a offsety dotyczą zdekompresowanego skryptu.

`voice_id` to pełny identyfikator zasobu z `_VPLY`, `voice_prefix` to jego trzy pierwsze znaki, a `voice_category` to kategoria wyznaczona z prefiksu. `speaker` i `speaker_ja` są **prawdopodobnym** przypisaniem z `voice_category_map` (`speaker_confidence: probable`), a nie technicznym nameplate. Gdy brak nowego kandydata `_VPLY`, pola głosu i speakera są puste; skrypt nie dziedziczy speakera z poprzedniej kwestii. Wiele kandydatów `_VPLY` pozostaje nierozstrzygniętych.

## `mahoyo_export_corpus.py`

`String` to jednostka tekstu identyfikowana przez `text_id`. `Occurrence` to użycie tego stringa w komendzie skryptu, wskazujące go przez `text_id` i przechowujące miejsce użycia oraz kontekst. Jeden string może mieć wiele occurrences, ale ma jedną wartość PL w `translation.jsonl`. Przy migracji z lokalnych kanonicznych plików `Chapter*.json` wygeneruj zestaw w katalogu głównym repo:

```bash
python tools/mahoyo_export_corpus.py project \
  --text /ścieżka/do/własnego/dumpa/script_text.mrg \
  --scripts /ścieżka/do/własnego/dumpa/allscr.mrg \
  --pl-dir /ścieżka/do/kanonicznych/Chapter-json \
  --output-dir .
```

`--pl-dir` wskazuje katalog z kanonicznymi `Chapter*.json`. W nowym formacie
eksporter używa jawnego `text_id`; w starym dopasowuje całe sekwencje JA do
tabeli z gry. Pusty lub zawierający tylko białe znaki PL staje się `null`.
`*_AUTOTRANSLATED.json` są pomijane. `--no-pl` tworzy pełny `translation.jsonl` z `pl: null`.
Pliki źródłowe nie są zmieniane. Przy kolejnych eksportach użyj
`--translation translation.jsonl` zamiast `--pl-dir`, aby zachować bieżące
polskie zmiany. Eksporter wczytuje tłumaczenie przed zapisaniem plików, więc
może ono leżeć w katalogu wyjściowym.

Projekt zawiera:

- `strings.jsonl`: jeden rekord na każde `text_id`, także nieużywane; pola `text_id`, `ja`, `en`.
- `translation.jsonl`: jeden pełny rekord na każde `text_id`; pola `text_id`, `ja`, `en`, `pl` i `occurrences`. Każde occurrence zawiera `id`, scenę/skrypt, pozycję komendy, kontekst w `text_id`, voice i metadane prawdopodobnego mówcy. Nieużywany string ma pustą listę occurrences. To gotowy widok do tłumaczenia bez ręcznego łączenia plików.
- `occurrences.jsonl`: jeden rekord na użycie w `_ZM`; zawiera `id` (`script:command_index:text_id`), `text_id`, lokalizację, metadane głosu i prawdopodobnego speakera oraz `context_before` i `context_after` jako listy sąsiednich `text_id`. Nie zawiera kopii `ja`, `en` ani `pl`; tekst pobiera się ze `strings.jsonl` przez `text_id`.
- `manifest.json`: wersja formatu, liczniki stringów, occurrences, rekordów translacji i niepustych PL, rozkład użyć, szerokość kontekstu oraz SHA-256 archiwów wejściowych i trzech pozostałych plików. Nie zawiera bezwzględnych ścieżek źródeł.

Kontekst wynika z kolejności w źródłowym skrypcie (**source-order**), a nie z gwarantowanej kolejności wykonania w grze (**runtime-flow**). Domyślnie obejmuje trzy sąsiednie komendy `_ZM` z każdej strony; `--context N` zmienia tę liczbę.

Tryb `strings` eksportuje tylko `strings.jsonl`. Tryb bez podkomendy eksportuje pojedynczy plik occurrences, a `--legacy-inline-text` dodaje do niego kopie `ja/en/pl` dla starszych odbiorców. Publiczny format repo tworzy tryb `project`; stare `Chapter*.json` zostały wycofane. Szczegóły opcji: `python tools/mahoyo_export_corpus.py --help` i `python tools/mahoyo_export_corpus.py project --help`.

## Testy

```bash
python -m unittest discover -s tools -p 'test_mahoyo_*.py'
```

Testy na prawdziwych archiwach uruchamiają się, jeśli domyślne lokalne ścieżki do dumpa są dostępne; pozostałe działają na danych przykładowych.
