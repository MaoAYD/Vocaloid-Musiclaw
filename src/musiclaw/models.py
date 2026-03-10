from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator


class SourceName(str, Enum):
    DIZZYLAB = "dizzylab"
    VOCADB = "vocadb"
    VCPEDIA = "vcpedia"
    MANUAL = "manual"


class DecisionAction(str, Enum):
    APPLY = "apply"
    REVIEW = "review"
    SKIP = "skip"


class MatchStatus(str, Enum):
    READY = "ready"
    REVIEW = "review"
    NOT_FOUND = "not_found"
    SKIPPED = "skipped"
    APPLIED = "applied"
    ERROR = "error"


class ManualReview(BaseModel):
    verified: bool = False
    approved_action: DecisionAction | None = None
    reviewer: str | None = None
    notes: str | None = None


class SearchOverrides(BaseModel):
    album_title: str | None = None
    priority_urls: list[str] = Field(default_factory=list)
    manual_urls_only: bool = False
    manual_text: str | None = None


class LocalTrack(BaseModel):
    path: Path
    index: int
    ext: str
    existing_tags: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def filename(self) -> str:
        return self.path.name


class LocalAlbum(BaseModel):
    folder_path: Path
    folder_name: str
    files: list[LocalTrack]
    guessed_title: str | None = None
    guessed_circle: str | None = None
    guessed_catalog_no: str | None = None
    guessed_event: str | None = None
    guessed_year: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def track_count(self) -> int:
        return len(self.files)


class SearchQuery(BaseModel):
    raw_query: str
    title: str | None = None
    circle: str | None = None
    catalog_no: str | None = None
    event: str | None = None
    year: str | None = None


class SearchCandidate(BaseModel):
    source: SourceName
    url: str
    title_hint: str | None = None
    circle_hint: str | None = None
    score_hint: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SearchAttempt(BaseModel):
    source: SourceName
    query: str
    candidate_count: int = 0
    candidate_urls: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CollectionSummary(BaseModel):
    queries: list[str] = Field(default_factory=list)
    searched_sources: list[SourceName] = Field(default_factory=list)
    attempts: list[SearchAttempt] = Field(default_factory=list)
    candidate_count: int = 0
    evidence_count: int = 0
    errors: list[str] = Field(default_factory=list)


class SourceEvidence(BaseModel):
    source: SourceName
    url: str
    page_title: str
    cleaned_text: str
    extracted_snippets: list[str] = Field(default_factory=list)
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    raw_html: str | None = None


class FieldEvidence(BaseModel):
    value: str | None
    evidence: str | None = None
    source_url: str | None = None
    confidence: float = 0.0


class StructuredTrack(BaseModel):
    number: int
    title: str
    artist: str | None = None
    composer: str | None = None
    duration: str | None = None
    evidence: str | None = None
    source_url: str | None = None


class StructuredAlbumPage(BaseModel):
    source: SourceName
    url: str
    title: FieldEvidence | None = None
    circle: FieldEvidence | None = None
    album_artist: FieldEvidence | None = None
    catalog_no: FieldEvidence | None = None
    release_date: FieldEvidence | None = None
    event_name: FieldEvidence | None = None
    cover_url: FieldEvidence | None = None
    tags: list[str] = Field(default_factory=list)
    tracks: list[StructuredTrack] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class TrackCandidate(BaseModel):
    number: int
    title: str
    artist: str | None = None
    composer: str | None = None
    duration: str | None = None
    evidence_url: str | None = None


class AlbumCandidate(BaseModel):
    source_priority: list[SourceName] = Field(default_factory=list)
    title: str | None = None
    circle: str | None = None
    album_artist: str | None = None
    catalog_no: str | None = None
    release_date: str | None = None
    event_name: str | None = None
    cover_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    tracks: list[TrackCandidate] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class MatchBreakdown(BaseModel):
    track_count_score: float = 0.0
    track_sequence_score: float = 0.0
    title_score: float = 0.0
    circle_score: float = 0.0
    catalog_score: float = 0.0
    date_event_score: float = 0.0
    source_consistency_score: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return round(
            self.track_count_score
            + self.track_sequence_score
            + self.title_score
            + self.circle_score
            + self.catalog_score
            + self.date_event_score
            + self.source_consistency_score,
            4,
        )


class RenamePlan(BaseModel):
    source_path: Path
    target_path: Path


class TagWritePlan(BaseModel):
    path: Path
    tags: dict[str, Any]
    rename_to: Path | None = None


class AlbumPlan(BaseModel):
    album: LocalAlbum
    candidate: AlbumCandidate | None = None
    breakdown: MatchBreakdown = Field(default_factory=MatchBreakdown)
    action: DecisionAction = DecisionAction.SKIP
    status: MatchStatus = MatchStatus.SKIPPED
    reason: str = ""
    collection_summary: CollectionSummary = Field(default_factory=CollectionSummary)
    tag_writes: list[TagWritePlan] = Field(default_factory=list)
    rename_plans: list[RenamePlan] = Field(default_factory=list)
    evidence_pages: list[StructuredAlbumPage] = Field(default_factory=list)
    manual_review: ManualReview = Field(default_factory=ManualReview)
    search_overrides: SearchOverrides = Field(default_factory=SearchOverrides)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_manual_review(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "manual_review" in data:
            return data
        verified = data.pop("user_verified", False)
        notes = data.pop("user_notes", None)
        reviewer = data.pop("user_reviewer", None)
        approved_action = data.pop("approved_action", None)
        if verified or notes or reviewer or approved_action:
            data["manual_review"] = {
                "verified": verified,
                "notes": notes,
                "reviewer": reviewer,
                "approved_action": approved_action,
            }
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def user_verified(self) -> bool:
        return self.manual_review.verified

    @computed_field  # type: ignore[prop-decorator]
    @property
    def user_notes(self) -> str | None:
        return self.manual_review.notes


class AlbumProcessingResult(BaseModel):
    album: LocalAlbum
    plan: AlbumPlan
    applied: bool = False
    errors: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    schema_version: int = 2
    root: Path
    processed_at: str
    mode: str
    results: list[AlbumProcessingResult] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def totals(self) -> dict[str, int]:
        return {
            "albums": len(self.results),
            "applied": sum(1 for result in self.results if result.applied),
            "ready": sum(1 for result in self.results if result.plan.status == MatchStatus.READY),
            "review": sum(1 for result in self.results if result.plan.status == MatchStatus.REVIEW),
            "not_found": sum(1 for result in self.results if result.plan.status == MatchStatus.NOT_FOUND),
            "skipped": sum(1 for result in self.results if result.plan.status == MatchStatus.SKIPPED),
            "verified": sum(1 for result in self.results if result.plan.manual_review.verified),
            "errors": sum(len(result.errors) for result in self.results),
        }
