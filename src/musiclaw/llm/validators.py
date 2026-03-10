from __future__ import annotations

import json

from musiclaw.llm.schemas import AlbumResolverResponse, PageStructurerResponse


def parse_structurer_json(payload: str) -> PageStructurerResponse:
    return PageStructurerResponse.model_validate(json.loads(payload))


def parse_resolver_json(payload: str) -> AlbumResolverResponse:
    return AlbumResolverResponse.model_validate(json.loads(payload))
