from __future__ import annotations

from abc import ABC, abstractmethod

from musiclaw.config import AppConfig
from musiclaw.models import SearchCandidate, SearchQuery, SourceEvidence, SourceName, StructuredAlbumPage
from musiclaw.utils.cache import JsonCache
from musiclaw.utils.html import parse_html
from musiclaw.utils.http import ScraplingHttpClient


class SourceAdapter(ABC):
    source_name: str
    enum_name: SourceName
    url_hosts: tuple[str, ...] = ()

    def __init__(self, config: AppConfig, cache: JsonCache, client: ScraplingHttpClient) -> None:
        self.config = config
        self.cache = cache
        self.client = client

    @abstractmethod
    def search(self, query: SearchQuery) -> list[SearchCandidate]:
        raise NotImplementedError

    @abstractmethod
    def fetch_detail(self, candidate: SearchCandidate) -> SourceEvidence:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, evidence: SourceEvidence) -> StructuredAlbumPage:
        raise NotImplementedError

    def can_handle_url(self, url: str) -> bool:
        return any(host in url for host in self.url_hosts)

    @staticmethod
    def selector(html: str):
        return parse_html(html)
