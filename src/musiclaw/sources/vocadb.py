from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin

from musiclaw.models import FieldEvidence, SearchCandidate, SearchQuery, SourceEvidence, SourceName, StructuredAlbumPage, StructuredTrack
from musiclaw.sources.base import SourceAdapter
from musiclaw.utils.textnorm import collapse_spaces


VOCADB_BASE = "https://vocadb.net"
CF_TITLE = "Just a moment..."
VOCADB_CSV_TEMP_DIR = Path("temp") / "vocadb_csv"


def vocadb_csv_cache_dir() -> Path:
    VOCADB_CSV_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return VOCADB_CSV_TEMP_DIR


def clear_vocadb_csv_cache() -> int:
    cache_dir = VOCADB_CSV_TEMP_DIR
    if not cache_dir.exists():
        return 0
    deleted = 0
    for path in cache_dir.glob("*"):
        if not path.is_file():
            continue
        path.unlink()
        deleted += 1
    return deleted


class VocaDbAdapter(SourceAdapter):
    source_name = SourceName.VOCADB.value
    enum_name = SourceName.VOCADB
    url_hosts = ("vocadb.net",)

    def search(self, query: SearchQuery) -> list[SearchCandidate]:
        cache_key = f"search:{query.raw_query}"
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            return [SearchCandidate.model_validate(item) for item in cached]

        candidates = self._search_api(query)
        if not candidates:
            candidates = self._search_html(query)
        limited = candidates[: self.config.sources.max_candidates]
        self.cache.store(self.source_name, cache_key, [candidate.model_dump(mode="json") for candidate in limited])
        return limited

    def fetch_detail(self, candidate: SearchCandidate) -> SourceEvidence:
        cache_key = f"detail:v4:{candidate.url}"
        album_id = self._album_id_from_candidate(candidate)
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            evidence = SourceEvidence.model_validate(cached)
            if album_id and self._ensure_cached_track_csv(album_id, evidence):
                self.cache.store(self.source_name, cache_key, evidence.model_dump(mode="json"))
            return evidence

        if album_id:
            api_url = f"{VOCADB_BASE}/api/albums/{album_id}?fields=MainPicture,Names,Artists,Tracks,Tags&songFields=Artists"
            try:
                payload = self.client.fetch_json(api_url, stealth=True)
                csv_bundle = self._resolve_track_csv_bundle(album_id, candidate.url)
                csv_tracks: list[dict[str, object]] = []
                raw_csv_tracks = csv_bundle.get("tracks")
                if isinstance(raw_csv_tracks, list):
                    csv_tracks = [track for track in raw_csv_tracks if isinstance(track, dict)]
                extracted_tracks = self._merge_csv_tracks(
                    self._extract_track_payloads(candidate.url, payload),
                    csv_tracks,
                )
                album_artists = payload.get("artists", [])
                circle_name = self._pick_circle_artist(album_artists)
                album_artist = self._pick_vocalists(album_artists) or self._pick_track_artist_fallback(extracted_tracks) or circle_name
                cleaned_text = json.dumps(payload, ensure_ascii=False)
                if csv_bundle.get("text"):
                    cleaned_text = f"{cleaned_text}\n\n[VocaDB track CSV]\n{csv_bundle['text']}"
                evidence = SourceEvidence(
                    source=SourceName.VOCADB,
                    url=candidate.url,
                    page_title=str(payload.get("name") or candidate.title_hint or "VocaDB album"),
                    cleaned_text=cleaned_text,
                    extracted_fields={
                        "title": payload.get("name"),
                        "circle": circle_name,
                        "album_artist": album_artist,
                        "catalog_no": payload.get("catalogNumber"),
                        "release_date": payload.get("releaseDate"),
                        "cover_url": ((payload.get("mainPicture") or {}).get("urlSmallThumb") if payload.get("mainPicture") else None),
                        "tags": [tag.get("tag", {}).get("name") for tag in payload.get("tags", []) if tag.get("tag", {}).get("name")],
                        "tracks": extracted_tracks,
                        "track_csv_path": csv_bundle.get("path"),
                        "track_csv_url": csv_bundle.get("url"),
                        "track_csv_text": csv_bundle.get("text"),
                        "track_csv_rows": csv_bundle.get("rows", []),
                        "raw_payload": payload,
                    },
                )
                self.cache.store(self.source_name, cache_key, evidence.model_dump(mode="json"))
                return evidence
            except Exception:
                pass

        response = self.client.fetch_html(candidate.url, stealth=True)
        evidence = SourceEvidence(
            source=SourceName.VOCADB,
            url=response.url,
            page_title=_page_title(response.text),
            cleaned_text=response.text,
            extracted_fields={"html_fallback": True},
            raw_html=response.text,
        )
        self.cache.store(self.source_name, cache_key, evidence.model_dump(mode="json"))
        return evidence

    @staticmethod
    def _album_id_from_candidate(candidate: SearchCandidate) -> int | None:
        album_id = candidate.extra.get("album_id")
        if album_id:
            return int(album_id)
        match = re.search(r"/Al/(\d+)", candidate.url)
        return int(match.group(1)) if match else None

    def _ensure_cached_track_csv(self, album_id: int, evidence: SourceEvidence) -> bool:
        csv_text = evidence.extracted_fields.get("track_csv_text")
        csv_path = evidence.extracted_fields.get("track_csv_path")
        if not isinstance(csv_text, str) or not csv_text.strip():
            return False
        if isinstance(csv_path, str) and csv_path and Path(csv_path).exists():
            return False
        path = self._write_track_csv(album_id, csv_text)
        evidence.extracted_fields["track_csv_path"] = str(path)
        return True

    def _resolve_track_csv_bundle(self, album_id: int, album_url: str) -> dict[str, object]:
        csv_url = self._discover_track_csv_url(album_url)
        csv_text = self._download_track_csv_text(csv_url) if csv_url else None
        bundle_source_url = csv_url
        if not csv_text:
            tracks_payload = self._fetch_album_tracks_payload(album_id)
            if tracks_payload:
                csv_text = self._build_track_csv_text(album_url, tracks_payload)
                bundle_source_url = bundle_source_url or f"{VOCADB_BASE}/api/albums/{album_id}/tracks?fields=Artists"
        if not csv_text:
            return {}
        path = self._write_track_csv(album_id, csv_text)
        rows = self._parse_track_csv_rows(csv_text)
        return {
            "path": str(path),
            "url": bundle_source_url,
            "text": csv_text,
            "rows": rows,
            "tracks": self._parse_csv_track_rows(album_url, rows),
        }

    def _discover_track_csv_url(self, album_url: str) -> str | None:
        try:
            response = self.client.fetch_html(album_url, stealth=True)
        except Exception:
            return None
        patterns = (
            r'href="(?P<url>[^"]+csv[^"]*)"[^>]*>\s*Download track info as CSV',
            r'Download track info as CSV[^\n]{0,400}?href="(?P<url>[^"]+)"',
        )
        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE | re.DOTALL)
            if match:
                candidate_url = urljoin(response.url, match.group("url"))
                if "csv" not in candidate_url.casefold():
                    continue
                if candidate_url.rstrip("/") == str(response.url).rstrip("/"):
                    continue
                return candidate_url
        return None

    def _download_track_csv_text(self, csv_url: str) -> str | None:
        try:
            payload = self.client.download_bytes(csv_url)
        except Exception:
            return None
        text = payload.decode("utf-8-sig", errors="ignore").strip()
        return text or None

    def _fetch_album_tracks_payload(self, album_id: int) -> list[dict[str, object]]:
        cache_key = f"album:v4:{album_id}:tracks"
        cached = self.cache.load(self.source_name, cache_key)
        if isinstance(cached, list):
            return [item for item in cached if isinstance(item, dict)]
        try:
            payload = self.client.fetch_json(f"{VOCADB_BASE}/api/albums/{album_id}/tracks?fields=Artists", stealth=False)
        except Exception:
            return []
        if isinstance(payload, list):
            tracks = [item for item in payload if isinstance(item, dict)]
            self.cache.store(self.source_name, cache_key, tracks)
            return tracks
        return []

    def _build_track_csv_text(self, album_url: str, tracks_payload: list[dict[str, object]]) -> str:
        output = io.StringIO()
        fieldnames = [
            "disc_number",
            "track_number",
            "title",
            "song_id",
            "song_url",
            "artist_string",
            "vocalists",
            "producers",
            "composers",
            "lyricists",
            "arrangers",
            "duration_seconds",
            "artist_details_json",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for item in tracks_payload:
            song = item.get("song") or {}
            if not isinstance(song, dict):
                song = {}
            artists_payload = song.get("artists", [])
            song_id = song.get("id")
            simplified_artists = []
            if isinstance(artists_payload, list):
                for artist in artists_payload:
                    if not isinstance(artist, dict):
                        continue
                    simplified_artists.append(
                        {
                            "name": artist.get("name") or (artist.get("artist") or {}).get("name"),
                            "categories": artist.get("categories"),
                            "roles": artist.get("roles"),
                            "effectiveRoles": artist.get("effectiveRoles"),
                        }
                    )
            writer.writerow(
                {
                    "disc_number": item.get("discNumber") or "",
                    "track_number": item.get("trackNumber") or item.get("discTrack") or "",
                    "title": song.get("name") or item.get("name") or "",
                    "song_id": song_id or "",
                    "song_url": f"{VOCADB_BASE}/S/{song_id}" if song_id else album_url,
                    "artist_string": song.get("artistString") or "",
                    "vocalists": self._pick_vocalists(artists_payload, song.get("artistString")) or "",
                    "producers": self._pick_artists_by_role(artists_payload, categories=("Producer",), role_terms=("Producer", "VoiceManipulator")) or "",
                    "composers": self._pick_artists_by_role(artists_payload, role_terms=("Composer",)) or "",
                    "lyricists": self._pick_artists_by_role(artists_payload, role_terms=("Lyricist",)) or "",
                    "arrangers": self._pick_artists_by_role(artists_payload, role_terms=("Arranger",)) or "",
                    "duration_seconds": song.get("lengthSeconds") or "",
                    "artist_details_json": json.dumps(simplified_artists, ensure_ascii=False),
                }
            )
        return output.getvalue()

    @staticmethod
    def _write_track_csv(album_id: int, csv_text: str) -> Path:
        path = vocadb_csv_cache_dir() / f"album_{album_id}.csv"
        path.write_text(csv_text, encoding="utf-8-sig")
        return path

    @staticmethod
    def _parse_track_csv_rows(csv_text: str) -> list[dict[str, str]]:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows: list[dict[str, str]] = []
        for row in reader:
            normalized: dict[str, str] = {}
            for key, value in row.items():
                normalized_key = str(key or "").strip()
                if isinstance(value, list):
                    normalized_value = ", ".join(str(item).strip() for item in value if str(item).strip())
                else:
                    normalized_value = str(value or "").strip()
                normalized[normalized_key] = normalized_value
            rows.append(normalized)
        return rows

    @staticmethod
    def _parse_csv_track_rows(album_url: str, rows: list[dict[str, str]]) -> list[dict[str, object]]:
        tracks: list[dict[str, object]] = []
        for row in rows:
            number = VocaDbAdapter._parse_csv_int(row, ("track_number", "track number", "track", "#"))
            title = VocaDbAdapter._first_csv_value(row, ("title", "track_title", "track title", "name", "song")) or ""
            if not number or not title:
                continue
            artist = VocaDbAdapter._first_csv_value(row, ("vocalists", "vocalist", "singers", "singer", "vocals"))
            source_url = VocaDbAdapter._first_csv_value(row, ("song_url", "song url", "url"))
            tracks.append(
                {
                    "number": number,
                    "title": title,
                    "artist": artist or None,
                    "source_url": source_url or album_url,
                }
            )
        return tracks

    @staticmethod
    def _merge_csv_tracks(existing_tracks: list[dict[str, object]], csv_tracks: list[dict[str, object]]) -> list[dict[str, object]]:
        if not csv_tracks:
            return existing_tracks
        csv_by_number = {
            number: track
            for track in csv_tracks
            if (number := VocaDbAdapter._coerce_int(track.get("number"))) is not None and number > 0
        }
        merged: list[dict[str, object]] = []
        for track in existing_tracks:
            number = VocaDbAdapter._coerce_int(track.get("number")) or 0
            csv_track = csv_by_number.get(number)
            if not csv_track:
                merged.append(track)
                continue
            merged.append(
                {
                    **track,
                    "artist": track.get("artist") or csv_track.get("artist"),
                    "source_url": track.get("source_url") or csv_track.get("source_url"),
                }
            )
        return merged

    @staticmethod
    def _first_csv_value(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
        normalized = {str(key).strip().casefold(): value for key, value in row.items()}
        for candidate in candidates:
            value = normalized.get(candidate.casefold())
            if value:
                return collapse_spaces(value)
        return None

    @staticmethod
    def _parse_csv_int(row: dict[str, str], candidates: tuple[str, ...]) -> int | None:
        value = VocaDbAdapter._first_csv_value(row, candidates)
        if not value:
            return None
        match = re.search(r"\d+", value)
        return int(match.group(0)) if match else None

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            return int(match.group(0)) if match else None
        return None

    def _extract_track_payloads(self, album_url: str, payload: dict) -> list[dict[str, object]]:
        extracted_tracks: list[dict[str, object]] = []
        unresolved_song_ids: dict[int, int] = {}
        for item in payload.get("tracks", []):
            if not isinstance(item, dict):
                continue
            song = item.get("song") or {}
            if not isinstance(song, dict):
                song = {}
            track_number = int(item.get("discTrack") or item.get("trackNumber") or len(extracted_tracks) + 1)
            song_id = song.get("id")
            track_payload = {
                "number": track_number,
                "title": song.get("name") or item.get("name") or "",
                "artist": self._pick_vocalists(song.get("artists", []), song.get("artistString")),
                "source_url": f"{VOCADB_BASE}/S/{song_id}" if song_id else album_url,
            }
            if not track_payload["artist"] and song_id:
                unresolved_song_ids[len(extracted_tracks)] = int(song_id)
            extracted_tracks.append(track_payload)

        for index, song_id in unresolved_song_ids.items():
            linked_song = self._fetch_song_artists(song_id)
            if not linked_song:
                continue
            artist = self._pick_vocalists(linked_song.get("artists", []), linked_song.get("artistString"))
            if artist:
                extracted_tracks[index]["artist"] = artist
                extracted_tracks[index]["source_url"] = f"{VOCADB_BASE}/S/{song_id}"
        return extracted_tracks

    def _fetch_song_artists(self, song_id: int) -> dict | None:
        cache_key = f"song:v2:{song_id}:artists"
        cached = self.cache.load(self.source_name, cache_key)
        if cached:
            return cached if isinstance(cached, dict) else None
        try:
            payload = self.client.fetch_json(f"{VOCADB_BASE}/api/songs/{song_id}?fields=Artists", stealth=True)
        except Exception:
            return None
        if isinstance(payload, dict):
            self.cache.store(self.source_name, cache_key, payload)
            return payload
        return None

    @staticmethod
    def _pick_vocalists(artists_payload: object, artist_string: object = None) -> str | None:
        if not isinstance(artists_payload, list):
            return VocaDbAdapter._extract_vocalists_from_artist_string(artist_string)
        vocalists: list[str] = []
        for entry in artists_payload:
            if not isinstance(entry, dict):
                continue
            categories = str(entry.get("categories") or "")
            if "Vocalist" not in categories:
                continue
            name = collapse_spaces(str(entry.get("name") or (entry.get("artist") or {}).get("name") or ""))
            if name and name not in vocalists:
                vocalists.append(name)
        if vocalists:
            return ", ".join(vocalists)
        return VocaDbAdapter._extract_vocalists_from_artist_string(artist_string)

    @staticmethod
    def _pick_circle_artist(artists_payload: object) -> str | None:
        if not isinstance(artists_payload, list):
            return None
        preferred_categories = ("Circle", "Label", "OtherGroup", "Band", "Group")
        fallback_names: list[str] = []
        for entry in artists_payload:
            if not isinstance(entry, dict):
                continue
            name = collapse_spaces(str(entry.get("name") or (entry.get("artist") or {}).get("name") or ""))
            if not name:
                continue
            if name not in fallback_names:
                fallback_names.append(name)
            categories = str(entry.get("categories") or "")
            if any(category in categories for category in preferred_categories):
                return name
        return fallback_names[0] if fallback_names else None

    @staticmethod
    def _pick_track_artist_fallback(tracks: list[dict[str, object]]) -> str | None:
        seen: list[str] = []
        for track in tracks:
            artist = collapse_spaces(str(track.get("artist") or ""))
            if artist and artist not in seen:
                seen.append(artist)
        return ", ".join(seen) if seen else None

    @staticmethod
    def _pick_artists_by_role(
        artists_payload: object,
        *,
        categories: tuple[str, ...] = (),
        role_terms: tuple[str, ...] = (),
    ) -> str | None:
        if not isinstance(artists_payload, list):
            return None
        names: list[str] = []
        for entry in artists_payload:
            if not isinstance(entry, dict):
                continue
            categories_value = str(entry.get("categories") or "")
            roles_value = f"{entry.get('roles') or ''} {entry.get('effectiveRoles') or ''}"
            if categories and not any(category in categories_value for category in categories):
                if role_terms and not any(term in roles_value for term in role_terms):
                    continue
            elif role_terms and not any(term in roles_value for term in role_terms) and not any(category in categories_value for category in categories):
                continue
            name = collapse_spaces(str(entry.get("name") or (entry.get("artist") or {}).get("name") or ""))
            if name and name not in names:
                names.append(name)
        return ", ".join(names) if names else None

    @staticmethod
    def _extract_vocalists_from_artist_string(artist_string: object) -> str | None:
        if not isinstance(artist_string, str):
            return None
        match = re.search(r"\b(?:feat\.?|ft\.?|featuring)\s+(?P<artist>.+)$", artist_string, re.IGNORECASE)
        if not match:
            return None
        artist = collapse_spaces(match.group("artist").strip(" -:;,."))
        return artist or None

    def normalize(self, evidence: SourceEvidence) -> StructuredAlbumPage:
        payload = evidence.extracted_fields
        if payload.get("html_fallback"):
            return StructuredAlbumPage(source=SourceName.VOCADB, url=evidence.url, notes=["VocaDB HTML fallback fetched, but structured parsing is unavailable."])

        if payload.get("title"):
            return StructuredAlbumPage(
                source=SourceName.VOCADB,
                url=evidence.url,
                title=FieldEvidence(value=payload.get("title"), evidence=payload.get("title"), source_url=evidence.url, confidence=0.9),
                circle=FieldEvidence(value=payload.get("circle"), evidence=payload.get("circle"), source_url=evidence.url, confidence=0.6) if payload.get("circle") else None,
                album_artist=FieldEvidence(value=payload.get("album_artist"), evidence=payload.get("album_artist"), source_url=evidence.url, confidence=0.6) if payload.get("album_artist") else None,
                catalog_no=FieldEvidence(value=payload.get("catalog_no"), evidence=payload.get("catalog_no"), source_url=evidence.url, confidence=0.7) if payload.get("catalog_no") else None,
                release_date=FieldEvidence(value=payload.get("release_date"), evidence=str(payload.get("release_date")), source_url=evidence.url, confidence=0.7) if payload.get("release_date") else None,
                cover_url=FieldEvidence(value=payload.get("cover_url"), source_url=evidence.url, confidence=0.7) if payload.get("cover_url") else None,
                tags=[tag for tag in payload.get("tags", []) if tag],
                tracks=[StructuredTrack.model_validate(track) for track in payload.get("tracks", [])],
                raw_payload=payload,
            )

        artist_names = [artist.get("name") for artist in payload.get("artists", []) if artist.get("name")]
        tracks = []
        for item in payload.get("tracks", []):
            song = item.get("song") or {}
            tracks.append(
                StructuredTrack(
                    number=int(item.get("discTrack") or item.get("trackNumber") or len(tracks) + 1),
                    title=song.get("name") or item.get("name") or "",
                    artist=None,
                    source_url=evidence.url,
                )
            )
        return StructuredAlbumPage(
            source=SourceName.VOCADB,
            url=evidence.url,
            title=FieldEvidence(value=payload.get("name"), evidence=payload.get("name"), source_url=evidence.url, confidence=0.9),
            circle=FieldEvidence(value=artist_names[0] if artist_names else None, evidence=artist_names[0] if artist_names else None, source_url=evidence.url, confidence=0.6),
            album_artist=FieldEvidence(value=artist_names[0] if artist_names else None, evidence=artist_names[0] if artist_names else None, source_url=evidence.url, confidence=0.6),
            catalog_no=FieldEvidence(value=payload.get("catalogNumber"), evidence=payload.get("catalogNumber"), source_url=evidence.url, confidence=0.7),
            release_date=FieldEvidence(value=payload.get("releaseDate"), evidence=str(payload.get("releaseDate")), source_url=evidence.url, confidence=0.7),
            cover_url=FieldEvidence(value=((payload.get("mainPicture") or {}).get("urlSmallThumb") if payload.get("mainPicture") else None), source_url=evidence.url, confidence=0.7),
            tags=[tag.get("tag", {}).get("name") for tag in payload.get("tags", []) if tag.get("tag", {}).get("name")],
            tracks=tracks,
            raw_payload=payload,
        )

    def _search_api(self, query: SearchQuery) -> list[SearchCandidate]:
        seen_ids: set[int] = set()
        candidates: list[SearchCandidate] = []

        for params in self._album_search_param_sets(query):
            try:
                payload = self.client.fetch_json(self._build_api_url("/api/albums", params), stealth=True)
            except Exception:
                continue
            candidates.extend(self._parse_album_items(payload, seen_ids))
            if len(candidates) >= self.config.sources.max_candidates:
                break
        return candidates[: self.config.sources.max_candidates]

    def _album_search_param_sets(self, query: SearchQuery) -> list[dict[str, object]]:
        param_sets: list[dict[str, object]] = []
        seen: set[str] = set()

        def add(params: dict[str, object]) -> None:
            key = json.dumps(params, ensure_ascii=False, sort_keys=True)
            if key in seen:
                return
            seen.add(key)
            param_sets.append(params)

        base = {
            "maxResults": self.config.sources.max_candidates,
            "preferAccurateMatches": "true",
            "lang": "Default",
        }

        raw_query = query.raw_query.strip()
        title_query = (query.title or "").strip()

        if raw_query:
            add({**base, "query": raw_query, "nameMatchMode": "Auto", "sort": "Name"})
            add({**base, "query": raw_query, "nameMatchMode": "Words", "sort": "Name"})
            add({**base, "query": raw_query, "nameMatchMode": "Partial", "sort": "NameThenReleaseDate"})
        if title_query and title_query.casefold() != raw_query.casefold():
            add({**base, "query": title_query, "nameMatchMode": "Auto", "sort": "Name"})
            add({**base, "query": title_query, "nameMatchMode": "Words", "sort": "Name"})
            add({**base, "query": title_query, "nameMatchMode": "Partial", "sort": "ReleaseDate"})

        return param_sets

    @staticmethod
    def _build_api_url(path: str, params: dict[str, object]) -> str:
        return f"{VOCADB_BASE}{path}?{urlencode(params, doseq=True)}"

    def _parse_album_items(self, payload: dict | list, seen_ids: set[int]) -> list[SearchCandidate]:
        if isinstance(payload, dict):
            items = payload.get("items", [])
        else:
            items = payload
        candidates: list[SearchCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            album_id = item.get("id")
            if not album_id:
                continue
            album_id = int(album_id)
            if album_id in seen_ids:
                continue
            seen_ids.add(album_id)
            candidates.append(
                SearchCandidate(
                    source=SourceName.VOCADB,
                    url=f"{VOCADB_BASE}/Al/{album_id}",
                    title_hint=item.get("name") or item.get("defaultName"),
                    circle_hint=item.get("artistString"),
                    extra={"album_id": album_id, "api": True},
                )
            )
        return candidates

    def _search_html(self, query: SearchQuery) -> list[SearchCandidate]:
        url = f"{VOCADB_BASE}/Search?filter=Albums&searchType=Album&term={quote(query.raw_query)}"
        response = self.client.fetch_html(url, stealth=True)
        if CF_TITLE in response.text:
            return []
        candidates = []
        seen: set[str] = set()
        for href, title in re.findall(r'href="(/Al/\d+)"[^>]*>([^<]+)<', response.text):
            if href in seen:
                continue
            seen.add(href)
            album_id = href.rsplit("/", 1)[-1]
            candidates.append(
                SearchCandidate(
                    source=SourceName.VOCADB,
                    url=urljoin(VOCADB_BASE, href),
                    title_hint=collapse_spaces(title),
                    extra={"album_id": int(album_id)},
                )
            )
        return candidates


def _page_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return collapse_spaces(match.group(1)) if match else "VocaDB"
