#!/usr/bin/env bash
# ──────────────────────────────────────────────────
# preload_models.sh — 啟動時預載 Ollama 模型並常駐記憶體
#
# 用法：
#   bash scripts/preload_models.sh          # 使用 .env 設定
#   OLLAMA_CHAT_MODEL=gemma3:27b bash scripts/preload_models.sh
#
# keep_alive=-1 表示模型載入後永遠不自動卸載。
# ──────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 讀取 .env（若存在）— 只匯入安全的 KEY=VALUE 行，跳過含正則的行
if [[ -f "$PROJECT_DIR/.env" ]]; then
  while IFS='=' read -r key value; do
    # 跳過註解、空行、含特殊字元的行
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    # 去掉首尾空白
    key="$(echo "$key" | xargs)"
    value="$(echo "$value" | xargs)"
    # 只匯入我們需要的變數
    case "$key" in
      OLLAMA_*|CHAT_PROVIDER|EMBEDDING_PROVIDER)
        export "$key=$value" 2>/dev/null || true
        ;;
    esac
  done < "$PROJECT_DIR/.env"
fi

OLLAMA_BASE="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
CHAT_MODEL="${OLLAMA_CHAT_MODEL:-gemma3:27b}"
EMBED_MODEL="${OLLAMA_EMBED_MODEL:-snowflake-arctic-embed2:568m}"

# 從 .env 收集所有 OLLAMA_*_MODEL 的值，去重
declare -A MODELS_MAP
MODELS_MAP["$CHAT_MODEL"]=1
MODELS_MAP["$EMBED_MODEL"]=1

# 讀取各階段 model override
for VAR in OLLAMA_ROUTER_MODEL OLLAMA_RAG_REWRITE_MODEL OLLAMA_RAG_AUX_QUERY_MODEL \
           OLLAMA_RAG_PACKAGE_MODEL OLLAMA_RAG_GENERATE_MODEL OLLAMA_RESEARCH_GENERATE_MODEL \
           OLLAMA_ANALYSIS_MODEL OLLAMA_CONTRACT_RISK_GENERATE_MODEL OLLAMA_CONTRACT_RISK_VERIFY_MODEL; do
  VALUE="${!VAR:-}"
  if [[ -n "$VALUE" ]]; then
    MODELS_MAP["$VALUE"]=1
  fi
done

echo "🚀 Ollama base: $OLLAMA_BASE"
echo "📦 Models to preload (keep_alive=-1):"

for MODEL in "${!MODELS_MAP[@]}"; do
  echo "   → $MODEL"
done

echo ""

for MODEL in "${!MODELS_MAP[@]}"; do
  echo "⏳ Loading $MODEL ..."
  # 送一個空的 generate 請求，只載入模型不生成
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "$OLLAMA_BASE/api/generate" \
    -d "{\"model\": \"$MODEL\", \"prompt\": \"\", \"keep_alive\": -1}" \
    --max-time 120 2>/dev/null || echo "000")

  if [[ "$HTTP_CODE" == "200" ]]; then
    echo "✅ $MODEL loaded and pinned in memory"
  else
    echo "⚠️  $MODEL returned HTTP $HTTP_CODE (may still be loading...)"
  fi
done

echo ""
echo "📊 Currently loaded models:"
curl -s "$OLLAMA_BASE/api/ps" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(could not query)"
echo ""
echo "✅ Preload complete!"
