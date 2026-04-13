from pathlib import Path

from agent.repo_memory.parsing.typescript_parser import parse_typescript_entities


FIXTURE = Path(__file__).parent / "fixtures" / "typescript_sample_module.ts.txt"


def test_typescript_extraction_fixture() -> None:
    entities = parse_typescript_entities("agent/widget.ts", FIXTURE.read_text())
    assert [entity.qualified_name for entity in entities] == [
        "WidgetService",
        "WidgetService.render",
        "helper",
    ]
