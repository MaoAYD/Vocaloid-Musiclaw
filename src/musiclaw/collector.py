from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from musiclaw.models import CollectionSummary, LocalAlbum, SearchAttempt, SearchOverrides, SourceEvidence
from musiclaw.models import SourceName
from musiclaw.scanner import build_search_queries
from musiclaw.sources.base import SourceAdapter


class EvidenceCollector:
    def __init__(self, adapters: list[SourceAdapter], query_workers: int = 4) -> None:
        self.adapters = adapters
        self.query_workers = max(1, query_workers)

    def collect(self, album: LocalAlbum, overrides: SearchOverrides | None = None) -> tuple[list[SourceEvidence], CollectionSummary]:
        pages: list[SourceEvidence] = []
        seen_urls: set[str] = set()
        queries = build_search_queries(album, overrides)
        attempts: list[SearchAttempt] = []
        errors: list[str] = []
        candidate_total = 0

        if overrides and overrides.manual_text and overrides.manual_text.strip():
            manual_text = overrides.manual_text.strip()
            manual_evidence = manual_text_evidence(album, manual_text)
            seen_urls.add(manual_evidence.url)
            pages.append(manual_evidence)
            attempts.append(
                SearchAttempt(
                    source=SourceName.MANUAL,
                    query="manual-text",
                    candidate_count=1,
                    candidate_urls=[manual_evidence.url],
                )
            )

        if overrides and overrides.priority_urls:
            for manual_url in overrides.priority_urls:
                manual_url = manual_url.strip()
                if not manual_url or manual_url in seen_urls:
                    continue
                seen_urls.add(manual_url)
                adapter = next((candidate for candidate in self.adapters if candidate.can_handle_url(manual_url)), None)
                if adapter is None:
                    errors.append(f"manual-url:{manual_url}:no_adapter")
                    continue
                attempts.append(
                    SearchAttempt(
                        source=cast(SourceName, adapter.enum_name),
                        query=f"manual-url:{manual_url}",
                        candidate_count=1,
                        candidate_urls=[manual_url],
                    )
                )
                try:
                    evidence = adapter.fetch_detail(adapter_search_candidate(adapter.enum_name, manual_url))
                    evidence.extracted_fields["priority_url"] = manual_url
                    evidence.extracted_fields["priority_url_source"] = True
                    pages.append(evidence)
                except Exception as exc:
                    errors.append(f"{manual_url}:{exc}")

        if overrides and overrides.manual_urls_only:
            summary = CollectionSummary(
                queries=[query.raw_query for query in queries],
                searched_sources=self._searched_sources(overrides),
                attempts=attempts,
                candidate_count=candidate_total,
                evidence_count=len(pages),
                errors=errors,
            )
            return pages, summary

        search_jobs = [(adapter, query) for adapter in self.adapters for query in queries]
        search_results: list[tuple[SourceAdapter, object, object]] = []
        if search_jobs:
            workers = min(self.query_workers, len(search_jobs))
            if workers <= 1:
                search_results = [(adapter, query, self._run_search(adapter, query)) for adapter, query in search_jobs]
            else:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="musiclaw-search") as executor:
                    search_results = list(executor.map(lambda job: (job[0], job[1], self._run_search(job[0], job[1])), search_jobs))

        detail_jobs = []
        for adapter, query, outcome in search_results:
            if isinstance(outcome, Exception):
                message = f"{adapter.source_name}:{query.raw_query}:{outcome}"
                errors.append(message)
                attempts.append(
                    SearchAttempt(
                        source=cast(SourceName, adapter.enum_name),
                        query=query.raw_query,
                        errors=[str(outcome)],
                    )
                )
                continue

            candidates = outcome
            candidate_total += len(candidates)
            attempts.append(
                SearchAttempt(
                    source=cast(SourceName, adapter.enum_name),
                    query=query.raw_query,
                    candidate_count=len(candidates),
                    candidate_urls=[candidate.url for candidate in candidates],
                )
            )
            for candidate in candidates:
                if candidate.url in seen_urls:
                    continue
                seen_urls.add(candidate.url)
                detail_jobs.append((adapter, candidate))

        detail_results: list[tuple[SourceAdapter, object, object]] = []
        if detail_jobs:
            workers = min(self.query_workers, len(detail_jobs))
            if workers <= 1:
                detail_results = [(adapter, candidate, self._run_detail(adapter, candidate)) for adapter, candidate in detail_jobs]
            else:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="musiclaw-detail") as executor:
                    detail_results = list(executor.map(lambda job: (job[0], job[1], self._run_detail(job[0], job[1])), detail_jobs))

        for _adapter, candidate, outcome in detail_results:
            if isinstance(outcome, Exception):
                errors.append(f"{candidate.url}:{outcome}")
                continue
            pages.append(outcome)
        summary = CollectionSummary(
            queries=[query.raw_query for query in queries],
            searched_sources=self._searched_sources(overrides),
            attempts=attempts,
            candidate_count=candidate_total,
            evidence_count=len(pages),
            errors=errors,
        )
        return pages, summary

    def _searched_sources(self, overrides: SearchOverrides | None) -> list[SourceName]:
        sources = [cast(SourceName, adapter.enum_name) for adapter in self.adapters]
        if overrides and overrides.manual_text and overrides.manual_text.strip():
            sources.append(SourceName.MANUAL)
        return sources

    @staticmethod
    def _run_search(adapter: SourceAdapter, query):
        try:
            return adapter.search(query)
        except Exception as exc:  # pragma: no cover - exercised in integration flows
            return exc

    @staticmethod
    def _run_detail(adapter: SourceAdapter, candidate):
        try:
            return adapter.fetch_detail(candidate)
        except Exception as exc:  # pragma: no cover - exercised in integration flows
            return exc


def adapter_search_candidate(source: SourceName, url: str):
    from musiclaw.models import SearchCandidate

    return SearchCandidate(source=source, url=url, title_hint=url)


def manual_text_evidence(album: LocalAlbum, manual_text: str) -> SourceEvidence:
    digest = hashlib.sha1(manual_text.encode("utf-8")).hexdigest()[:12]
    return SourceEvidence(
        source=SourceName.MANUAL,
        url=f"manual://{digest}",
        page_title=f"Manual text for {album.folder_name}",
        cleaned_text=manual_text,
        extracted_fields={
            "manual_input": True,
            "manual_text": manual_text,
            "title_hint": album.guessed_title or album.folder_name,
            "album_path": str(album.folder_path),
        },
    )
