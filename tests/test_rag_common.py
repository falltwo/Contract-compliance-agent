"""rag_common 單元測試（純函式，不打 API）。"""
import pytest

from rag_common import chunk_text, format_context, stable_id


class TestChunkText:
    def test_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_smaller_than_chunk_size(self):
        text = "一段短文字"
        out = chunk_text(text, chunk_size=900, overlap=150)
        assert len(out) == 1
        assert out[0] == text.strip()

    def test_chunk_size_and_overlap(self):
        # 造一段超過 chunk_size 的文字
        block = "a" * 500 + "\n\n" + "b" * 500
        out = chunk_text(block, chunk_size=400, overlap=50)
        assert len(out) >= 2

    def test_chunk_size_must_gt_overlap(self):
        with pytest.raises(ValueError, match="chunk_size"):
            chunk_text("x", chunk_size=100, overlap=100)


class TestStableId:
    def test_deterministic(self):
        a = stable_id("s", 0, "text")
        b = stable_id("s", 0, "text")
        assert a == b

    def test_different_input_different_id(self):
        a = stable_id("s", 0, "text1")
        b = stable_id("s", 0, "text2")
        assert a != b

    def test_length(self):
        uid = stable_id("source", 3, "hello")
        assert len(uid) == 32


class TestFormatContext:
    def test_empty_matches(self):
        ctx, sources, cleaned = format_context([])
        assert ctx == ""
        assert sources == []
        assert cleaned == []

    def test_single_match(self):
        matches = [
            {"metadata": {"source": "a.txt", "chunk_index": 0, "text": "內容一"}},
        ]
        ctx, sources, cleaned = format_context(matches)
        assert "a.txt#chunk0" in ctx
        assert "[a.txt#chunk0]" in ctx
        assert sources == ["a.txt#chunk0"]
        assert len(cleaned) == 1
        assert cleaned[0]["tag"] == "a.txt#chunk0"
        assert cleaned[0]["text"] == "內容一"

    def test_skips_empty_text(self):
        matches = [
            {"metadata": {"source": "a.txt", "chunk_index": 0, "text": ""}},
        ]
        ctx, sources, cleaned = format_context(matches)
        assert sources == []
        assert cleaned == []
