from __future__ import annotations

import hashlib
import json
from collections import Counter

import httpx

from musiclaw.config import AppConfig
from musiclaw.llm.validators import parse_resolver_json
from musiclaw.models import AlbumCandidate, LocalAlbum, SourceName, StructuredAlbumPage, TrackCandidate
from musiclaw.utils.cache import JsonCache


class AlbumResolverAgent:
    def __init__(self, config: AppConfig, cache: JsonCache) -> None:
        self.config = config
        self.cache = cache

    def resolve(self, album: LocalAlbum, pages: list[StructuredAlbumPage]) -> AlbumCandidate:
        cache_key = self._cache_key(album, pages)
        cached = self.cache.load("llm", cache_key)
        if cached:
            return AlbumCandidate.model_validate(cached)

        resolved = self._heuristic_resolve(pages)
        llm_result = self._llm_resolve(album, pages)
        if llm_result is not None:
            resolved = self._merge_candidates(resolved, llm_result)
        self.cache.store("llm", cache_key, resolved.model_dump(mode="json"))
        return resolved

    @staticmethod
    def _cache_key(album: LocalAlbum, pages: list[StructuredAlbumPage]) -> str:
        signature = json.dumps([page.model_dump(mode="json") for page in pages], ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()
        return f"album-resolver:v3:{album.folder_path}:{digest}"

    @staticmethod
    def _merge_candidates(base: AlbumCandidate, override: AlbumCandidate) -> AlbumCandidate:
        tracks_by_number: dict[int, TrackCandidate] = {track.number: track for track in base.tracks}
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
                    "evidence_url": track.evidence_url or existing.evidence_url,
                }
            )

        return AlbumCandidate(
            source_priority=override.source_priority or base.source_priority,
            title=override.title or base.title,
            circle=override.circle or base.circle,
            album_artist=override.album_artist or base.album_artist,
            catalog_no=override.catalog_no or base.catalog_no,
            release_date=override.release_date or base.release_date,
            event_name=override.event_name or base.event_name,
            cover_url=override.cover_url or base.cover_url,
            tags=sorted(set(base.tags) | set(override.tags)),
            tracks=[tracks_by_number[number] for number in sorted(tracks_by_number)],
            evidence_urls=list(dict.fromkeys([*base.evidence_urls, *override.evidence_urls])),
            conflicts=list(dict.fromkeys([*base.conflicts, *override.conflicts])),
            confidence=max(base.confidence, override.confidence),
        )

    def _heuristic_resolve(self, pages: list[StructuredAlbumPage]) -> AlbumCandidate:
        def most_common(values: list[str | None]) -> str | None:
            counted = Counter(value for value in values if value)
            return counted.most_common(1)[0][0] if counted else None

        def prefer_manual(field_name: str) -> str | None:
            for page in pages:
                if page.source != SourceName.MANUAL:
                    continue
                field = getattr(page, field_name)
                if field and field.value:
                    return field.value
            values = [getattr(page, field_name).value for page in pages if getattr(page, field_name)]
            return most_common(values)

        def is_manual_page(page: StructuredAlbumPage) -> bool:
            return page.source == SourceName.MANUAL

        source_priority = [page.source for page in pages]
        title = prefer_manual("title")
        circle = prefer_manual("circle")
        album_artist = prefer_manual("album_artist") or circle
        catalog_no = prefer_manual("catalog_no")
        release_date = prefer_manual("release_date")
        event_name = prefer_manual("event_name")
        cover_url = prefer_manual("cover_url")

        tracks_by_number: dict[int, TrackCandidate] = {}
        tags: set[str] = set()
        conflicts: list[str] = []
        for page in pages:
            tags.update(page.tags)
            for track in page.tracks:
                existing = tracks_by_number.get(track.number)
                if existing and existing.title != track.title:
                    if is_manual_page(page) and track.title:
                        conflicts.append(f"Track {track.number} title conflict: '{existing.title}' vs '{track.title}' (manual preferred)")
                        tracks_by_number[track.number] = existing.model_copy(
                            update={
                                "title": track.title,
                                "artist": track.artist or existing.artist,
                                "composer": track.composer or existing.composer,
                                "duration": track.duration or existing.duration,
                                "evidence_url": track.source_url or page.url or existing.evidence_url,
                            }
                        )
                        continue
                    conflicts.append(f"Track {track.number} title conflict: '{existing.title}' vs '{track.title}'")
                    continue
                if existing:
                    prefer_manual_track = is_manual_page(page)
                    merged_artist = track.artist if prefer_manual_track and track.artist else (existing.artist or track.artist)
                    if existing.artist and track.artist and existing.artist != track.artist:
                        suffix = " (manual preferred)" if prefer_manual_track else ""
                        conflicts.append(f"Track {track.number} artist conflict: '{existing.artist}' vs '{track.artist}'{suffix}")
                    merged_composer = track.composer if prefer_manual_track and track.composer else (existing.composer or track.composer)
                    if existing.composer and track.composer and existing.composer != track.composer:
                        suffix = " (manual preferred)" if prefer_manual_track else ""
                        conflicts.append(f"Track {track.number} composer conflict: '{existing.composer}' vs '{track.composer}'{suffix}")
                    merged_duration = track.duration if prefer_manual_track and track.duration else (existing.duration or track.duration)
                    tracks_by_number[track.number] = TrackCandidate(
                        number=track.number,
                        title=track.title if prefer_manual_track and track.title else existing.title,
                        artist=merged_artist,
                        composer=merged_composer,
                        duration=merged_duration,
                        evidence_url=(track.source_url or page.url) if prefer_manual_track else (existing.evidence_url or track.source_url or page.url),
                    )
                    continue
                tracks_by_number[track.number] = TrackCandidate(
                    number=track.number,
                    title=track.title,
                    artist=track.artist,
                    composer=track.composer,
                    duration=track.duration,
                    evidence_url=track.source_url or page.url,
                )

        confidence = 0.5
        if len(pages) > 1:
            confidence += 0.1
        if title:
            confidence += 0.1
        if tracks_by_number:
            confidence += 0.2
        if conflicts:
            confidence -= min(0.2, len(conflicts) * 0.05)

        return AlbumCandidate(
            source_priority=source_priority,
            title=title,
            circle=circle,
            album_artist=album_artist,
            catalog_no=catalog_no,
            release_date=release_date,
            event_name=event_name,
            cover_url=cover_url,
            tags=sorted(tags),
            tracks=[tracks_by_number[number] for number in sorted(tracks_by_number)],
            evidence_urls=[page.url for page in pages],
            conflicts=conflicts,
            confidence=max(0.0, min(1.0, round(confidence, 3))),
        )

    def _llm_resolve(self, album: LocalAlbum, pages: list[StructuredAlbumPage]) -> AlbumCandidate | None:
        if not self.config.llm.enabled or not self.config.llm.api_key or not self.config.llm.model or not self.config.llm.resolved_base_url:
            return None
        prompt = (
            "Merge evidence pages into one album candidate. Return JSON only with keys title, circle, album_artist, catalog_no, release_date, event_name, cover_url, tags, tracks, conflicts, confidence. "
            "Do not invent missing fields. User-supplied manual raw text is primary evidence when it explicitly states metadata, so prefer it over conflicting site data. Preserve explicit per-track artists from same-page evidence, and do not replace a known track artist with null from another source. If one page explicitly states a global vocal rule such as 全碟演唱, that can apply to tracks from that same page only.\n\n"
            f"Local album: {album.model_dump(mode='json')}\n"
            f"Pages: {json.dumps([page.model_dump(mode='json') for page in pages], ensure_ascii=False)}"
        )
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
                        {"role": "system", "content": "You are a careful metadata resolver that only outputs JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = parse_resolver_json(content)
            data = parsed.model_dump()
            data["source_priority"] = [page.source for page in pages]
            return AlbumCandidate.model_validate(data)
        except Exception:
            return None
        finally:
            client.close()
