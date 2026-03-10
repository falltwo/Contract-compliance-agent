"""
Eval 批次執行：讀取 eval_set.json，對每題呼叫 route_and_answer，寫入結果並計算核心指標。

使用方式（專案根目錄）：
  uv run python eval/run_eval.py
  uv run python eval/run_eval.py --groq                    # 使用 Groq（需設 GROQ_API_KEY）
  uv run python eval/run_eval.py --groq --delay-sec 3      # Groq 限流時：每題間隔 3 秒
  uv run python eval/run_eval.py --eval-set ... --out-dir ...
遇 429／rate limit 會自動等待 --rate-limit-wait 秒後重試（預設 60 秒，最多 3 次）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 專案根目錄加入 path，才能 import agent_router
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root / ".env")

from agent_router import route_and_answer


def load_eval_set(path: Path) -> list[dict]:
    """載入題集 JSON。"""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("eval_set 應為 JSON 陣列")
    return data


def _is_rate_limit_error(e: Exception) -> bool:
    """是否為 API 限流錯誤（429 / rate limit）。"""
    s = str(e).upper()
    return "429" in s or "RATE_LIMIT" in s or "RESOURCE_EXHAUSTED" in s or "TOO_MANY_REQUESTS" in s


def _parse_retry_after_sec(e: Exception, default_sec: float = 60.0, max_sec: float = 600.0) -> float:
    """從錯誤訊息解析「請在 X 秒/分 後重試」，若無則回傳 default_sec。"""
    s = str(e)
    # Groq: "Please try again in 8m47.04s"
    m = re.search(r"try again in (\d+)m(\d*\.?\d*)s", s, re.IGNORECASE)
    if m:
        minutes = int(m.group(1))
        seconds = float(m.group(2) or 0)
        total = minutes * 60 + seconds
        return min(max(1, total), max_sec)
    # 僅秒數: "retry in 120s"
    m = re.search(r"retry in (\d+)\s*s", s, re.IGNORECASE)
    if m:
        return min(max(1, float(m.group(1))), max_sec)
    return default_sec


def run_one(
    item: dict,
    *,
    top_k: int = 5,
    rate_limit_retry_sec: float = 60.0,
    rate_limit_max_retries: int = 3,
) -> dict:
    """跑一題，回傳一筆結果（含 predicted_tool, latency_sec, success, error 等）。
    若遇 429／rate limit 會自動等待後重試。
    """
    q = (item.get("question") or "").strip()
    if not q:
        return {
            "id": item.get("id", ""),
            "question": "",
            "expected_tool": item.get("expected_tool"),
            "predicted_tool": None,
            "latency_sec": 0,
            "source_count": 0,
            "answer_len": 0,
            "answer": None,
            "success": False,
            "error": "empty question",
        }
    last_error: Exception | None = None
    for attempt in range(rate_limit_max_retries):
        t0 = time.perf_counter()
        try:
            answer, sources, chunks, tool_name, extra = route_and_answer(
                question=q,
                top_k=top_k,
                history=[],
                strict=False,
                chat_id=None,
            )
            latency = time.perf_counter() - t0
            return {
                "id": item.get("id", ""),
                "question": q,
                "expected_tool": item.get("expected_tool"),
                "predicted_tool": tool_name,
                "latency_sec": round(latency, 3),
                "source_count": len(sources),
                "answer_len": len(answer or ""),
                "answer": (answer or "").strip(),
                "success": True,
                "error": None,
            }
        except Exception as e:
            last_error = e
            latency = time.perf_counter() - t0
            if _is_rate_limit_error(e) and attempt < rate_limit_max_retries - 1:
                wait_sec = _parse_retry_after_sec(e, default_sec=rate_limit_retry_sec)
                print(f"  [限流 429] 等待 {int(wait_sec)} 秒後重試（第 {attempt + 1}/{rate_limit_max_retries} 次）…", flush=True)
                time.sleep(wait_sec)
                continue
            return {
                "id": item.get("id", ""),
                "question": q,
                "expected_tool": item.get("expected_tool"),
                "predicted_tool": None,
                "latency_sec": round(latency, 3),
                "source_count": 0,
                "answer_len": 0,
                "answer": None,
                "success": False,
                "error": str(e),
            }
    return {
        "id": item.get("id", ""),
        "question": q,
        "expected_tool": item.get("expected_tool"),
        "predicted_tool": None,
        "latency_sec": 0,
        "source_count": 0,
        "answer_len": 0,
        "answer": None,
        "success": False,
        "error": str(last_error) if last_error else "rate limit retries exhausted",
    }


def compute_metrics(results: list[dict]) -> dict:
    """從結果列表計算核心指標。"""
    n = len(results)
    if n == 0:
        return {
            "routing_accuracy": None,
            "routing_accuracy_n": 0,
            "tool_success_rate": None,
            "latency_p50_sec": None,
            "latency_p95_sec": None,
            "total": 0,
        }

    # Tool call 成功率（無 exception）
    success_count = sum(1 for r in results if r.get("success"))
    tool_success_rate = round(success_count / n * 100, 1) if n else None

    # Latency P50 / P95（只算成功的）
    latencies = sorted([r["latency_sec"] for r in results if r.get("success") and r.get("latency_sec") is not None])
    if not latencies:
        latency_p50_sec = None
        latency_p95_sec = None
    else:
        idx_p50 = max(0, int(len(latencies) * 0.5) - 1)
        idx_p95 = max(0, int(len(latencies) * 0.95) - 1)
        latency_p50_sec = round(latencies[idx_p50], 3)
        latency_p95_sec = round(latencies[idx_p95], 3)

    # Routing 準確率（只算有 expected_tool 的題目）
    with_expected = [r for r in results if r.get("expected_tool")]
    if not with_expected:
        routing_accuracy = None
        routing_accuracy_n = 0
    else:
        correct = sum(1 for r in with_expected if r.get("predicted_tool") == r.get("expected_tool"))
        routing_accuracy = round(correct / len(with_expected) * 100, 1)
        routing_accuracy_n = len(with_expected)

    return {
        "routing_accuracy": routing_accuracy,
        "routing_accuracy_n": routing_accuracy_n,
        "tool_success_rate": tool_success_rate,
        "latency_p50_sec": latency_p50_sec,
        "latency_p95_sec": latency_p95_sec,
        "total": n,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run eval set and compute metrics")
    parser.add_argument("--groq", action="store_true", help="Eval 使用 Groq（需設 GROQ_API_KEY）")
    parser.add_argument("--eval-set", type=Path, default=_root / "eval" / "eval_set.json", help="題集 JSON 路徑")
    parser.add_argument("--out-dir", type=Path, default=_root / "eval" / "runs", help="結果輸出目錄")
    parser.add_argument("--top-k", type=int, default=5, help="RAG top_k")
    parser.add_argument("--delay-sec", type=float, default=0, help="每題之間延遲秒數（Groq 限流時建議 2～5）")
    parser.add_argument("--rate-limit-wait", type=float, default=60, help="遇 429 時重試前等待秒數")
    args = parser.parse_args()

    if args.groq:
        os.environ["EVAL_USE_GROQ"] = "1"
        print("已設定 EVAL_USE_GROQ=1，Eval 將使用 Groq。")

    eval_set_path = args.eval_set
    if not eval_set_path.is_absolute():
        eval_set_path = _root / eval_set_path
    if not eval_set_path.exists():
        print(f"錯誤：找不到題集 {eval_set_path}")
        sys.exit(1)

    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = _root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_path = out_dir / f"run_{run_id}_results.jsonl"
    metrics_path = out_dir / f"run_{run_id}_metrics.json"

    items = load_eval_set(eval_set_path)
    print(f"題集共 {len(items)} 題，開始執行…")

    results: list[dict] = []
    delay_sec = max(0.0, args.delay_sec)
    if args.groq and delay_sec == 0:
        print("提示：使用 --groq 時若遇限流可加 --delay-sec 2 或 5 降低請求頻率。")
    for i, item in enumerate(items):
        r = run_one(
            item,
            top_k=args.top_k,
            rate_limit_retry_sec=args.rate_limit_wait,
        )
        results.append(r)
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        status = "✓" if r.get("success") else "✗"
        print(f"  [{i+1}/{len(items)}] {status} {r.get('id')} → {r.get('predicted_tool')} ({r.get('latency_sec')}s)")
        if delay_sec > 0 and i < len(items) - 1:
            time.sleep(delay_sec)

    metrics = compute_metrics(results)
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print("\n--- 核心指標 ---")
    print(f"  總題數: {metrics['total']}")
    if metrics.get("routing_accuracy") is not None:
        print(f"  Routing 準確率: {metrics['routing_accuracy']}% (n={metrics['routing_accuracy_n']})")
    print(f"  Tool 成功率: {metrics.get('tool_success_rate')}%")
    if metrics.get("latency_p50_sec") is not None:
        print(f"  Latency P50: {metrics['latency_p50_sec']}s")
    if metrics.get("latency_p95_sec") is not None:
        print(f"  Latency P95: {metrics['latency_p95_sec']}s")
    print(f"\n結果: {results_path}")
    print(f"指標: {metrics_path}")


if __name__ == "__main__":
    main()
