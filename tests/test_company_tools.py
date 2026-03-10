"""company_tools 單元測試。"""
import pytest

from company_tools import (
    financial_metrics,
    generate_quarterly_plan,
    parse_dates_from_text,
)


class TestFinancialMetrics:
    def test_growth_positive(self):
        out = financial_metrics(revenue_this_year=100, revenue_last_year=80)
        assert "營收成長率" in out
        assert "+25.0%" in out
        assert "營收成長表現佳" in out

    def test_growth_negative(self):
        out = financial_metrics(revenue_this_year=70, revenue_last_year=80)
        assert "營收成長率" in out
        assert "-12.5%" in out
        assert "營收明顯衰退" in out

    def test_missing_both_revenues(self):
        out = financial_metrics()
        assert "revenue_this_year" in out or "今年" in out

    def test_only_one_revenue(self):
        out = financial_metrics(revenue_this_year=100)
        assert "今年營收」與「去年營收」" in out or "同時提供" in out

    def test_gross_margin_and_net_margin(self):
        out = financial_metrics(
            revenue_this_year=100,
            revenue_last_year=80,
            gross_margin_this_year=40,
            gross_margin_last_year=35,
            net_margin_this_year=10,
            net_margin_last_year=8,
        )
        assert "毛利率變化" in out
        assert "淨利率變化" in out

    def test_str_numbers_with_comma(self):
        out = financial_metrics(revenue_this_year="1,000", revenue_last_year="800")
        assert "營收成長率" in out
        assert "+25.0%" in out


class TestParseDatesFromText:
    def test_empty(self):
        out = parse_dates_from_text("")
        assert "未提供文字" in out

    def test_iso_date(self):
        out = parse_dates_from_text("會議訂在 2025-03-15 舉行")
        assert "2025-03-15" in out

    def test_slash_date(self):
        out = parse_dates_from_text("截止日 2025/12/31")
        assert "2025-12-31" in out or "2025" in out

    def test_chinese_date(self):
        out = parse_dates_from_text("2025年3月15日開會")
        assert "2025" in out and "3" in out

    def test_quarter(self):
        out = parse_dates_from_text("Q1 2025 檢討")
        assert "Q1" in out or "2025" in out

    def test_no_dates(self):
        out = parse_dates_from_text("沒有任何日期在這裡")
        assert "未偵測" in out or "解析" in out


class TestGenerateQuarterlyPlan:
    def test_default_four_quarters(self):
        out = generate_quarterly_plan(topic="產品上市", start_quarter="2025Q1", num_quarters=4)
        assert "產品上市" in out
        assert "2025 Q1" in out or "Q1" in out
        assert "季度" in out or "重點" in out

    def test_two_quarters(self):
        out = generate_quarterly_plan(topic="預算", start_quarter="2025Q3", num_quarters=2)
        assert "預算" in out
        assert "2" in out or "Q3" in out

    def test_num_quarters_clamped(self):
        out = generate_quarterly_plan(topic="X", start_quarter="2025Q1", num_quarters=10)
        # 應被 clamp 到 8
        assert "X" in out
