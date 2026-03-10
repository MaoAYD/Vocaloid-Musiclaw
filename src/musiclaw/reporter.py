from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from musiclaw.models import AlbumProcessingResult, MatchStatus, RunReport


def build_run_report(root: Path, mode: str, results: list[AlbumProcessingResult]) -> RunReport:
    return RunReport(
        root=root,
        processed_at=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        results=results,
    )


def save_report(report: RunReport, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def load_report(source: Path) -> RunReport:
    return RunReport.model_validate_json(source.read_text(encoding="utf-8"))


def render_console_summary(report: RunReport) -> str:
    totals = report.totals
    lines = [
        f"mode={report.mode}",
        f"albums={totals['albums']}",
        f"applied={totals['applied']}",
        f"ready={totals['ready']}",
        f"review={totals['review']}",
        f"not_found={totals['not_found']}",
        f"verified={totals['verified']}",
        f"skipped={totals['skipped']}",
        f"errors={totals['errors']}",
    ]
    return " | ".join(lines)


def render_review_lines(report: RunReport, statuses: set[MatchStatus] | None = None) -> list[str]:
    selected = statuses or {MatchStatus.READY, MatchStatus.REVIEW, MatchStatus.NOT_FOUND}
    lines: list[str] = []
    for result in report.results:
        if result.plan.status not in selected:
            continue
        candidate = result.plan.candidate.title if result.plan.candidate and result.plan.candidate.title else "-"
        sources = ",".join(source.value for source in result.plan.collection_summary.searched_sources) or "-"
        verified = "yes" if result.plan.manual_review.verified else "no"
        lines.append(
            " | ".join(
                [
                    f"status={result.plan.status.value}",
                    f"verified={verified}",
                    f"album={result.album.folder_name}",
                    f"candidate={candidate}",
                    f"sources={sources}",
                    f"reason={result.plan.reason or '-'}",
                ]
            )
        )
    return lines
