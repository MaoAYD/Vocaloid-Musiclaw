from __future__ import annotations

from typing import Any

from scrapling.parser import Selector, Selectors  # type: ignore[import-not-found]

from musiclaw.utils.textnorm import collapse_spaces


def parse_html(html: str) -> Selector:
    return Selector(html)


def first_text(node: Selector | Selectors, selector: str) -> str | None:
    value = node.css(f"{selector}::text").get()
    return collapse_spaces(value) or None


def all_texts(node: Selector | Selectors, selector: str) -> list[str]:
    values = node.css(f"{selector}::text").getall()
    return [collapse_spaces(value) for value in values if collapse_spaces(value)]


def first_attr(node: Selector | Selectors, selector: str, attr: str) -> str | None:
    value = node.css(f"{selector}::attr({attr})").get()
    return str(value).strip() if value else None


def node_text(node: Selector | Selectors) -> str:
    values = node.css("::text").getall() if hasattr(node, "css") else []
    return collapse_spaces(" ".join(str(value) for value in values))


def document_text(html_or_node: str | Selector) -> str:
    node = parse_html(html_or_node) if isinstance(html_or_node, str) else html_or_node
    return "\n".join(text for text in all_texts(node, "") if text)


def attr_value(node: Any, attr: str) -> str | None:
    attrib = getattr(node, "attrib", {}) or {}
    value = attrib.get(attr)
    return str(value).strip() if value else None
