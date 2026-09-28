# Mahōtsukai no Yoru - polskie tłumaczenie

Otwartoźródłowe tłumaczenie [Mahōtsukai no Yoru](http://typemoon.com/products/mahoyo/) na język polski.

## Praca w toku

Prosimy o zwrócenie uwagi na fakt, że nie jest to finalne tłumaczenie, a prace cały czas trwają.

## Dane tłumaczenia

Projekt publikuje pełny corpus w czterech plikach w katalogu głównym:

- `strings.jsonl` — kanoniczny tekst japoński i angielski, jeden wiersz na `text_id`.
- `translation.jsonl` — pełne rekordy do pracy nad tłumaczeniem: JA, EN, PL oraz lista wszystkich miejsc użycia z kontekstem i metadanymi głosu/mówcy; jeden wiersz na `text_id`.
- `occurrences.jsonl` — miejsca użycia tekstu w skryptach, kontekst w kolejności źródłowej oraz metadane głosu i prawdopodobnego mówcy.
- `manifest.json` — wersja formatu, pochodzenie danych, sumy SHA-256 i statystyki.

Pliki `text/Chapter*.json` zostały wycofane. W `translation.jsonl` nieprzetłumaczone teksty mają `pl: null`, a teksty bez miejsca użycia `occurrences: []`. Połączenie danych odbywa się przez `text_id`.

## Podziękowania

Type-Moon za wydanie tej [gry](http://typemoon.com/products/mahoyo/)!

## Tooling

Narzędzia do analizy skryptów i eksportu danych tłumaczeniowych opisano w [tools/README.md](tools/README.md).
