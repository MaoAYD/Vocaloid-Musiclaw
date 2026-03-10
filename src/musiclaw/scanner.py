from __future__ import annotations

import re
from pathlib import Path

from mutagen import File as MutagenFile

from musiclaw.models import LocalAlbum, LocalTrack, SearchOverrides, SearchQuery
from musiclaw.utils.textnorm import collapse_spaces, extract_catalog_no, extract_year, strip_brackets


AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav"}
LEADING_TRACK_RE = re.compile(r"^(?P<track>\d{1,3})(?:[ ._\-]+(?P<rest>.*))?$")


def scan_music_root(root_path: Path) -> list[LocalAlbum]:
    albums: list[LocalAlbum] = []
    for path in sorted(root_path.iterdir()):
        if not path.is_dir():
            continue
        tracks = _scan_album_tracks(path)
        if not tracks:
            continue
        hints = parse_album_folder_name(path.name)
        albums.append(
            LocalAlbum(
                folder_path=path,
                folder_name=path.name,
                files=tracks,
                guessed_title=hints["title"],
                guessed_circle=hints["circle"],
                guessed_catalog_no=hints["catalog_no"],
                guessed_event=hints["event"],
                guessed_year=hints["year"],
            )
        )
    return albums


def _scan_album_tracks(album_dir: Path) -> list[LocalTrack]:
    tracks: list[LocalTrack] = []
    audio_files = [path for path in album_dir.iterdir() if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
    ordered = sorted(audio_files, key=_track_sort_key)
    for index, path in enumerate(ordered, start=1):
        tracks.append(
            LocalTrack(
                path=path,
                index=index,
                ext=path.suffix.lower().lstrip("."),
                existing_tags=read_existing_tags(path),
            )
        )
    return tracks


def _track_sort_key(path: Path) -> tuple[int, str]:
    stem = path.stem
    match = LEADING_TRACK_RE.match(stem)
    if match:
        return (int(match.group("track")), stem.casefold())
    return (10_000, stem.casefold())


def read_existing_tags(path: Path) -> dict[str, object]:
    try:
        audio = MutagenFile(path)
    except Exception:
        return {}
    if not audio or not getattr(audio, "tags", None):
        return {}
    tags = {}
    for key, value in audio.tags.items():
        try:
            tags[str(key)] = list(value) if isinstance(value, list) else str(value)
        except Exception:
            tags[str(key)] = repr(value)
    return tags


def parse_album_folder_name(folder_name: str) -> dict[str, str | None]:
    working = collapse_spaces(folder_name)
    parts = re.findall(r"(\[[^\]]+\]|\([^\)]+\)|【[^】]+】|[^\[(【]+)", working)
    cleaned_parts = [collapse_spaces(part) for part in parts if collapse_spaces(part)]
    circle = None
    title_parts: list[str] = []
    event = None

    for index, part in enumerate(cleaned_parts):
        stripped = strip_brackets(part)
        if index == 0 and part.startswith(("[", "【")):
            circle = stripped
            continue
        if event is None and any(token in stripped.casefold() for token in ("m3", "reitaisai", "c10", "comic", "秋", "春", "夏", "冬")):
            event = stripped
            continue
        title_parts.append(stripped)

    title_candidate = " ".join(title_parts).strip() or working
    catalog_no = extract_catalog_no(working)
    year = extract_year(working)

    if catalog_no:
        title_candidate = title_candidate.replace(catalog_no, "").strip(" -_()[]")
    if year:
        title_candidate = title_candidate.replace(year, "").strip(" -_()[]")

    return {
        "title": collapse_spaces(title_candidate) or None,
        "circle": circle,
        "catalog_no": catalog_no,
        "event": event,
        "year": year,
    }


def build_search_queries(album: LocalAlbum, overrides: SearchOverrides | None = None) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    seen: set[str] = set()

    effective_title = collapse_spaces((overrides.album_title if overrides and overrides.album_title else album.guessed_title) or "")
    fallback_name = collapse_spaces(album.folder_name)

    candidates = [
        effective_title,
        fallback_name,
    ]
    for value in candidates:
        if not value:
            continue
        normalized = value.casefold().strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        queries.append(
            SearchQuery(
                raw_query=value,
                title=effective_title or fallback_name,
            )
        )
    return queries
