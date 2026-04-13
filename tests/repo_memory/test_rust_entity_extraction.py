from pathlib import Path

from agent.repo_memory.parsing.rust_parser import parse_rust_entities


FIXTURE = Path(__file__).parent / "fixtures" / "rust_sample_module.rs.txt"


def test_rust_extraction_fixture() -> None:
    entities = parse_rust_entities("agent/widget.rs", FIXTURE.read_text())
    assert [entity.qualified_name for entity in entities] == [
        "Renderer",
        "Renderer.render",
        "WidgetService",
        "helper",
    ]
