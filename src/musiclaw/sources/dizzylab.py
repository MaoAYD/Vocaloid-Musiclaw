from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from musiclaw.models import FieldEvidence, SearchCandidate, SearchQuery, SourceEvidence, SourceName, StructuredAlbumPage, StructuredTrack
from musiclaw.sources.base import SourceAdapter
from musiclaw.utils.html import all_texts, attr_value, document_text, first_attr, first_text, node_text, parse_html
from musiclaw.utils.textnorm import collapse_spaces


DIZZYLAB_BASE = "https://www.dizzylab.net"
TRACK_RE = re.compile(
    r"^\s*(?P<number>\d{1,2})\.\s*(?P<title>.+?)(?:\s+-\s+(?P<artist>.+?))?(?:\s*\((?P<duration>\d{2}:\d{2})\))?\s*$"
)
TRACK_ALT_RE = re.compile(
    r"^\s*(?P<number>\d{1,2})[.、]\s*(?P<title>.+?)(?:\s*(?:/|／|by)\s*(?P<artist>.+?))?(?:\s*\((?P<duration>\d{2}:\d{2})\))?\s*$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"发布于\s*(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日")


class DizzylabAdapter(SourceAdapter):
    source_name = SourceName.DIZZYLAB.value
    enum_name = SourceName.DIZZYLAB
    url_hosts = ("dizzylab.net",)

    def search(self, query: SearchQuery) -> list[SearchCandidate]:
        cache_key = f"search:{query.raw_query}"
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            return [SearchCandidate.model_validate(item) for item in cached]

        url = f"{DIZZYLAB_BASE}/search/?q={quote(query.raw_query)}"
        response = self.client.fetch_html(url)
        candidates = parse_dizzylab_search_html(response.text)
        limited = candidates[: self.config.sources.max_candidates]
        self.cache.store(self.source_name, cache_key, [candidate.model_dump(mode="json") for candidate in limited])
        return limited

    def fetch_detail(self, candidate: SearchCandidate) -> SourceEvidence:
        cache_key = f"detail:{candidate.url}"
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            return SourceEvidence.model_validate(cached)

        response = self.client.fetch_html(candidate.url)
        evidence = parse_dizzylab_detail_html(response.text, response.url)
        self.cache.store(self.source_name, cache_key, evidence.model_dump(mode="json"))
        return evidence

    def normalize(self, evidence: SourceEvidence) -> StructuredAlbumPage:
        return normalize_dizzylab_evidence(evidence)


def parse_dizzylab_search_html(html: str) -> list[SearchCandidate]:
    root = parse_html(html)
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    for anchor in root.css('a[href^="/d/"]'):
        href = attr_value(anchor, "href")
        if not href or href in seen:
            continue
        title = node_text(anchor)
        if not title or title in {"更多", "more"}:
            continue
        seen.add(href)
        candidates.append(
            SearchCandidate(
                source=SourceName.DIZZYLAB,
                url=urljoin(DIZZYLAB_BASE, href),
                title_hint=title,
            )
        )
    return candidates


def parse_dizzylab_detail_html(html: str, url: str) -> SourceEvidence:
    root = parse_html(html)
    page_title = first_text(root, "title") or "dizzylab"
    clean_text = document_text(root)

    title = _pick_title(root, clean_text)
    circle = _pick_circle(root)
    release_date = _pick_release_date(clean_text)
    cover_url = _pick_cover(root, url)
    tags = [text.lstrip("#") for text in all_texts(root, 'a[href*="/albums/tags/"]')]
    tracks = _parse_tracks(clean_text)

    extracted_fields = {
        "title": title,
        "circle": circle,
        "release_date": release_date,
        "cover_url": cover_url,
        "tags": tags,
        "tracks": [track.model_dump() for track in tracks],
    }
    snippets = [track.evidence for track in tracks if track.evidence]
    return SourceEvidence(
        source=SourceName.DIZZYLAB,
        url=url,
        page_title=page_title,
        cleaned_text=clean_text,
        extracted_snippets=snippets,
        extracted_fields=extracted_fields,
        raw_html=html,
    )


def normalize_dizzylab_evidence(evidence: SourceEvidence) -> StructuredAlbumPage:
    payload = evidence.extracted_fields
    return StructuredAlbumPage(
        source=SourceName.DIZZYLAB,
        url=evidence.url,
        title=FieldEvidence(value=payload.get("title"), evidence=payload.get("title"), source_url=evidence.url, confidence=0.95),
        circle=FieldEvidence(value=payload.get("circle"), evidence=payload.get("circle"), source_url=evidence.url, confidence=0.9),
        album_artist=FieldEvidence(value=payload.get("circle"), evidence=payload.get("circle"), source_url=evidence.url, confidence=0.7),
        release_date=FieldEvidence(value=payload.get("release_date"), evidence=payload.get("release_date"), source_url=evidence.url, confidence=0.8),
        cover_url=FieldEvidence(value=payload.get("cover_url"), evidence=payload.get("cover_url"), source_url=evidence.url, confidence=0.9),
        tags=list(payload.get("tags", [])),
        tracks=[StructuredTrack.model_validate(track) for track in payload.get("tracks", [])],
        raw_payload=payload,
    )


def _pick_title(root, clean_text: str) -> str | None:
    og_title = first_attr(root, 'meta[property="og:title"]', "content")
    if og_title:
        return collapse_spaces(og_title.split(" - dizzylab")[0])
    heading = first_text(root, "h1") or first_text(root, "h2")
    if heading:
        return heading
    lines = [collapse_spaces(line) for line in clean_text.splitlines() if collapse_spaces(line)]
    return lines[0] if lines else None


def _pick_circle(root) -> str | None:
    for anchor in root.css('a[href^="/l/"]'):
        text = node_text(anchor).lstrip("@ ")
        if text:
            return text
    return None


def _pick_release_date(clean_text: str) -> str | None:
    match = DATE_RE.search(clean_text)
    if not match:
        return None
    return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def _pick_cover(root, url: str) -> str | None:
    og_image = first_attr(root, 'meta[property="og:image"]', "content")
    if og_image:
        return urljoin(url, og_image)
    for image in root.css("img[src]"):
        src = attr_value(image, "src")
        if src and "/media/cover/" in src:
            return urljoin(url, src)
    return None


def _parse_tracks(clean_text: str) -> list[StructuredTrack]:
    tracks: list[StructuredTrack] = []
    for raw_line in clean_text.splitlines():
        line = collapse_spaces(raw_line)
        if not line:
            continue
        match = TRACK_RE.match(line) or TRACK_ALT_RE.match(line)
        if not match:
            continue
        tracks.append(
            StructuredTrack(
                number=int(match.group("number")),
                title=collapse_spaces(match.group("title")),
                artist=collapse_spaces(match.group("artist")) or None,
                duration=match.group("duration"),
                evidence=line,
            )
        )
    return tracks
