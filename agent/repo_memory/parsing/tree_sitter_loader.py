from __future__ import annotations

from functools import lru_cache

import tree_sitter as ts
import tree_sitter_go as tsg
import tree_sitter_python as tsp
import tree_sitter_rust as tsr
import tree_sitter_typescript as tst

_LANGUAGE_FACTORIES = {
    "python": tsp.language,
    "typescript": tst.language_typescript,
    "tsx": tst.language_tsx,
    "go": tsg.language,
    "rust": tsr.language,
}


@lru_cache(maxsize=None)
def get_language(name: str) -> ts.Language:
    factory = _LANGUAGE_FACTORIES.get(name)
    if factory is None:
        raise ValueError(f"Unsupported tree-sitter language: {name}")
    return ts.Language(factory())


@lru_cache(maxsize=None)
def get_parser(name: str) -> ts.Parser:
    return ts.Parser(get_language(name))


def parse_source(name: str, source: str) -> ts.Tree:
    return get_parser(name).parse(source.encode("utf-8"))


def node_text(node: ts.Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
