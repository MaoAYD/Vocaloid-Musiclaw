from __future__ import annotations

import hashlib
import json
import re

import httpx

from musiclaw.config import AppConfig
from musiclaw.llm.validators import parse_structurer_json
from musiclaw.models import FieldEvidence, SourceEvidence, SourceName, StructuredAlbumPage, StructuredTrack
from musiclaw.utils.cache import JsonCache
from musiclaw.utils.textnorm import collapse_spaces


class PageStructurerAgent:
    def __init__(self, config: AppConfig, cache: JsonCache) -> None:
        self.config = config
        self.cache = cache

    def structure(self, evidence: SourceEvidence) -> StructuredAlbumPage:
        cache_key = self._cache_key(evidence)
        cached = self.cache.load("llm", cache_key)
        if cached:
            return StructuredAlbumPage.model_validate(cached)

        structured = self._heuristic_structure(evidence)
        llm_result = self._llm_structure(evidence)
        if llm_result is not None:
            structured = self._merge_structured_pages(structured, llm_result)

        self.cache.store("llm", cache_key, structured.model_dump(mode="json"))
        return structured

    @staticmethod
    def _cache_key(evidence: SourceEvidence) -> str:
        signature = json.dumps(
            {
                "source": evidence.source.value,
                "url": evidence.url,
                "page_title": evidence.page_title,
                "cleaned_text": evidence.cleaned_text,
                "extracted_fields": evidence.extracted_fields,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()
        return f"page-structurer:v3:{evidence.url}:{digest}"

    @staticmethod
    def _merge_structured_pages(base: StructuredAlbumPage, override: StructuredAlbumPage) -> StructuredAlbumPage:
        tracks_by_number: dict[int, StructuredTrack] = {track.number: track for track in base.tracks}
        for track in override.tracks:
            existing = tracks_by_number.get(track.number)
            if existing is None:
                tracks_by_number[track.number] = track
                continue
            tracks_by_number[track.number] = existing.model_copy(
                update={
                    "title": track.title or existing.title,
                    "artist": track.artist or existing.artist,
                    "composer": track.composer or existing.composer,
                    "duration": track.duration or existing.duration,
                    "evidence": track.evidence or existing.evidence,
                    "source_url": track.source_url or existing.source_url,
                }
            )

        merged_tags = list(dict.fromkeys([*base.tags, *override.tags]))
        merged_notes = list(dict.fromkeys([*base.notes, *override.notes]))
        return StructuredAlbumPage(
            source=base.source,
            url=base.url,
            title=override.title or base.title,
            circle=override.circle or base.circle,
            album_artist=override.album_artist or base.album_artist,
            catalog_no=override.catalog_no or base.catalog_no,
            release_date=override.release_date or base.release_date,
            event_name=override.event_name or base.event_name,
            cover_url=override.cover_url or base.cover_url,
            tags=merged_tags,
            tracks=[tracks_by_number[number] for number in sorted(tracks_by_number)],
            notes=merged_notes,
            raw_payload=override.raw_payload or base.raw_payload,
        )

    def _heuristic_structure(self, evidence: SourceEvidence) -> StructuredAlbumPage:
        payload = dict(evidence.extracted_fields)
        if evidence.source == SourceName.MANUAL or payload.get("manual_input"):
            payload = self._merge_manual_payload(payload, self._parse_manual_text(evidence.cleaned_text or str(payload.get("manual_text") or "")))
        tracks = []
        for track in payload.get("tracks", []):
            try:
                tracks.append(StructuredTrack.model_validate(track))
            except Exception:
                continue
        return StructuredAlbumPage(
            source=evidence.source,
            url=evidence.url,
            title=self._field_evidence(payload.get("title"), evidence.url, confidence=0.7),
            circle=self._field_evidence(payload.get("circle"), evidence.url, confidence=0.7),
            album_artist=self._field_evidence(payload.get("album_artist") or payload.get("circle"), evidence.url, confidence=0.6),
            catalog_no=self._field_evidence(payload.get("catalog_no"), evidence.url, confidence=0.6),
            release_date=self._field_evidence(payload.get("release_date"), evidence.url, confidence=0.6),
            event_name=self._field_evidence(payload.get("event_name"), evidence.url, confidence=0.5),
            cover_url=self._field_evidence(payload.get("cover_url"), evidence.url, confidence=0.7),
            tags=list(payload.get("tags", [])),
            tracks=tracks,
            notes=list(payload.get("notes", [])),
            raw_payload=payload,
        )

    @staticmethod
    def _merge_manual_payload(base: dict, parsed: dict) -> dict:
        merged = dict(base)
        for key, value in parsed.items():
            if key == "tracks":
                if value:
                    merged[key] = value
                continue
            if key == "notes":
                merged[key] = list(dict.fromkeys([*(merged.get("notes") or []), *value]))
                continue
            if value and not merged.get(key):
                merged[key] = value
        return merged

    @staticmethod
    def _parse_manual_text(text: str) -> dict[str, object]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        tracks: list[dict[str, object]] = []
        notes: list[str] = []
        payload: dict[str, object] = {"tracks": tracks, "notes": notes}
        field_aliases = {
            "title": {"title", "album", "album title", "albumname", "专辑", "专辑名", "标题", "碟名"},
            "circle": {"circle", "group", "label", "brand", "社团", "团体", "制作", "制作方", "厂牌", "社团名"},
            "album_artist": {"album artist", "artist", "vocal", "vocalist", "vocals", "歌手", "演唱", "艺术家", "主唱", "歌姬", "演唱者"},
            "catalog_no": {"catalog", "catalog no", "catalogue", "catalog number", "catno", "编号", "品番", "编号/品番", "品番号"},
            "release_date": {"release", "release date", "date", "发行日期", "发售日", "発売日", "日期", "时间"},
            "event_name": {"event", "event name", "活动", "活动名", "展会", "会场", "首发", "首发活动"},
            "cover_url": {"cover", "cover url", "cover image", "封面", "封面图", "封面链接"},
        }
        global_track_artist: str | None = None
        global_track_composer: str | None = None
        for line in lines:
            global_track_artist = global_track_artist or PageStructurerAgent._parse_manual_global_track_artist(line)
            global_track_composer = global_track_composer or PageStructurerAgent._parse_manual_global_track_composer(line)
            if PageStructurerAgent._is_manual_section_header(line):
                continue
            field = PageStructurerAgent._parse_manual_field_line(line, field_aliases)
            if not field:
                field = PageStructurerAgent._parse_manual_inline_field_line(line, field_aliases)
            if field:
                key, value = field
                payload[key] = value
                continue
            note = PageStructurerAgent._parse_manual_staff_note(line)
            if note:
                notes.append(note)
                continue
            track = PageStructurerAgent._parse_manual_track_line(line)
            if track:
                tracks.append(track)
        if global_track_artist:
            payload["album_artist"] = payload.get("album_artist") or global_track_artist
            for track in tracks:
                if not track.get("artist"):
                    track["artist"] = global_track_artist
        if global_track_composer:
            for track in tracks:
                if not track.get("composer"):
                    track["composer"] = global_track_composer
        if tracks:
            payload["tracks"] = tracks
        return payload

    @staticmethod
    def _parse_manual_global_track_artist(line: str) -> str | None:
        match = re.match(
            r"^(?P<label>全碟演唱|全曲演唱|全碟歌手|全曲歌手|演唱|歌手|主唱|歌姬|演唱者|vocal(?:ist)?s?|vocals?)\s*(?:[:：=]|->|=>|-)?\s*(?P<value>.+)$",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None
        return collapse_spaces(match.group("value")) or None

    @staticmethod
    def _parse_manual_global_track_composer(line: str) -> str | None:
        match = re.match(
            r"^(?P<label>全碟作曲|全曲作曲|作曲|composer(?:s)?)\s*(?:[:：=]|->|=>|-)?\s*(?P<value>.+)$",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None
        return collapse_spaces(match.group("value")) or None

    @staticmethod
    def _is_manual_section_header(line: str) -> bool:
        normalized = collapse_spaces(line).strip(" :：")
        return normalized.casefold() in {
            "包含曲目",
            "曲目",
            "曲目列表",
            "收录曲目",
            "tracklist",
            "track list",
            "tracks",
            "songs",
            "song list",
            "included tracks",
            "包含歌曲",
        }

    @staticmethod
    def _parse_manual_field_line(line: str, field_aliases: dict[str, set[str]]) -> tuple[str, str] | None:
        match = re.match(r"^(?P<label>[^:：=]+)\s*(?:[:：=]|->|=>)\s*(?P<value>.+)$", line)
        if not match:
            return None
        label = collapse_spaces(match.group("label")).casefold()
        value = collapse_spaces(match.group("value"))
        if not value:
            return None
        for key, aliases in field_aliases.items():
            if label in aliases:
                return key, value
        return None

    @staticmethod
    def _parse_manual_inline_field_line(line: str, field_aliases: dict[str, set[str]]) -> tuple[str, str] | None:
        normalized = collapse_spaces(line)
        for key, aliases in field_aliases.items():
            for alias in aliases:
                patterns = [
                    rf"^{re.escape(alias)}\s+(?P<value>.+)$",
                    rf"^{re.escape(alias)}\s*[-/|]\s*(?P<value>.+)$",
                ]
                for pattern in patterns:
                    match = re.match(pattern, normalized, re.IGNORECASE)
                    if match:
                        value = collapse_spaces(match.group("value"))
                        if value:
                            return key, value
        return None

    @staticmethod
    def _parse_manual_staff_note(line: str) -> str | None:
        match = re.match(
            r"^(?P<label>作曲|编曲|作词|调校|混音|母带|吉他|贝斯|钢琴|鼓|和声|PV|MV|视频|曲绘|封面|设计|插画|illust(?:ration)?|movie|mix|master(?:ing)?|arrange(?:r)?|lyric(?:ist)?|tuning)\s*(?:[:：=]|->|=>|-)?\s*(?P<value>.+)$",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None
        label = collapse_spaces(match.group("label"))
        value = collapse_spaces(match.group("value"))
        return f"{label}: {value}" if value else None

    @staticmethod
    def _parse_manual_track_line(line: str) -> dict[str, object] | None:
        match = re.match(
            r"^(?:(?:disc|cd)\s*(?P<disc>\d+)\s*[-/: ]+)?(?P<number>(?:track|tr|m)\s*[- ]*\d{1,2}|\d{1,2}|[Mm]\s*-?\s*\d{1,2})(?:\s*(?:[.\-_)_:：、]|\)))?\s*(?P<rest>.+)$",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None
        number_token = match.group("number")
        number_match = re.search(r"\d+", number_token)
        if not number_match:
            return None
        number = int(number_match.group(0))
        rest = collapse_spaces(match.group("rest"))
        if not rest:
            return None
        title, metadata = PageStructurerAgent._split_manual_track_rest(rest)
        if not title:
            return None
        return {
            "number": number,
            "title": title,
            "artist": metadata.get("artist"),
            "composer": metadata.get("composer"),
            "source_url": None,
            "evidence": line,
        }

    @staticmethod
    def _split_manual_track_rest(rest: str) -> tuple[str | None, dict[str, str | None]]:
        metadata: dict[str, str | None] = {"artist": None, "composer": None}
        working = collapse_spaces(rest)
        bracket_segments = re.findall(r"[\(\[（【](.*?)[\)\]）】]", working)
        for segment in bracket_segments:
            PageStructurerAgent._apply_manual_track_segment_metadata(segment, metadata)
        working = re.sub(r"[\(\[（【].*?[\)\]）】]", "", working).strip()

        segments = [segment.strip() for segment in re.split(r"\s*[|｜;；]\s*", working) if segment.strip()]
        title = segments[0] if segments else working
        extra_segments = segments[1:] if segments else []
        for segment in extra_segments:
            PageStructurerAgent._apply_manual_track_segment_metadata(segment, metadata)

        title, inline_artist = PageStructurerAgent._parse_unlabeled_track_suffix(title)
        if inline_artist and not metadata.get("artist"):
            metadata["artist"] = inline_artist
        return collapse_spaces(title) or None, metadata

    @staticmethod
    def _apply_manual_track_segment_metadata(segment: str, metadata: dict[str, str | None]) -> None:
        normalized = collapse_spaces(segment)
        match = re.match(r"^(?P<label>[^:：=/]+)\s*(?:[:：=/]|->|=>|-)\s*(?P<value>.+)$", normalized)
        if match:
            label = match.group("label").strip().casefold()
            value = collapse_spaces(match.group("value"))
            if not value:
                return
            if label in {"vocal", "vocals", "vocalist", "vocalists", "artist", "歌手", "演唱", "主唱", "歌姬", "feat", "featuring"}:
                metadata["artist"] = metadata.get("artist") or value
                return
            if label in {"composer", "compose", "作曲"}:
                metadata["composer"] = metadata.get("composer") or value
                return
            return
        lower_segment = normalized.casefold()
        if lower_segment.startswith(("feat. ", "ft. ", "featuring ")):
            metadata["artist"] = metadata.get("artist") or collapse_spaces(normalized.split(" ", 1)[1])

    @staticmethod
    def _parse_unlabeled_track_suffix(title: str) -> tuple[str, str | None]:
        lowered = title.casefold()
        for separator in [" feat. ", " ft. ", " featuring ", " / ", " - "]:
            index = lowered.find(separator.casefold())
            if index <= 0:
                continue
            left = collapse_spaces(title[:index])
            right = collapse_spaces(title[index + len(separator):])
            if left and right:
                return left, right
        return title, None

    @staticmethod
    def _field_evidence(value: object, source_url: str, *, confidence: float) -> FieldEvidence | None:
        text = PageStructurerAgent._stringify_value(value)
        if not text:
            return None
        return FieldEvidence(value=text, evidence=text, source_url=source_url, confidence=confidence)

    @staticmethod
    def _stringify_value(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            year = value.get("year")
            month = value.get("month")
            day = value.get("day")
            if year and month and day:
                return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            if year and month:
                return f"{int(year):04d}-{int(month):02d}"
            if year:
                return str(year)
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, (list, tuple, set)):
            items = [PageStructurerAgent._stringify_value(item) for item in value]
            return ", ".join(item for item in items if item) or None
        return str(value).strip() or None

    def _llm_structure(self, evidence: SourceEvidence) -> StructuredAlbumPage | None:
        if not self.config.llm.enabled or not self.config.llm.api_key or not self.config.llm.model or not self.config.llm.resolved_base_url:
            return None
        prompt = self._build_prompt(evidence)
        client = httpx.Client(timeout=self.config.network.timeout_seconds)
        try:
            response = client.post(
                f"{self.config.llm.resolved_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.llm.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.llm.model,
                    "temperature": self.config.llm.temperature,
                    "messages": [
                        {"role": "system", "content": "You are a careful metadata extraction engine that only outputs JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = parse_structurer_json(content)
            return StructuredAlbumPage.model_validate({"source": evidence.source, "url": evidence.url, **parsed.model_dump()})
        except Exception:
            return None
        finally:
            client.close()

    @staticmethod
    def _build_prompt(evidence: SourceEvidence) -> str:
        prompt_context: list[str] = []
        if evidence.extracted_fields.get("priority_url_source"):
            prompt_context.append(
                "This evidence came from a user-supplied priority URL. Prefer extracting useful metadata directly from the fetched page content when possible."
            )
        if evidence.extracted_fields.get("manual_input"):
            prompt_context.append(
                "This evidence is user-supplied raw text and should be treated as primary evidence when it explicitly states metadata. For fuzzy, shorthand, or partially structured manual notes, try to normalize them into the most likely structured fields and track entries you can justify from the text. Support common handwritten styles such as '全碟演唱: 星尘Infinity', '包含曲目:', '1. 曲名', '01) 曲名', '1、曲名', 'Tr1 曲名', 'Track 2 曲名', 'M-1 曲名 / Vocal', '歌手 Singer A', '专辑 Foo', '作曲: Composer', and similar shorthand tracklists or staff notes. Prefer conservative transformations over invention, keep uncertain inferences lower-confidence, and use notes to explain ambiguity."
            )
        return (
            "Extract album metadata from the provided evidence. "
            "Return JSON only with keys title, circle, album_artist, catalog_no, release_date, event_name, cover_url, tags, tracks, notes. "
            "Each scalar field must be an object with value, evidence, source_url, confidence. "
            "Do not infer missing tracks or fill blanks without evidence. "
            "If extracted_fields includes VocaDB track CSV data, reconcile it with the page/API data and prefer explicit vocalist columns over producer/composer fields when filling track artists. "
            "For track artists, only include an artist when the same page explicitly associates that artist with that track, or when the page explicitly states a global rule such as 全碟演唱 that applies to all tracks on the page.\n\n"
            f"Context: {' '.join(prompt_context) or 'No extra context.'}\n"
            f"Source: {evidence.source.value}\nURL: {evidence.url}\nTitle: {evidence.page_title}\n"
            f"Extracted fields: {json.dumps(evidence.extracted_fields, ensure_ascii=False)}\n"
            f"Cleaned text:\n{evidence.cleaned_text[:12000]}"
        )
