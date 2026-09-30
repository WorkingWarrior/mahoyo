# Narzędzia analizy tłumaczenia Mahoyo

Eksporter czyta archiwa skryptów z dumpa gry i buduje publiczny corpus tłumaczeniowy. Importer tworzy nowe archiwum z tłumaczeniem. Archiwa `.mrg`, RomFS/ExeFS, głosy i inne zasoby binarne gry nie są częścią tego repozytorium.

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

## `mahoyo_import_translation.py`

Importer bierze **oryginalne** `script_text.mrg`, sprawdza jego SHA-256 z `manifest.json`, wszystkie 24 136 rekordów JA/EN z `strings.jsonl` oraz tłumaczenie. Przebudowuje tablicę offsetów EN i całe archiwum `mrgd00` przy użyciu writera z submodułu. Przed opublikowaniem pliku wynikowego ponownie go parsuje i porównuje wszystkie pięć języków oraz niezmieniane entries. Nie używa legacy importerów `H/Apply.py` i `H/applier.py`.

```bash
python tools/mahoyo_import_translation.py \
  --source /ścieżka/do/własnego/dumpa/script_text.mrg \
  --strings strings.jsonl \
  --translation translation.jsonl \
  --manifest manifest.json \
  --output build/script_text.mrg
```

Dodaj `--dry-run`, aby wykonać pełną walidację i repack w pamięci bez zapisu. `--force-source-mismatch` omija wyłącznie kontrolę SHA-256 źródła; zgodność `strings.jsonl` ze źródłem pozostaje wymagana. Importer odrzuca próbę zapisania wyniku w ścieżce źródła. Katalog wyjściowy tworzy automatycznie. Po błędzie walidacji wynik nie jest publikowany.

Obecny `translation.jsonl` jest pełnym widokiem z 24 136 rekordami, z których 2 110 ma PL, a pozostałe mają `pl: null`. Importer obsługuje też format sparse `{ "text_id": ..., "pl": "..." }`; brak rekordu oznacza zachowanie oficjalnego EN. `occurrences.jsonl` nie bierze udziału w repacku. Polski tekst trafia do slotu EN, a JA/ZH/ZH2/KO pozostają logicznie identyczne.

Eksperyment na obecnym archiwum potwierdził, że repack bez zmian przy użyciu przypiętego writera jest bajtowo identyczny. Importer zachowuje także surowe bajty niezmienianych rekordów EN, w tym dwa puste rekordy końcowe bez terminatora CRLF.

## Weblate

Adapter tools/mahoyo_weblate.py używa wyłącznie biblioteki standardowej Pythona. JSONL pozostaje źródłem danych:

    python tools/mahoyo_weblate.py validate
    python tools/mahoyo_weblate.py export
    python tools/mahoyo_weblate.py validate --po weblate/pl.po
    python tools/mahoyo_weblate.py import

Ten starszy tryb eksportu tworzy weblate/mahoyo.pot (JA jako msgid), weblate/en.po (EN jako msgstr) i weblate/pl.po (PL jako msgstr). Dotyczy komponentu ze źródłem JA i pozostaje dostępny do odtworzenia starego zestawu.

msgctxt zawiera wyłącznie stabilne text_id i kanonicznego mówcę (`speaker=unknown`, gdy nie ma jednoznacznego przypisania). Komentarze #. podają metadane każdego occurrence osobno, w tym japońską nazwę mówcy, głos, źródło i pewność przypisania oraz scenę; #: wskazuje skrypt i indeks komendy. Wiele occurrences jednego text_id nadal tworzy jeden wpis PO i jedno tłumaczenie PL. Import wymaga pełnego pl.po, sprawdza każde text_id i japoński tekst źródłowy oraz odrzuca wpisy fuzzy. Pusty msgstr wraca jako pl: null. Zapis aktualizuje tylko pole pl w translation.jsonl; pozostałe pola i niezmienione wiersze są zachowywane. Opcje --strings, --translation, --occurrences, --output-dir i --po pozwalają pracować na kopii danych.

## Weblate ze źródłem EN

Nowy adapter tools/mahoyo_weblate_en.py wymaga biblioteki polib:

    python -m pip install -r tools/requirements-weblate.txt

Eksportuj z Weblate starego komponentu aktualne ja.po, en.po i pl.po do osobnego katalogu. Zablokuj edycję starego komponentu na czas końcowego pobrania. Lokalna wersja PL w translation.jsonl może być starsza od Weblate, więc generator bierze PL wyłącznie z pobranego pl.po:

    python tools/mahoyo_weblate_en.py export --snapshot-dir /ścieżka/do/pobranego-zestawu --exclude-empty-unused
    python tools/mahoyo_weblate_en.py validate --snapshot-dir /ścieżka/do/pobranego-zestawu --exclude-empty-unused

Wynikiem są weblate-en/mahoyo.pot, weblate-en/ja.po, weblate-en/pl.po oraz migration-manifest.json. Źródłowy msgid to dokładny EN z Weblate, JA trafia do msgstr w ja.po, a PL z Weblate do msgstr w pl.po. Generator łączy wpisy po text_id, zachowuje msgctxt, komentarze, locations i flagi PL, w tym fuzzy; zatrzymuje się przy brakujących identyfikatorach, zmienionym EN, liczbie mnogiej oraz flagach formatu wymagających ręcznej kontroli. Opcja --exclude-empty-unused pomija tylko nieużywane rekordy z pustymi JA/EN/PL; obecnie są to text_id 24134 i 24135. Nadal pozostają one w JSONL.

Po zmianach w nowym komponencie można zsynchronizować jego PL do kanonicznego translation.jsonl:

    python tools/mahoyo_weblate_en.py import --exclude-empty-unused

Import sprawdza text_id oraz dokładny angielski msgid. Zmienia tylko pole pl; przy fuzzy przerywa, bo JSONL nie ma pola statusu. Narzędzie mahoyo_import_translation.py nadal używa text_id z translation.jsonl do eksportu gry, więc nie zależy od języka msgid. Sam eksport EN nie zmienia zasobów gry.

## Testy

```bash
python -m unittest discover -s tools -p 'test_mahoyo_*.py'
```

Testy na prawdziwych archiwach uruchamiają się, jeśli domyślne lokalne ścieżki do dumpa są dostępne; pozostałe działają na danych przykładowych.
