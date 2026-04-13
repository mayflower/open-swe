from pathlib import Path

from agent.repo_memory.parsing.python_parser import parse_python_entities
from agent.repo_memory.parsing.retrieval_text import build_retrieval_text


FIXTURE = Path(__file__).parent / "fixtures" / "python_sample_module.py.txt"


def test_python_entity_extraction_preserves_parentage_and_qualified_names() -> None:
    source = FIXTURE.read_text()
    result = parse_python_entities("agent/example.py", source)

    qualified_names = [entity.qualified_name for entity in result.entities]

    assert qualified_names == [
        "example.py",
        "Widget",
        "Widget.render",
        "helper",
    ]
    method = next(entity for entity in result.entities if entity.qualified_name == "Widget.render")
    assert method.parent_qualified_name == "Widget"
    assert method.signature == "def render(self, name)"


def test_retrieval_text_contains_shape_and_body() -> None:
    source = FIXTURE.read_text()
    result = parse_python_entities("agent/example.py", source)
    helper = next(entity for entity in result.entities if entity.qualified_name == "helper")
    retrieval_text = build_retrieval_text(helper)

    assert "kind=function" in retrieval_text
    assert "qualified_name=helper" in retrieval_text
    assert "docstring=Normalize a value." in retrieval_text
    assert "body=def helper(value):" in retrieval_text
