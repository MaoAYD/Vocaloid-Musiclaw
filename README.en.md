# MusicLaw

`MusicLaw` is a local-first metadata collection and tagging tool designed for VOCALOID, UTAU, Synthesizer V, CeVIO, VOICEROID-derived singing projects, and other synthetic singing software doujin albums.

It scans album folders, searches source sites such as `VocaDB`, `VCPedia`, and `dizzylab`, combines site-specific parsing with an optional OpenAI-compatible LLM workflow, and writes reviewed metadata back to your local files.

## What This Release Contains

This `release/` package is sanitized for publication:

- source code only under `src/musiclaw`
- default configuration files only
- empty runtime folders for `cache`, `reports`, `snapshots`, `logs`, and `temp`
- no test data, no user music files, no generated reports, no cached CSVs, and no snapshots

## Main Features

- built for niche VOCALOID and synthetic voice doujin album metadata collection
- scans album folders where tracks are ordered as `01.xxx`, `02.xxx`, `03.xxx`, etc.
- searches `VocaDB`, `VCPedia`, and `dizzylab`
- supports manual priority URLs per album
- supports manual raw text as primary evidence for handwritten notes and shorthand tracklists
- uses conservative matching plus optional LLM extraction/resolution
- keeps a review-first workflow before writing tags or renaming files
- includes a desktop GUI built with Qt (`PySide6`)

## Requirements

- Python `3.11+`
- Windows is the primary tested platform for this release
- an OpenAI-compatible API endpoint is optional, not required

## Installation

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
pip install -e .
scrapling install
```

If you do not want editable install, you can still install the package normally from the release folder:

```bash
pip install .
scrapling install
```

## Configuration

Start from `config.example.toml`:

```bash
copy config.example.toml config.toml
```

Then edit the following if needed:

- `root.music_dir`: your music library root
- `sources.enabled`: enabled source adapters
- `matching.*`: review thresholds
- `processing.*`: album/query concurrency
- `tags.*`: write and rename behavior

### Optional LLM Environment Variables

If you want to enable the LLM workflow, set:

- `MUSICLAW_LLM_BASE_URL`
- `MUSICLAW_LLM_API_KEY`
- `MUSICLAW_LLM_MODEL`

Without these values, the heuristic workflow still works.

## Command Line Usage

### Scan albums

```bash
musiclaw scan --root D:/Albums --config config.toml
```

### Build a review report

```bash
musiclaw match --root D:/Albums --config config.toml --report reports/latest.json
```

### Review the report in the terminal

```bash
musiclaw review --report reports/latest.json
```

### Apply reviewed metadata

```bash
musiclaw apply --report reports/reviewed.json --config config.toml --output reports/apply.json
```

## GUI Usage

Launch the desktop interface:

```bash
musiclaw-gui
```

Recommended GUI workflow:

1. Select your music folder.
2. Check the detected album list.
3. Edit `Search album name` if needed.
4. Add `Priority URLs` for pages you want fetched first.
5. Paste `Manual raw text` if you have handwritten notes, copied descriptions, shorthand tracklists, or staff notes.
6. Run matching.
7. Review evidence, track artist/source, and conflicts.
8. Mark albums as verified, then apply.

## Manual Raw Text Tips

The tool is specifically tuned for messy album notes. Examples it tries to understand:

```text
Title: Album Name
Circle: Circle Name
Album artist: Singer A
Catalog: ABC-123
Event: M3-2026春

全碟演唱: 星尘Infinity
包含曲目:
1. 当美梦浮于夜空
2. 新人类
3. 行星

M1 Song A / Vocal A
Track 2 Song B (Vocal: Singer B)
01) Song C
Tr4 Song D - Singer D

作曲: Composer A
编曲: Arranger B
调校: Tuner C
PV: Visual D
曲绘: Illustrator E
```

Manual raw text is treated as primary evidence when it explicitly states metadata.

## File Structure

```text
release/
|- README.md
|- README.en.md
|- README.zh-CN.md
|- pyproject.toml
|- requirements.txt
|- config.example.toml
|- .gitignore
|- src/
|  \- musiclaw/
|     |- __main__.py
|     |- cli.py
|     |- collector.py
|     |- config.py
|     |- gui.py
|     |- matcher.py
|     |- models.py
|     |- pipeline.py
|     |- reporter.py
|     |- scanner.py
|     |- llm/
|     |- sources/
|     |- tagger/
|     \- utils/
|- cache/
|- reports/
|- snapshots/
|- logs/
\- temp/
   \- vocadb_csv/
```

## Runtime Folders

- `cache/`: HTTP, search, and LLM caches
- `reports/`: generated JSON reports
- `snapshots/`: pre-apply snapshots
- `logs/`: reserved for future runtime logs
- `temp/vocadb_csv/`: temporary VocaDB CSV cache files

## Privacy and Safety Notes

- this release intentionally excludes generated caches and test artifacts
- your own reports, snapshots, and temp files will be created only after you use the tool
- `match` is review-first; it does not directly modify your music files
- `apply` only works on reviewed and approved results

## Source Notes

- `VocaDB`: good for album, song, and vocalist-linked data; the adapter can also build/use track CSV data
- `VCPedia`: useful for Chinese synthetic singing releases and explicit track vocal tables
- `dizzylab`: useful for doujin release pages and storefront metadata

## Known Scope

This project is specialized. It is not intended to be a universal music tagger for all commercial music libraries. It is optimized for:

- VOCALOID / UTAU / SynthV / CeVIO style releases
- doujin albums and event-distributed albums
- handwritten or semi-structured metadata notes
- human-reviewed local tagging workflows
