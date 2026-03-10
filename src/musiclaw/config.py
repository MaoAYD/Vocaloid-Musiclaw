from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class RootConfig(BaseModel):
    music_dir: Path = Path(".")


class SourcesConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["dizzylab", "vocadb", "vcpedia"])
    max_candidates: int = 5


class MatchingConfig(BaseModel):
    auto_apply_score: float = 0.85
    review_score: float = 0.65
    require_track_count_match: bool = True


class LlmConfig(BaseModel):
    provider: str = "openai-compatible"
    base_url: str = ""
    api_key_env: str = "MUSICLAW_LLM_API_KEY"
    model_env: str = "MUSICLAW_LLM_MODEL"
    base_url_env: str = "MUSICLAW_LLM_BASE_URL"
    temperature: float = 0.1
    enabled: bool = True

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env)

    @property
    def model(self) -> str | None:
        return os.getenv(self.model_env)

    @property
    def resolved_base_url(self) -> str | None:
        return os.getenv(self.base_url_env) or self.base_url or None


class NetworkConfig(BaseModel):
    timeout_seconds: int = 30
    user_agent: str = "musiclaw/0.1"


class ProcessingConfig(BaseModel):
    album_workers: int = max(1, min(4, (os.cpu_count() or 2)))
    query_workers: int = 4
    parallel_profile: str = "balanced"


class TagsConfig(BaseModel):
    rename_template: str = "{track:02d}. {title}"
    write_cover: bool = True
    write_extended_fields: bool = True
    write_tags: bool = True
    rename_files: bool = True


class CacheConfig(BaseModel):
    dir: Path = Path("cache")


class AppConfig(BaseModel):
    root: RootConfig = Field(default_factory=RootConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    tags: TagsConfig = Field(default_factory=TagsConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)


def load_config(config_path: Path | None = None) -> AppConfig:
    if config_path is None:
        return AppConfig()
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    return AppConfig.model_validate(payload)
