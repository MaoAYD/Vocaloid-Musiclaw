from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


JSON_WRAPPER_RE = re.compile(r"<p>\s*(\{.*\}|\[.*\])\s*</p>", re.IGNORECASE | re.DOTALL)


@dataclass
class HttpResponse:
    url: str
    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            cleaned = self.text.strip()
            match = JSON_WRAPPER_RE.search(cleaned)
            if match:
                return json.loads(match.group(1))
            if cleaned.startswith("<html"):
                cleaned = re.sub(r"<[^>]+>", "", cleaned)
                cleaned = cleaned.strip()
                return json.loads(cleaned)
            raise


class ScraplingHttpClient:
    def __init__(self, user_agent: str, timeout_seconds: int = 30, host_min_intervals: dict[str, float] | None = None) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.host_min_intervals = host_min_intervals or {}
        self._host_lock = threading.Lock()
        self._host_next_allowed: dict[str, float] = {}
        self._sync_client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    def close(self) -> None:
        self._sync_client.close()

    def fetch_html(self, url: str, *, stealth: bool = False) -> HttpResponse:
        attempts = [stealth] if stealth else [False, True]
        last_error: Exception | None = None
        last_response: HttpResponse | None = None
        for use_stealth in attempts:
            try:
                response = self._fetch_with_scrapling(url, stealth=use_stealth)
            except Exception as exc:
                last_error = exc
                continue
            last_response = response
            if not self._looks_blocked(response):
                return response

        self._respect_rate_limit(url)
        response = self._sync_client.get(url)
        fallback = HttpResponse(
            url=str(response.url),
            status_code=response.status_code,
            text=response.text,
            content=response.content,
            headers=dict(response.headers),
        )
        if not self._looks_blocked(fallback):
            return fallback
        if last_response is not None:
            return last_response
        if last_error is not None:
            raise last_error
        return fallback

    def fetch_json(self, url: str, *, stealth: bool = False) -> Any:
        response = self.fetch_html(url, stealth=stealth)
        return response.json()

    def download_bytes(self, url: str) -> bytes:
        self._respect_rate_limit(url)
        response = self._sync_client.get(url)
        response.raise_for_status()
        return response.content

    def _fetch_with_scrapling(self, url: str, *, stealth: bool = False) -> HttpResponse:
        from scrapling.fetchers import Fetcher, StealthyFetcher

        self._respect_rate_limit(url)

        if stealth:
            page = StealthyFetcher.fetch(
                url,
                headless=True,
                disable_resources=False,
                network_idle=True,
            )
        else:
            page = Fetcher.get(url)
        html = self._coerce_html(page)
        headers = {}
        status_code = getattr(page, "status", None) or getattr(page, "status_code", 200) or 200
        page_url = getattr(page, "url", None) or url
        return HttpResponse(
            url=str(page_url),
            status_code=int(status_code),
            text=html,
            content=html.encode("utf-8", errors="ignore"),
            headers=headers,
        )

    def _respect_rate_limit(self, url: str) -> None:
        hostname = (urlparse(url).hostname or "").casefold()
        interval = self.host_min_intervals.get(hostname)
        if not interval:
            return
        with self._host_lock:
            now = time.monotonic()
            next_allowed = self._host_next_allowed.get(hostname, now)
            wait_time = max(0.0, next_allowed - now)
            if wait_time > 0:
                time.sleep(wait_time)
                now = time.monotonic()
            self._host_next_allowed[hostname] = now + interval

    @staticmethod
    def _coerce_html(page: Any) -> str:
        for attr in ("html", "html_content", "content", "body", "markup", "text"):
            if not hasattr(page, attr):
                continue
            value = getattr(page, attr)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="ignore")
            if isinstance(value, str):
                return value
        return str(page)

    @staticmethod
    def _looks_blocked(response: HttpResponse) -> bool:
        if response.status_code in {401, 403, 429, 503}:
            return True
        text = response.text[:5000].casefold()
        blocked_markers = (
            "just a moment",
            "cf-browser-verification",
            "cloudflare",
            "captcha",
            "verify you are human",
            "access denied",
            "attention required",
            "机器人",
            "人机验证",
        )
        if any(marker in text for marker in blocked_markers):
            return True
        if re.search(r"<title>\s*(403|access denied|just a moment)", response.text, re.IGNORECASE):
            return True
        return False
