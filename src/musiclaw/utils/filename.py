from __future__ import annotations

import re
from pathlib import Path


INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
TRAILING_RE = re.compile(r"[ .]+$")


def sanitize_filename(value: str, replacement: str = "_") -> str:
    cleaned = INVALID_CHARS_RE.sub(replacement, value).strip()
    cleaned = TRAILING_RE.sub("", cleaned)
    return cleaned or "untitled"


def build_track_filename(template: str, track_number: int, title: str, ext: str, disc_number: int | None = None) -> str:
    rendered = template.format(track=track_number, title=title, disc=disc_number or 1)
    rendered = sanitize_filename(rendered)
    suffix = ext if ext.startswith(".") else f".{ext}"
    return f"{rendered}{suffix}"


def target_path_for_track(source_path: Path, template: str, track_number: int, title: str, disc_number: int | None = None) -> Path:
    filename = build_track_filename(template, track_number, title, source_path.suffix, disc_number)
    return source_path.with_name(filename)
