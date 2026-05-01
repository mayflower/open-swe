from agent.utils.jira_adf import adf_to_text, text_to_adf


def test_adf_to_text_handles_paragraph_and_hard_break() -> None:
    text = adf_to_text(
        {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "First line"},
                        {"type": "hardBreak"},
                        {"type": "text", "text": "Second line"},
                    ],
                }
            ],
        }
    )

    assert text == "First line\nSecond line"


def test_adf_to_text_handles_bullet_list() -> None:
    text = adf_to_text(
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "One"}]}],
                },
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Two"}]}],
                },
            ],
        }
    )

    assert text == "- One\n- Two"


def test_adf_to_text_flattens_ordered_list_readably() -> None:
    text = adf_to_text(
        {
            "type": "orderedList",
            "content": [
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "First"}]}],
                },
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Second"}]}],
                },
            ],
        }
    )

    assert text == "1. First\n2. Second"


def test_adf_to_text_formats_code_blocks() -> None:
    text = adf_to_text(
        {
            "type": "codeBlock",
            "content": [{"type": "text", "text": "print('hi')"}],
        }
    )

    assert text == "```\nprint('hi')\n```"


def test_adf_to_text_formats_mentions_readably() -> None:
    text = adf_to_text(
        {
            "type": "paragraph",
            "content": [
                {"type": "mention", "attrs": {"text": "@Ada"}},
                {"type": "text", "text": " please review"},
            ],
        }
    )

    assert text == "@Ada please review"


def test_adf_to_text_preserves_link_mark_text_readably() -> None:
    text = adf_to_text(
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": "OpenAI",
                    "marks": [{"type": "link", "attrs": {"href": "https://openai.com"}}],
                }
            ],
        }
    )

    assert text == "OpenAI (https://openai.com)"


def test_adf_to_text_ignores_unknown_nodes_without_raising() -> None:
    text = adf_to_text(
        {
            "type": "doc",
            "content": [
                {"type": "mystery", "content": [{"type": "text", "text": "hidden"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "visible"}]},
            ],
        }
    )

    assert "visible" in text


def test_text_to_adf_returns_valid_doc() -> None:
    adf = text_to_adf("Hello world")

    assert adf["type"] == "doc"
    assert adf["version"] == 1
    assert isinstance(adf["content"], list)
    assert adf["content"]


def test_text_to_adf_round_trips_simple_paragraphs_and_bullets() -> None:
    source = "Hello world\nwith context\n\n- one\n- two"

    assert adf_to_text(text_to_adf(source)) == source


def test_text_to_adf_preserves_bare_urls_as_link_marks() -> None:
    adf = text_to_adf("Visit https://example.com/docs")

    paragraph = adf["content"][0]
    link_nodes = [node for node in paragraph["content"] if node.get("marks")]
    assert link_nodes
    assert link_nodes[0]["marks"][0]["type"] == "link"
