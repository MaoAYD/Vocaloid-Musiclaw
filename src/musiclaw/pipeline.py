from __future__ import annotations

import os
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from musiclaw.collector import EvidenceCollector
from musiclaw.config import AppConfig
from musiclaw.llm.album_resolver import AlbumResolverAgent
from musiclaw.llm.page_structurer import PageStructurerAgent
from musiclaw.matcher import build_album_plan
from musiclaw.models import AlbumPlan, AlbumProcessingResult, DecisionAction, LocalAlbum, MatchStatus, RunReport, SearchOverrides, SourceName
from musiclaw.reporter import build_run_report
from musiclaw.scanner import scan_music_root
from musiclaw.sources import DizzylabAdapter, VCPediaAdapter, VocaDbAdapter
from musiclaw.tagger import TagWriter
from musiclaw.utils.cache import JsonCache
from musiclaw.utils.filename import sanitize_filename
from musiclaw.utils.http import ScraplingHttpClient


class MusicLawPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.cache = JsonCache(config.cache.dir)
        self.client = ScraplingHttpClient(
            config.network.user_agent,
            config.network.timeout_seconds,
            host_min_intervals=self._host_rate_limits(config.processing.parallel_profile),
        )
        adapters = []
        if "dizzylab" in config.sources.enabled:
            adapters.append(DizzylabAdapter(config, self.cache, self.client))
        if "vocadb" in config.sources.enabled:
            adapters.append(VocaDbAdapter(config, self.cache, self.client))
        if "vcpedia" in config.sources.enabled:
            adapters.append(VCPediaAdapter(config, self.cache, self.client))
        self.collector = EvidenceCollector(adapters, query_workers=config.processing.query_workers)
        self.page_structurer = PageStructurerAgent(config, self.cache)
        self.album_resolver = AlbumResolverAgent(config, self.cache)
        self.tag_writer = TagWriter(config, self.client)

    def close(self) -> None:
        self.client.close()

    def scan(self, root: Path) -> list:
        return scan_music_root(root)

    def match(self, root: Path) -> list[AlbumProcessingResult]:
        albums = scan_music_root(root)
        return self._match_many((album, None) for album in albums)

    def match_with_overrides(self, root: Path, album_inputs: list[tuple[LocalAlbum, SearchOverrides]]) -> RunReport:
        results = self._match_many(album_inputs)
        return build_run_report(root, "match", results)

    def match_album(self, album: LocalAlbum, overrides: SearchOverrides | None = None) -> AlbumProcessingResult:
        working_album = self._album_for_search(album, overrides)
        evidences, summary = self.collector.collect(working_album, overrides)
        pages = [self.page_structurer.structure(evidence) for evidence in evidences]
        if not pages:
            plan = AlbumPlan(
                album=working_album,
                status=MatchStatus.NOT_FOUND,
                reason="No evidence pages found.",
                collection_summary=summary,
                search_overrides=overrides or SearchOverrides(),
            )
            return AlbumProcessingResult(album=working_album, plan=plan, applied=False, errors=summary.errors or ["no_evidence"])

        candidate = self.album_resolver.resolve(working_album, pages)
        plan = build_album_plan(working_album, candidate, self.config)
        plan.evidence_pages = pages
        plan.collection_summary = summary
        plan.search_overrides = overrides or SearchOverrides()
        plan.status = self._derive_status(plan)
        if plan.status == MatchStatus.NOT_FOUND:
            plan.reason = "Evidence was found, but no usable album track metadata could be extracted."
        elif plan.status == MatchStatus.REVIEW and plan.candidate and not plan.candidate.tracks:
            plan.reason = "Manual evidence extracted album metadata, but the track list is incomplete. Review before apply."
        return AlbumProcessingResult(album=working_album, plan=plan, applied=False, errors=summary.errors)

    def apply_from_report(self, report: RunReport) -> RunReport:
        for result in report.results:
            review = result.plan.manual_review
            if not review.verified:
                continue
            if not result.plan.candidate or not result.plan.tag_writes:
                continue
            approved_action = review.approved_action
            if approved_action == DecisionAction.SKIP:
                result.plan.status = MatchStatus.SKIPPED
                continue
            if result.plan.status == MatchStatus.REVIEW and approved_action != DecisionAction.APPLY:
                continue
            if result.plan.status not in {MatchStatus.READY, MatchStatus.REVIEW}:
                continue
            try:
                self._write_snapshot(result)
                for tag_plan in result.plan.tag_writes:
                    self.tag_writer.apply(tag_plan, cover_url=result.plan.candidate.cover_url if result.plan.candidate else None)
                result.applied = True
                result.plan.status = MatchStatus.APPLIED
            except Exception as exc:
                result.errors.append(str(exc))
                result.applied = False
                result.plan.status = MatchStatus.ERROR
        return build_run_report(report.root, "apply", report.results)

    def run_report(self, root: Path, mode: str):
        results = self.match(root)
        return build_run_report(root, mode, results)

    @staticmethod
    def _derive_status(plan: AlbumPlan) -> MatchStatus:
        candidate = plan.candidate
        if not plan.evidence_pages or not candidate:
            return MatchStatus.NOT_FOUND
        if not candidate.tracks:
            if MusicLawPipeline._has_manual_candidate_metadata(plan):
                return MatchStatus.REVIEW
            return MatchStatus.NOT_FOUND
        if plan.action == DecisionAction.APPLY:
            return MatchStatus.READY
        if plan.action == DecisionAction.REVIEW:
            return MatchStatus.REVIEW
        return MatchStatus.SKIPPED

    @staticmethod
    def _has_manual_candidate_metadata(plan: AlbumPlan) -> bool:
        if not any(page.source == SourceName.MANUAL for page in plan.evidence_pages):
            return False
        candidate = plan.candidate
        if candidate is None:
            return False
        return any(
            [
                candidate.title,
                candidate.circle,
                candidate.album_artist,
                candidate.catalog_no,
                candidate.release_date,
                candidate.event_name,
                candidate.cover_url,
                candidate.tags,
            ]
        )

    def _write_snapshot(self, result: AlbumProcessingResult) -> None:
        snapshot_dir = Path("snapshots") / sanitize_filename(result.album.folder_name)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "album": result.album.model_dump(mode="json"),
            "plan": result.plan.model_dump(mode="json"),
        }
        (snapshot_dir / "before.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _album_for_search(album: LocalAlbum, overrides: SearchOverrides | None) -> LocalAlbum:
        working_album = album.model_copy(deep=True)
        if overrides and overrides.album_title:
            working_album.folder_name = overrides.album_title.strip()
            working_album.guessed_title = overrides.album_title.strip()
        return working_album

    @staticmethod
    def _host_rate_limits(profile: str) -> dict[str, float]:
        profile_key = (profile or "balanced").casefold()
        if profile_key == "safe":
            return {"www.dizzylab.net": 1.0, "dizzylab.net": 1.0, "vocadb.net": 1.2, "vcpedia.cn": 1.0}
        if profile_key == "aggressive":
            return {"www.dizzylab.net": 0.2, "dizzylab.net": 0.2, "vocadb.net": 0.35, "vcpedia.cn": 0.25}
        return {"www.dizzylab.net": 0.5, "dizzylab.net": 0.5, "vocadb.net": 0.7, "vcpedia.cn": 0.5}

    def _match_many(self, album_inputs) -> list[AlbumProcessingResult]:
        work_items = list(album_inputs)
        if not work_items:
            return []
        max_workers = self._album_workers(len(work_items))
        if max_workers <= 1:
            return [self.match_album(album, overrides) for album, overrides in work_items]
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="musiclaw-album") as executor:
            return list(executor.map(lambda item: self.match_album(item[0], item[1]), work_items))

    def _album_workers(self, album_count: int) -> int:
        configured = max(1, int(self.config.processing.album_workers))
        return min(configured, max(1, album_count), max(1, os.cpu_count() or 1))
