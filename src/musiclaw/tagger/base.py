from __future__ import annotations

from pathlib import Path

from musiclaw.config import AppConfig
from musiclaw.models import TagWritePlan
from musiclaw.tagger.flac import write_flac_tags
from musiclaw.tagger.mp3 import write_mp3_tags
from musiclaw.utils.http import ScraplingHttpClient


class TagWriter:
    def __init__(self, config: AppConfig, client: ScraplingHttpClient) -> None:
        self.config = config
        self.client = client

    def apply(self, plan: TagWritePlan, cover_url: str | None = None) -> None:
        cover_data = self.client.download_bytes(cover_url) if cover_url and self.config.tags.write_cover else None
        suffix = plan.path.suffix.lower()
        if suffix == ".flac":
            write_flac_tags(plan.path, plan.tags, cover_data)
        elif suffix == ".mp3":
            write_mp3_tags(plan.path, plan.tags, cover_data)
        else:
            raise ValueError(f"Unsupported file type: {suffix}")
        if plan.rename_to and self.config.tags.rename_files and plan.rename_to != plan.path:
            Path(plan.path).rename(plan.rename_to)
