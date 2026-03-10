from __future__ import annotations

from pathlib import Path

import typer

from musiclaw.config import load_config
from musiclaw.models import MatchStatus
from musiclaw.pipeline import MusicLawPipeline
from musiclaw.reporter import load_report, render_console_summary, render_review_lines, save_report


app = typer.Typer(add_completion=False, no_args_is_help=True)


def _parse_statuses(statuses: list[str] | None) -> set[MatchStatus] | None:
    if not statuses:
        return None
    return {MatchStatus(status) for status in statuses}


@app.command()
def scan(root: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True), config: Path | None = typer.Option(None)) -> None:
    settings = load_config(config)
    pipeline = MusicLawPipeline(settings)
    try:
        albums = pipeline.scan(root)
    finally:
        pipeline.close()
    for album in albums:
        typer.echo(f"{album.folder_name} | tracks={album.track_count} | title={album.guessed_title} | circle={album.guessed_circle}")


@app.command()
def match(
    root: Path = typer.Option(..., exists=True, file_okay=False, dir_okay=True),
    config: Path | None = typer.Option(None),
    report: Path = typer.Option(Path("reports/latest.json")),
) -> None:
    settings = load_config(config)
    pipeline = MusicLawPipeline(settings)
    try:
        run_report = pipeline.run_report(root, mode="match")
    finally:
        pipeline.close()
    save_report(run_report, report)
    typer.echo(render_console_summary(run_report))
    typer.echo(f"report={report}")


@app.command()
def review(
    report: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    status: list[str] = typer.Option([], help="Repeatable status filter: ready, review, not_found, skipped, applied, error"),
) -> None:
    run_report = load_report(report)
    typer.echo(render_console_summary(run_report))
    typer.echo(f"review_report={report}")
    typer.echo("Set `plan.manual_review.verified=true` for approved albums.")
    typer.echo("For `review` rows, also set `plan.manual_review.approved_action=\"apply\"`.")
    for line in render_review_lines(run_report, _parse_statuses(status)):
        typer.echo(line)


@app.command()
def apply(
    report: Path = typer.Option(..., exists=True, file_okay=True, dir_okay=False),
    config: Path | None = typer.Option(None),
    output: Path = typer.Option(Path("reports/apply.json")),
) -> None:
    settings = load_config(config)
    reviewed_report = load_report(report)
    pipeline = MusicLawPipeline(settings)
    try:
        run_report = pipeline.apply_from_report(reviewed_report)
    finally:
        pipeline.close()
    save_report(run_report, output)
    typer.echo(render_console_summary(run_report))
    typer.echo(f"report={output}")
