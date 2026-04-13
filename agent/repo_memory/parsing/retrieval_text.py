from __future__ import annotations

from .common import ParsedEntity, normalize_body


def build_retrieval_text(entity: ParsedEntity) -> str:
    parent = entity.parent_qualified_name or "root"
    parts = [
        f"kind={entity.kind.value}",
        f"qualified_name={entity.qualified_name}",
        f"signature={entity.signature}".strip(),
        f"parent={parent}",
    ]
    if entity.docstring:
        parts.append(f"docstring={entity.docstring.strip()}")
    if entity.comment:
        parts.append(f"comment={entity.comment.strip()}")
    parts.append(f"body={normalize_body(entity.body)}")
    return "\n".join(part for part in parts if part and not part.endswith("="))

