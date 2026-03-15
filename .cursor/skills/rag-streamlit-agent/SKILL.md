---
name: rag-streamlit-agent
description: Use when the user asks about RAG, retrieval-augmented generation, AI agents, agent routing, Streamlit apps, LangGraph, Pinecone, Gemini, knowledge base QA, tool routing, 分析這個專案, 分析專案, or building chat/QA demos. Follow this project's architecture and conventions.
---

# RAG + Streamlit + Agent（本專案慣例）

## When to Use

- User mentions **RAG**, **retrieval-augmented generation**, **knowledge base**, **vector search**, or **document QA**
- User mentions **AI agent**, **agent routing**, **tool selection**, **multi-tool**, or **LLM tool use**
- User mentions **Streamlit** for chat UI, file upload, or dashboards
- User asks to add a new tool, new expert agent, or change the router
- User asks about **LangGraph**, **StateGraph**, **Pinecone**, **Gemini**, or **ingest pipeline**

## Project Architecture (Brief)

- **LLM / Embedding**: Google Gemini (`gemini-3.1-flash-lite-preview`, `gemini-embedding-001`)
- **Vector DB**: Pinecone (index name from `PINECONE_INDEX`, default `agent-index`)
- **RAG flow**: LangGraph `StateGraph(RAGState)` in `rag_graph.py` — retrieve (with optional dedup, MMR, rerank) → generate
- **Agent**: `agent_router.py` — decides tool (rag_search, research, web_search, scrape_url, firecrawl_search, create_chart, expert_agents, etc.) via LLM; then executes and returns answer + sources
- **Frontend**: `streamlit_app.py` — multi-chat, strict/non-strict mode, upload & ingest, display chunks and ECharts
- **Ingest**: `rag_ingest.py` — scan `data/` (txt/md/pdf), chunk, embed with Gemini, upsert to Pinecone; `sources_registry.py` for source list
- **Extras**: `firecrawl_tools.py`, `echarts_tools.py`, `expert_agents.py` (e.g. financial_report_agent, esg_agent)

## Instructions

1. **RAG changes**  
   - Keep retrieve → generate in `rag_graph.py`. Use `retrieve_only()` for expert agents.  
   - Env: `RAG_INTERNAL_TOP_K`, `RAG_RERANK_TOP_N`, `RAG_DEDUP_ENABLED`, `RAG_MMR_LAMBDA`, `RAG_MIN_SCORE`, `PINECONE_INDEX`, `RAG_DATA_DIR`.

2. **New agent tool**  
   - In `agent_router.py`: add tool name and description to the router prompt and to the list of tools; implement execution (call RAG, external API, or helper) and return `(answer, sources, chunks, tool_name, extra)`.

3. **New expert agent**  
   - In `expert_agents.py`: define function that uses `retrieve_only()` from `rag_graph` and a dedicated system prompt; in `agent_router._decide_tool` add option (e.g. 16, 17) and in execution branch call the new agent.

4. **Streamlit UI**  
   - Keep chat in `st.session_state`; use `streamlit_echarts` for charts; optional ECharts MCP client for PNG. Follow existing patterns in `streamlit_app.py` for strict mode, source list, and ingest.

5. **Environment**  
   - Prefer `.env` and `os.getenv()` for keys and flags (`GEMINI_API_KEY`, `PINECONE_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `USE_ECHARTS_MCP`, etc.).

## Conventions

- **State**: Use `RAGState` TypedDict for graph state; include `messages`, `context`, `answer`, `sources`, etc. as already defined.
- **Return shape**: Agent tools return `(answer, sources, chunks, tool_name, extra)` for consistent handling in router and Streamlit.
- **Chunking / ingest**: Use existing pipeline in `rag_ingest.py`; respect `RAG_DATA_DIR` and `sources_registry`.
- **Code style**: Same as rest of project — type hints, env for config, clear separation between graph, router, and UI.
