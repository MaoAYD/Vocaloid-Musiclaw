from __future__ import annotations

from musiclaw.config import AppConfig
from musiclaw.models import AlbumCandidate, AlbumPlan, DecisionAction, LocalAlbum, MatchBreakdown, RenamePlan, TagWritePlan
from musiclaw.utils.filename import target_path_for_track
from musiclaw.utils.similarity import ratio


def build_album_plan(album: LocalAlbum, candidate: AlbumCandidate, config: AppConfig) -> AlbumPlan:
    breakdown = score_candidate(album, candidate)
    action = decide_action(breakdown.total, config)
    reason = build_reason(action, breakdown.total, candidate)
    tag_writes: list[TagWritePlan] = []
    rename_plans: list[RenamePlan] = []

    if candidate.tracks:
        candidate_tracks = {track.number: track for track in candidate.tracks}
        for local_track in album.files:
            remote = candidate_tracks.get(local_track.index)
            if not remote:
                continue
            tags = {
                "title": remote.title,
                "artist": remote.artist or candidate.album_artist or candidate.circle,
                "album": candidate.title or album.guessed_title or album.folder_name,
                "albumartist": candidate.album_artist or candidate.circle,
                "tracknumber": str(remote.number),
                "discnumber": "1",
                "date": candidate.release_date,
                "genre": "; ".join(candidate.tags) if candidate.tags else None,
                "circle": candidate.circle,
                "catalog_no": candidate.catalog_no,
                "event_name": candidate.event_name,
                "source_url": ", ".join(candidate.evidence_urls),
                "source_site": ", ".join(source.value for source in candidate.source_priority),
            }
            rename_target = target_path_for_track(local_track.path, config.tags.rename_template, remote.number, remote.title)
            tag_writes.append(TagWritePlan(path=local_track.path, tags={key: value for key, value in tags.items() if value}, rename_to=rename_target))
            if rename_target != local_track.path:
                rename_plans.append(RenamePlan(source_path=local_track.path, target_path=rename_target))

    return AlbumPlan(
        album=album,
        candidate=candidate,
        breakdown=breakdown,
        action=action,
        reason=reason,
        tag_writes=tag_writes,
        rename_plans=rename_plans,
    )


def score_candidate(album: LocalAlbum, candidate: AlbumCandidate) -> MatchBreakdown:
    track_count_score = 0.35 if album.track_count and album.track_count == len(candidate.tracks) else 0.0
    track_sequence_score = 0.15 if _has_contiguous_track_numbers(candidate) else 0.0
    title_score = ratio(album.guessed_title or album.folder_name, candidate.title) * 0.20
    circle_score = ratio(album.guessed_circle, candidate.circle or candidate.album_artist) * 0.10
    catalog_score = 0.10 if album.guessed_catalog_no and candidate.catalog_no and album.guessed_catalog_no.casefold() == candidate.catalog_no.casefold() else 0.0
    date_event_score = 0.0
    if album.guessed_year and candidate.release_date and album.guessed_year in candidate.release_date:
        date_event_score += 0.03
    if album.guessed_event and candidate.event_name and ratio(album.guessed_event, candidate.event_name) >= 0.75:
        date_event_score += 0.02
    source_consistency_score = min(candidate.confidence, 1.0) * 0.05
    return MatchBreakdown(
        track_count_score=round(track_count_score, 4),
        track_sequence_score=round(track_sequence_score, 4),
        title_score=round(title_score, 4),
        circle_score=round(circle_score, 4),
        catalog_score=round(catalog_score, 4),
        date_event_score=round(date_event_score, 4),
        source_consistency_score=round(source_consistency_score, 4),
    )


def decide_action(score: float, config: AppConfig) -> DecisionAction:
    if score >= config.matching.auto_apply_score:
        return DecisionAction.APPLY
    if score >= config.matching.review_score:
        return DecisionAction.REVIEW
    return DecisionAction.SKIP


def build_reason(action: DecisionAction, score: float, candidate: AlbumCandidate) -> str:
    if action == DecisionAction.APPLY:
        return f"Matched with score {score:.2f} using {len(candidate.evidence_urls)} evidence pages."
    if action == DecisionAction.REVIEW:
        return f"Needs review at score {score:.2f}; conflicts: {len(candidate.conflicts)}."
    return f"Skipped at score {score:.2f}; insufficient evidence."


def _has_contiguous_track_numbers(candidate: AlbumCandidate) -> bool:
    if not candidate.tracks:
        return False
    numbers = sorted(track.number for track in candidate.tracks)
    return numbers == list(range(1, len(numbers) + 1))
