import os


os.environ.setdefault("PROJECT_NAME", "langboard")


from langboard_shared.domain.contracts.content_block_migration import (  # noqa: E402
    extract_legacy_blocks,
    extract_persistable_description_blocks,
    normalize_engine,
)
from langboard_shared.domain.contracts.content_blocks import MAX_SOURCE_CHARS, ContentBlockType, build_payload


class TestNormalizeEngine:
    def test_aliases(self):
        assert normalize_engine("Mermaid") == "mermaid"
        assert normalize_engine("PlantUml") == "plantuml"
        assert normalize_engine("Graphviz") == "graphviz"
        assert normalize_engine("Flowchart") == "flowchart"
        assert normalize_engine("mermaid") == "mermaid"

    def test_unknown(self):
        assert normalize_engine("d3") is None
        assert normalize_engine(None) is None


class TestExtractLegacyBlocks:
    def test_code_fence(self):
        blocks = extract_legacy_blocks("설명\n\n```python\nprint('hi')\n```\n\n끝")
        assert [b["block_type"] for b in blocks] == ["rich_text", "code", "rich_text"]
        assert blocks[1]["payload"]["language"] == "python"
        assert blocks[1]["payload"]["source"] == "print('hi')\n"

    def test_drawing_span(self):
        blocks = extract_legacy_blocks("$$Mermaid\ngraph TD; A-->B\n$$")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == "diagram"
        assert blocks[0]["payload"]["engine"] == "mermaid"
        assert blocks[0]["payload"]["view_mode"] == "both"

    def test_inner_span_stays_code(self):
        blocks = extract_legacy_blocks("```\n$$Mermaid\nfake\n$$\n```")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == "code"

    def test_plain_text_yields_empty(self):
        assert extract_legacy_blocks("그냥 텍스트만") == []

    def test_view_mode_meta(self):
        blocks = extract_legacy_blocks('$$Mermaid mode="code"\ngraph TD; A-->B\n$$')
        assert blocks[0]["payload"]["view_mode"] == "source"

    def test_every_drawing_mode_is_valid_for_persistence(self):
        for mode, expected in (("code", "source"), ("source", "source"), ("image", "rendered"), ("rendered", "rendered"), ("both", "both")):
            blocks = extract_legacy_blocks(f'$$Mermaid mode="{mode}"\ngraph TD; A-->B\n$$')
            assert blocks[0]["payload"]["view_mode"] == expected
            assert build_payload(ContentBlockType.DIAGRAM, blocks[0]["payload"])["view_mode"] == expected

    def test_order_preserved(self):
        markdown = "intro\n\n```js\ncode1()\n```\n\nmid\n\n$$Graphviz\ndigraph {a}\n$$\n\noutro"
        blocks = extract_legacy_blocks(markdown)
        assert [b["block_type"] for b in blocks] == ["rich_text", "code", "rich_text", "diagram", "rich_text"]

    def test_ui_parity_sample(self):
        """UI contentBlockSerializer와 동일 세그먼트 계약 유지 확인."""
        markdown = "본문 텍스트\n\n```python\nprint('sync')\n```\n\n$$Mermaid\ngraph TD; A-->B\n$$"
        blocks = extract_legacy_blocks(markdown)
        assert [b["block_type"] for b in blocks] == ["rich_text", "code", "diagram"]
        assert blocks[1]["payload"]["source"] == "print('sync')\n"
        assert blocks[2]["payload"]["engine"] == "mermaid"


class TestPersistableDescriptionBlocks:
    def test_valid_source_at_limit_keeps_structured_reader(self):
        body = "```text\n" + "x" * (MAX_SOURCE_CHARS - 1) + "\n```"
        blocks = extract_persistable_description_blocks(body)
        assert len(blocks) == 1
        assert len(blocks[0]["payload"]["source"]) == MAX_SOURCE_CHARS

    def test_oversized_code_or_diagram_keeps_complete_legacy_reader(self):
        source = "x" * MAX_SOURCE_CHARS + "\n"
        for body in ("```text\n" + source + "```", "$$Mermaid\n" + source + "$$"):
            assert extract_legacy_blocks(body)
            assert extract_persistable_description_blocks(body) == []

    def test_oversized_surrounding_text_never_leaves_partial_blocks(self):
        text = "x" * (MAX_SOURCE_CHARS + 1)
        for body in (text + "\n```text\ncode\n```", "```text\ncode\n```\n" + text):
            assert len(extract_legacy_blocks(body)) == 2
            assert extract_persistable_description_blocks(body) == []

    def test_legacy_fence_label_keeps_legacy_reader(self):
        body = "```한글\nsource\n```"
        assert extract_legacy_blocks(body)[0]["payload"]["language"] == "한글"
        assert extract_persistable_description_blocks(body) == []
