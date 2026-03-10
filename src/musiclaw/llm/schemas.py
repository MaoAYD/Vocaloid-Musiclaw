from __future__ import annotations

from pydantic import BaseModel, Field


class PageStructurerResponse(BaseModel):
    title: dict | None = None
    circle: dict | None = None
    album_artist: dict | None = None
    catalog_no: dict | None = None
    release_date: dict | None = None
    event_name: dict | None = None
    cover_url: dict | None = None
    tags: list[str] = Field(default_factory=list)
    tracks: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AlbumResolverResponse(BaseModel):
    title: str | None = None
    circle: str | None = None
    album_artist: str | None = None
    catalog_no: str | None = None
    release_date: str | None = None
    event_name: str | None = None
    cover_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    tracks: list[dict] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    confidence: float = 0.0
