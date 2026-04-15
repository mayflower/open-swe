"""Helpers for converting Jira ADF to and from plain text."""

from __future__ import annotations

import re
from typing import Any

URL_RE = re.compile(r"https?://[^\s]+")


def _iter_content(node: Any) -> list[Any]:
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, list):
            return content
    if isinstance(node, list):
        return node
    return []


def _strip_trailing_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _render_text_node(node: dict[str, Any]) -> str:
    text = node.get("text", "")
    if not isinstance(text, str):
        text = ""

    for mark in node.get("marks", []):
        if not isinstance(mark, dict) or mark.get("type") != "link":
            continue
        attrs = mark.get("attrs")
        href = attrs.get("href", "") if isinstance(attrs, dict) else ""
        if isinstance(href, str) and href:
            return text if text == href else f"{text} ({href})"
    return text


def _render_inline(node: Any) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")
    if node_type == "text":
        return _render_text_node(node)
    if node_type == "hardBreak":
        return "\n"
    if node_type == "mention":
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            text = attrs.get("text")
            if isinstance(text, str) and text:
                return text
            mention_id = attrs.get("id")
            if isinstance(mention_id, str) and mention_id:
                return f"@{mention_id}"
        return "@mention"
    return "".join(_render_inline(child) for child in _iter_content(node))


def _flatten_list_item(node: Any) -> str:
    text = adf_to_text(node)
    return _strip_trailing_whitespace(text.replace("\n\n", "\n"))


def _render_block(node: Any) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")
    if node_type == "doc":
        blocks = [_render_block(child) for child in _iter_content(node)]
        return "\n\n".join(block for block in blocks if block.strip())
    if node_type == "paragraph":
        return "".join(_render_inline(child) for child in _iter_content(node))
    if node_type == "bulletList":
        items = [_flatten_list_item(child) for child in _iter_content(node)]
        return "\n".join(f"- {item}" for item in items if item)
    if node_type == "orderedList":
        items = [_flatten_list_item(child) for child in _iter_content(node)]
        return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1) if item)
    if node_type == "listItem":
        parts = [_render_block(child) for child in _iter_content(node)]
        return "\n\n".join(part for part in parts if part.strip())
    if node_type == "codeBlock":
        code = "".join(_render_inline(child) for child in _iter_content(node))
        return f"```\n{code}\n```"
    return "".join(_render_block(child) for child in _iter_content(node))


def adf_to_text(node: Any) -> str:
    """Convert Jira ADF into readable text."""
    return _strip_trailing_whitespace(_render_block(node))


def _text_with_link_marks(text: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    index = 0
    for match in URL_RE.finditer(text):
        start, end = match.span()
        if start > index:
            content.append({"type": "text", "text": text[index:start]})
        url = match.group(0)
        content.append(
            {
                "type": "text",
                "text": url,
                "marks": [{"type": "link", "attrs": {"href": url}}],
            }
        )
        index = end
    if index < len(text):
        content.append({"type": "text", "text": text[index:]})
    return content or [{"type": "text", "text": ""}]


def _paragraph_from_text(text: str) -> dict[str, Any]:
    lines = text.split("\n")
    content: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        content.extend(_text_with_link_marks(line))
        if index < len(lines) - 1:
            content.append({"type": "hardBreak"})
    return {"type": "paragraph", "content": content}


def _bullet_list_from_lines(lines: list[str]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [_paragraph_from_text(line[2:])],
            }
            for line in lines
        ],
    }


def text_to_adf(text: str) -> dict[str, Any]:
    """Convert plain text into a minimal Jira ADF document."""
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return {"type": "doc", "version": 1, "content": []}

    sections = [section for section in re.split(r"\n\s*\n", normalized) if section.strip()]
    content: list[dict[str, Any]] = []
    for section in sections:
        lines = [line for line in section.split("\n") if line.strip()]
        if lines and all(line.startswith("- ") for line in lines):
            content.append(_bullet_list_from_lines(lines))
            continue
        content.append(_paragraph_from_text(section))

    return {"type": "doc", "version": 1, "content": content}
