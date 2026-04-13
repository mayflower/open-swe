from pathlib import Path

from agent.repo_memory.parsing.go_parser import parse_go_entities


FIXTURE = Path(__file__).parent / "fixtures" / "go_sample_module.go.txt"


def test_go_extraction_fixture() -> None:
    entities = parse_go_entities("agent/widget.go", FIXTURE.read_text())
    assert [entity.qualified_name for entity in entities] == [
        "WidgetService",
        "WidgetService.Render",
        "Helper",
    ]
