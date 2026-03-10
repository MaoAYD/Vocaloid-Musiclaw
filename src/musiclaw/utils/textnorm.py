from __future__ import annotations

import re
import unicodedata


WHITESPACE_RE = re.compile(r"\s+")
CATALOG_RE = re.compile(r"\b(?=[A-Z0-9_-]{4,})(?=[A-Z0-9_-]*\d)[A-Z]{1,10}[A-Z0-9]*(?:[-_][A-Z0-9]+)+\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
BRACKET_RE = re.compile(r"^[\[(【](.*?)[\])】]$")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("_", " ")
    normalized = WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized.casefold()


def collapse_spaces(value: str | None) -> str:
    return WHITESPACE_RE.sub(" ", value or "").strip()


def extract_catalog_no(value: str | None) -> str | None:
    if not value:
        return None
    match = CATALOG_RE.search(unicodedata.normalize("NFKC", value).upper())
    return match.group(0) if match else None


def extract_year(value: str | None) -> str | None:
    if not value:
        return None
    match = YEAR_RE.search(value)
    return match.group(0) if match else None


def strip_brackets(value: str) -> str:
    match = BRACKET_RE.match(value.strip())
    return match.group(1).strip() if match else value.strip()
