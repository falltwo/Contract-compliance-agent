"""rag_graph 內部純函式單元測試（dedup、MMR、format 委派給 common）。"""
import pytest

from rag_graph import _dedup_matches, _get_text, _mmr_select, _normalize_text_for_dedup, _text_similarity


def _m(text: str, score: float = 0.9) -> dict:
    """建一個 match  dict。"""
    return {"metadata": {"text": text, "source": "x", "chunk_index": 0}, "score": score}


class TestNormalizeTextForDedup:
    def test_strip_and_collapse_space(self):
        assert _normalize_text_for_dedup("  a  b  c  ") == "a b c"


class TestTextSimilarity:
    def test_empty(self):
        assert _text_similarity("", "x") == 0.0
        assert _text_similarity("x", "") == 0.0

    def test_identical(self):
        assert _text_similarity("abc", "abc") == 1.0

    def test_different(self):
        assert _text_similarity("abc", "xyz") < 0.5


class TestGetText:
    def test_from_metadata(self):
        assert _get_text(_m("hello")) == "hello"

    def test_missing_metadata(self):
        assert _get_text({}) == ""
        assert _get_text({"metadata": {}}) == ""


class TestDedupMatches:
    def test_empty(self):
        assert _dedup_matches([]) == []

    def test_deduplicate_same_hash(self):
        m1 = _m("完全相同內容")
        m2 = _m("完全相同內容")
        out = _dedup_matches([m1, m2])
        assert len(out) == 1

    def test_keep_different(self):
        out = _dedup_matches([_m("A"), _m("B"), _m("C")])
        assert len(out) == 3

    def test_skip_empty_text(self):
        out = _dedup_matches([_m(""), _m("有內容")])
        assert len(out) == 1
        assert _get_text(out[0]) == "有內容"


class TestMmrSelect:
    def test_empty(self):
        assert _mmr_select([], top_n=5) == []

    def test_top_n_zero(self):
        assert _mmr_select([_m("a")], top_n=0) == []

    def test_select_top_n(self):
        matches = [_m("a", 0.9), _m("b", 0.8), _m("c", 0.7), _m("d", 0.6)]
        out = _mmr_select(matches, top_n=2, lambda_=0.6)
        assert len(out) == 2
        # 第一個應是 score 最高的
        assert out[0]["metadata"]["text"] == "a"

    def test_lambda_one_prefers_relevance(self):
        # λ=1 時只依 score，不考慮 diversity
        matches = [_m("a", 0.95), _m("a", 0.9), _m("b", 0.7)]  # 兩個 a 很相似
        out = _mmr_select(matches, top_n=2, lambda_=1.0)
        assert len(out) == 2
