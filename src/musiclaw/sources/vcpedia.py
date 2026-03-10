from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from musiclaw.models import FieldEvidence, SearchCandidate, SearchQuery, SourceEvidence, SourceName, StructuredAlbumPage, StructuredTrack
from musiclaw.sources.base import SourceAdapter
from musiclaw.utils.html import all_texts, attr_value, document_text, first_attr, first_text, node_text, parse_html
from musiclaw.utils.textnorm import collapse_spaces


VCPEDIA_BASE = "https://vcpedia.cn"
TRACK_LINE_RE = re.compile(r"^(?P<number>\d{1,2})[.、\-\s]+(?P<title>.+)$")
TRACK_ARTIST_RE = re.compile(
    r"^(?P<number>\d{1,2})[.、\-\s]+(?P<title>.+?)(?:\s*(?:-|/|／|by|feat\.?|ft\.?)\s*(?P<artist>[^()]+?))?(?:\s*\((?P<duration>\d{2}:\d{2})\))?$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(19|20)\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?")


class VCPediaAdapter(SourceAdapter):
    source_name = SourceName.VCPEDIA.value
    enum_name = SourceName.VCPEDIA
    url_hosts = ("vcpedia.cn",)

    def search(self, query: SearchQuery) -> list[SearchCandidate]:
        cache_key = f"search:{query.raw_query}"
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            return [SearchCandidate.model_validate(item) for item in cached]

        url = f"{VCPEDIA_BASE}/index.php?search={quote(query.raw_query)}"
        response = self.client.fetch_html(url, stealth=True)
        candidates = parse_vcpedia_search_html(response.text)
        limited = candidates[: self.config.sources.max_candidates]
        self.cache.store(self.source_name, cache_key, [candidate.model_dump(mode="json") for candidate in limited])
        return limited

    def fetch_detail(self, candidate: SearchCandidate) -> SourceEvidence:
        cache_key = f"detail:{candidate.url}"
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            return SourceEvidence.model_validate(cached)

        response = self.client.fetch_html(candidate.url, stealth=True)
        evidence = parse_vcpedia_detail_html(response.text, response.url)
        self.cache.store(self.source_name, cache_key, evidence.model_dump(mode="json"))
        return evidence

    def normalize(self, evidence: SourceEvidence) -> StructuredAlbumPage:
        payload = evidence.extracted_fields
        notes = list(payload.get("notes", []))
        if not payload.get("is_album_page", False):
            notes.append("VCPedia page is not clearly categorized as an album page.")
        return StructuredAlbumPage(
            source=SourceName.VCPEDIA,
            url=evidence.url,
            title=FieldEvidence(value=payload.get("title"), evidence=payload.get("title"), source_url=evidence.url, confidence=0.8) if payload.get("title") else None,
            circle=FieldEvidence(value=payload.get("circle"), evidence=payload.get("circle"), source_url=evidence.url, confidence=0.7) if payload.get("circle") else None,
            album_artist=FieldEvidence(value=payload.get("album_artist") or payload.get("circle"), evidence=payload.get("album_artist") or payload.get("circle"), source_url=evidence.url, confidence=0.6) if payload.get("album_artist") or payload.get("circle") else None,
            catalog_no=FieldEvidence(value=payload.get("catalog_no"), evidence=payload.get("catalog_no"), source_url=evidence.url, confidence=0.6) if payload.get("catalog_no") else None,
            release_date=FieldEvidence(value=payload.get("release_date"), evidence=payload.get("release_date"), source_url=evidence.url, confidence=0.6) if payload.get("release_date") else None,
            event_name=FieldEvidence(value=payload.get("event_name"), evidence=payload.get("event_name"), source_url=evidence.url, confidence=0.6) if payload.get("event_name") else None,
            cover_url=FieldEvidence(value=payload.get("cover_url"), evidence=payload.get("cover_url"), source_url=evidence.url, confidence=0.7) if payload.get("cover_url") else None,
            tags=list(payload.get("tags", [])),
            tracks=[StructuredTrack.model_validate(track) for track in payload.get("tracks", [])],
            notes=notes,
            raw_payload=payload,
        )


def parse_vcpedia_search_html(html: str) -> list[SearchCandidate]:
    root = parse_html(html)
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()
    search_rows = root.css(".mw-search-result")
    if search_rows:
        for row in search_rows:
            anchor = row.css(".mw-search-result-heading a")
            if not anchor:
                continue
            candidates.extend(_candidate_from_anchor(anchor[0], row_text=node_text(row), seen=seen))
        return candidates

    heading_anchors = root.css(".mw-search-result-heading a")
    if heading_anchors:
        for anchor in heading_anchors:
            candidates.extend(_candidate_from_anchor(anchor, row_text=node_text(anchor), seen=seen))
        return candidates

    for anchor in root.css("#mw-content-text li a[href]"):
        candidates.extend(_candidate_from_anchor(anchor, row_text=node_text(anchor), seen=seen))
    return candidates


def parse_vcpedia_detail_html(html: str, url: str) -> SourceEvidence:
    root = parse_html(html)
    title = _pick_vcpedia_title(root)
    infobox = _extract_infobox(root)
    categories = _extract_categories(root)
    clean_text = document_text(root)
    global_track_artist = _extract_global_track_artist(infobox, clean_text)
    tracks = _apply_global_track_artist(_extract_tracks(root), global_track_artist)
    related_albums = _extract_related_albums(root)
    notes: list[str] = []
    is_album_page = any("音乐专辑" in category for category in categories) or "专辑" in (title or "")
    if related_albums:
        notes.append("Found linked album references from a non-tracklist page.")

    extracted_fields = {
        "title": title,
        "circle": _pick_field(infobox, ["制作", "制作方", "社团", "策划", "作者", "UP主", "发行方", "出品"]),
        "album_artist": _pick_field(infobox, ["演唱", "社团", "制作方", "发行方", "歌手"]),
        "catalog_no": _pick_field(infobox, ["编号", "专辑编号", "品番"]),
        "release_date": _normalize_release_date(_pick_field(infobox, ["发行时间", "发售日期", "发行日期", "投稿时间", "发布日期"])),
        "event_name": _pick_field(infobox, ["活动", "收录活动"]),
        "cover_url": _pick_cover(root, url),
        "tags": categories,
        "tracks": [track.model_dump() for track in tracks],
        "global_track_artist": global_track_artist,
        "infobox": infobox,
        "is_album_page": is_album_page,
        "related_albums": related_albums,
        "notes": notes,
    }
    return SourceEvidence(
        source=SourceName.VCPEDIA,
        url=url,
        page_title=title or "VCPedia",
        cleaned_text=clean_text,
        extracted_snippets=[track.evidence for track in tracks if track.evidence],
        extracted_fields=extracted_fields,
        raw_html=html,
    )


def _candidate_from_anchor(anchor, row_text: str, seen: set[str]) -> list[SearchCandidate]:
    href = attr_value(anchor, "href")
    if not href or href in seen:
        return []
    if any(token in href for token in ("Special:", "Category:", "Template:", "File:", "User:")):
        return []
    title = node_text(anchor)
    if not title:
        return []
    seen.add(href)
    score_hint = 0.4
    if "专辑" in title or "音乐专辑" in row_text:
        score_hint = 0.85
    elif "收录专辑" in row_text:
        score_hint = 0.55
    return [
        SearchCandidate(
            source=SourceName.VCPEDIA,
            url=urljoin(VCPEDIA_BASE, href),
            title_hint=title,
            score_hint=score_hint,
            extra={"row_text": row_text},
        )
    ]


def _pick_vcpedia_title(root) -> str | None:
    heading = first_text(root, "#firstHeading")
    if heading:
        return heading.replace("(专辑)", "")
    title = first_text(root, "title")
    if title:
        return title.split(" - VCPedia.cn")[0].replace("(专辑)", "")
    return None


def _extract_infobox(root) -> dict[str, str]:
    infobox: dict[str, str] = {}
    table = root.css("table.infobox, table[class*='infobox']")
    if not table:
        return infobox
    current_key: str | None = None
    for row in table[0].css("tr"):
        headers = row.css("th")
        cells = row.css("td")
        if headers and cells:
            key = node_text(headers[0])
            value = node_text(cells[0])
            if key and value:
                infobox[key] = value
                current_key = key
        elif current_key and cells:
            value = node_text(cells[0])
            if value:
                infobox[current_key] = f"{infobox[current_key]} {value}".strip()
    return infobox


def _extract_categories(root) -> list[str]:
    return [text for text in all_texts(root, "#mw-normal-catlinks a") if text != "分类"]


def _pick_field(infobox: dict[str, str], keys: list[str]) -> str | None:
    for key, value in infobox.items():
        if any(term in key for term in keys):
            return value
    return None


def _extract_global_track_artist(infobox: dict[str, str], clean_text: str) -> str | None:
    explicit_keys = ("全碟演唱", "全曲演唱", "全碟vocal", "all vocals", "all tracks vocals")
    for key, value in infobox.items():
        if any(token in key.casefold() for token in explicit_keys):
            return _normalize_artist_name(value)
    match = re.search(r"全碟演唱[:：]\s*(?P<artist>[^\n]+)", clean_text, re.IGNORECASE)
    if match:
        return _normalize_artist_name(collapse_spaces(match.group("artist")))
    return None


def _apply_global_track_artist(tracks: list[StructuredTrack], artist: str | None) -> list[StructuredTrack]:
    if not artist:
        return tracks
    updated: list[StructuredTrack] = []
    for track in tracks:
        if track.artist:
            updated.append(track)
            continue
        updated.append(track.model_copy(update={"artist": artist}))
    return updated


def _normalize_release_date(value: str | None) -> str | None:
    if not value:
        return None
    match = DATE_RE.search(value)
    if not match:
        return value
    normalized = match.group(0).replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").replace(".", "-")
    parts = [part for part in normalized.split("-") if part]
    if len(parts) == 3:
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if len(parts) == 2:
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
    return parts[0]


def _pick_cover(root, url: str) -> str | None:
    infobox = root.css("table.infobox, table[class*='infobox']")
    if infobox:
        image = infobox[0].css("img[src]")
        if image:
            src = attr_value(image[0], "src")
            if src:
                return urljoin(url, src)
    image = root.css("img[src]")
    if image:
        src = attr_value(image[0], "src")
        if src:
            return urljoin(url, src)
    return None


def _extract_tracks(root) -> list[StructuredTrack]:
    for heading in root.css("h2, h3, h4"):
        title = node_text(heading)
        if not any(keyword in title for keyword in ("曲目", "专辑收录曲", "专辑曲目", "收录曲", "Track", "TRACK")):
            continue
        tracks = _parse_track_section(heading)
        if tracks:
            return tracks

    table_tracks = _parse_standalone_track_tables(root)
    if table_tracks:
        return table_tracks

    return _parse_track_lines(document_text(root))


def _parse_track_section(heading) -> list[StructuredTrack]:
    tracks: list[StructuredTrack] = []
    for sibling in heading.siblings:
        tag = getattr(sibling, "tag", None)
        if tag in {"h2", "h3", "h4"}:
            break
        if tag in {"ol", "ul"}:
            for idx, item in enumerate(sibling.css("li"), start=1):
                track = _track_from_text(node_text(item), idx)
                if track:
                    tracks.append(track)
        elif tag == "table":
            tracks.extend(_parse_track_table(sibling))
        elif tag in {"p", "div"}:
            tracks.extend(_parse_bullet_text(node_text(sibling)))
    return _dedupe_tracks(tracks)


def _parse_standalone_track_tables(root) -> list[StructuredTrack]:
    tracks: list[StructuredTrack] = []
    for table in root.css("table"):
        table_tracks = _parse_track_table(table)
        if len(table_tracks) >= 2:
            tracks.extend(table_tracks)
    return _dedupe_tracks(tracks)


def _parse_track_table(table) -> list[StructuredTrack]:
    tracks: list[StructuredTrack] = []
    headers = [node_text(cell).casefold() for cell in table.css("tr th")]
    artist_column = _find_header_index(headers, ("artist", "vocal", "vocalist", "vocals", "歌手", "演唱", "艺人", "主唱", "歌唱"))
    for row in table.css("tr"):
        cells = row.css("th, td")
        if len(cells) < 2:
            continue
        number_text = node_text(cells[0])
        title_text = node_text(cells[1])
        if number_text.isdigit() and title_text:
            artist = None
            if artist_column is not None and artist_column < len(cells):
                artist = _normalize_artist_name(node_text(cells[artist_column]))
            tracks.append(StructuredTrack(number=int(number_text), title=title_text, artist=artist, evidence=f"{number_text} {title_text}"))
    return tracks


def _parse_bullet_text(text: str) -> list[StructuredTrack]:
    if "•" not in text:
        return []
    tracks: list[StructuredTrack] = []
    for idx, part in enumerate(text.split("•"), start=1):
        candidate = collapse_spaces(part)
        if not candidate:
            continue
        track = _track_from_text(candidate, idx)
        if track:
            tracks.append(track)
    return tracks


def _parse_track_lines(clean_text: str) -> list[StructuredTrack]:
    tracks: list[StructuredTrack] = []
    for line in clean_text.splitlines():
        text = collapse_spaces(line)
        track = _track_from_text(text, len(tracks) + 1)
        if track:
            tracks.append(track)
    return _dedupe_tracks(tracks)


def _track_from_text(text: str, fallback_number: int) -> StructuredTrack | None:
    match = TRACK_ARTIST_RE.match(text)
    if match:
        artist = _normalize_artist_name(collapse_spaces(match.group("artist")))
        return StructuredTrack(
            number=int(match.group("number")),
            title=collapse_spaces(match.group("title")),
            artist=artist,
            duration=match.group("duration"),
            evidence=text,
        )
    match = TRACK_LINE_RE.match(text)
    if match:
        return StructuredTrack(number=int(match.group("number")), title=collapse_spaces(match.group("title")), evidence=text)
    if text.startswith("《") and text.endswith("》"):
        return None
    if fallback_number >= 1 and re.match(r"^[^:]{1,120}$", text) and not any(token in text for token in ("Category:", "检索自", "VCPedia", "页面不存在")):
        return None
    return None


def _extract_related_albums(root) -> list[str]:
    related: list[str] = []
    for heading in root.css("h2, h3, h4"):
        title = node_text(heading)
        if "收录专辑" not in title:
            continue
        for sibling in heading.siblings:
            tag = getattr(sibling, "tag", None)
            if tag in {"h2", "h3", "h4"}:
                break
            for anchor in sibling.css("a[href]"):
                candidate = node_text(anchor).replace("(专辑)", "")
                if candidate and candidate not in related:
                    related.append(candidate)
    return related


def _dedupe_tracks(tracks: list[StructuredTrack]) -> list[StructuredTrack]:
    deduped: dict[int, StructuredTrack] = {}
    for track in tracks:
        existing = deduped.get(track.number)
        if existing is None and track.title:
            deduped[track.number] = track
            continue
        if existing is None:
            continue
        if existing.title != track.title:
            continue
        if not existing.artist and track.artist:
            deduped[track.number] = track
    return [deduped[number] for number in sorted(deduped)]


def _find_header_index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        if any(candidate in header for candidate in candidates):
            return index
    return None


def _normalize_artist_name(value: str | None) -> str | None:
    if not value:
        return None
    artist = collapse_spaces(value)
    if not artist:
        return None
    noise_tokens = ("track", "title", "曲目", "试听", "music", "lyric")
    if any(token == artist.casefold() for token in noise_tokens):
        return None
    return artist
