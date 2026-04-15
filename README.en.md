# Autonomous Contract Risk Assessment Agent System

> AI-powered first-pass contract review with RAG, legal grounding, and deployable Streamlit / FastAPI + Vue interfaces.

[![Version](https://img.shields.io/badge/version-v1.0.0-2ea44f)](https://github.com/falltwo/Contract-compliance-agent)
[![License](https://img.shields.io/github/license/falltwo/Contract-compliance-agent)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![README](https://img.shields.io/badge/README-English-0F766E)](README.en.md)
[![Stars](https://img.shields.io/github/stars/falltwo/Contract-compliance-agent?style=social)](https://github.com/falltwo/Contract-compliance-agent/stargazers)

This project is built for legal, procurement, internal control, and AI product teams that need a practical contract-review assistant. It combines contract risk detection, legal reference lookup, knowledge-base Q&A, and evaluation workflows into a single entry point so teams can complete a first-pass contract review in minutes with traceable citations.

For Traditional Chinese documentation, see [README.md](README.md).

## Why This Project Matters

- ⚡ Shortens first-pass contract review from manual clause-by-clause reading to upload-and-review or ask-and-answer workflows.
- ⚖️ Goes beyond summarization by combining risk analysis with legal reference lookup to surface high-risk clauses faster.
- 🔎 Grounds answers in retrieved knowledge, supports citations and strict mode, and reduces hallucination risk.
- 🧭 Offers both `Streamlit` and `FastAPI + Vue` paths, making it usable for demos, internal pilots, and deployable services.
- 📊 Includes Eval datasets and repeatable validation flows so quality can be measured, not just demonstrated.

## Who It Is For

| Audience | Best Use Case |
|----------|---------------|
| Legal / compliance teams | Find high-risk clauses quickly, compare legal references, and produce a first-pass review |
| Internal AI teams | Extend an existing RAG + agent architecture for more contract types and tools |
| PoC / competition teams | Demo a full workflow with upload, retrieval, review, and validation in a short time |
| Platform / IT teams | Deploy the app internally with `FastAPI + Vue` on DGX or other Linux infrastructure |

## Features

- 📄 Upload contracts and documents in `.txt`, `.md`, `.pdf`, and `.docx`
- 🧠 LangGraph-powered RAG workflow with multi-turn chat and knowledge-base Q&A
- 🔀 Agent Router that selects between RAG, contract review, legal lookup, and expert flows
- ⚖️ Contract risk assessment plus legal lookup with integrated search and AI self-check
- 🔍 Hybrid retrieval combining vector search with BM25 for stronger precision
- 🧪 Built-in Eval datasets, batch execution, and result output for tracking regressions
- 🚀 Both a Streamlit demo UI and a Vue Web MVP
- 🖥️ Local-model support through Ollama and persistent DGX deployment support

## System Overview

### Request Flow

After a user asks a question, the system identifies intent, routes the request to the right tool or expert flow, and returns an answer with citations and risk explanations.

![System flow](assets/flowchart.png)

### Architecture

The main modules cover frontend interfaces, the Agent Router, RAG retrieval, legal lookup, document ingest, and Eval validation.

![System architecture](assets/architecture-diagram.png)

## Quick Start

### Prerequisites

| Item | Notes |
|------|-------|
| Python | `3.13+` |
| `uv` | Python dependency and environment manager |
| Pinecone | Required for vector index storage and retrieval |
| LLM Provider | Choose one: Google Gemini or Ollama |
| Tavily | Needed for legal / web lookup workflows |
| Node.js | Recommended if you want to run the Vue frontend |

### 1. Create Environment Variables

The most common first-run blocker is environment setup. Start by copying `.env.example` and then fill in the minimum required values.

**macOS / Linux**

```bash
cp .env.example .env
```

**PowerShell**

```powershell
Copy-Item .env.example .env
```

### 2. Required and Common Settings

| Variable | Required | Purpose |
|----------|----------|---------|
| `PINECONE_API_KEY` | Yes | Pinecone API key |
| `PINECONE_INDEX` | Yes | Pinecone index name |
| `CHAT_PROVIDER` | Yes | `gemini` or `ollama` |
| `GOOGLE_API_KEY` | Required for Gemini | Cloud chat model access |
| `EMBEDDING_PROVIDER` | Recommended | `gemini` or `ollama` |
| `OLLAMA_CHAT_MODEL` | Required for Ollama | Local chat model name |
| `OLLAMA_EMBED_MODEL` | Recommended for Ollama | Local embedding model name |
| `TAVILY_API_KEY` | Optional | Enables legal / web search |

If you want to use the project's recommended local-model setup, use this `.env` configuration:

```env
CHAT_PROVIDER=ollama
OLLAMA_CHAT_MODEL=gemma3:27b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBED_MODEL=snowflake-arctic-embed2:568m
```

### 3. Install Dependencies

```bash
uv sync
```

### 4. Ingest Sample Data

The repository already includes `data/sample.txt` and `data/sample_contract_NDA.txt`, so you do not need to prepare your own files for the first run.

```bash
uv run rag_ingest.py
```

### 5. Launch the Fastest Success Path: Streamlit

```bash
uv run streamlit run streamlit_app.py
```

Then open `http://localhost:8501` and you can:

- upload contracts and ingest them for the current conversation
- ask questions against the knowledge base
- trigger one-click contract review from the sidebar
- enable legal-lookup review when `TAVILY_API_KEY` is configured

## 5-Minute First Run

If you want the shortest path to a successful demo, follow this exact flow:

1. Copy `.env.example` to `.env`
2. Fill in Pinecone and one model configuration
3. Run `uv sync`
4. Run `uv run rag_ingest.py`
5. Run `uv run streamlit run streamlit_app.py`
6. Ask: `Please review the risk clauses in this contract`

If `TAVILY_API_KEY` is configured, you can also ask:

```text
Assess the risks in this contract and find the related legal provisions
```

## Web Mode: FastAPI + Vue

If you want to use the project as an API service or internal web application, this is the recommended path.

### Start the Backend API

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Available endpoints:

- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

### Start the Vue Frontend

```bash
cd web
npm ci
npm run dev
```

Available routes:

- Chat: `http://localhost:5173/chat`
- Upload: `http://localhost:5173/upload`
- Sources: `http://localhost:5173/sources`
- Admin: `http://localhost:5173/admin`
- Eval: `http://localhost:5173/eval`

## Deployment

### DGX / Internal Linux Deployment

The project includes `systemd` templates and deployment scripts for persistent use on DGX or other internal Linux hosts.

```bash
bash scripts/install_dgx_services.sh
bash scripts/deploy_contract_agent.sh
```

Default services:

| Service | Default Port | Purpose |
|---------|--------------|---------|
| `contract-agent-api.service` | `8000` | FastAPI backend |
| `contract-agent-web.service` | `4173` | Vue preview service after build |

Deployment notes:

- The Vue production app uses `VITE_API_BASE_URL` first
- If it is not set, the frontend derives the API base as `current-browser-host + :8000`
- To allow LAN, Tailscale IP, and localhost cross-port access, configure `API_CORS_ORIGIN_REGEX`

## Usage

### Contract Review

The two fastest ways to use the contract-review flow are:

1. Click the sidebar actions in Streamlit for one-click review
2. Ask in natural language, for example:

```text
Please review the risk clauses in this contract
```

```text
Summarize the high-risk clauses and list the relevant legal basis
```

### Knowledge-Base Q&A

Once files are ingested, you can ask:

```text
How long does the confidentiality obligation last in this NDA?
```

```text
List the documents currently available in the knowledge base
```

### Strict Mode

If you want answers to stay strictly within the knowledge base instead of mixing in model assumptions, enable strict mode in the UI. This is especially useful for compliance-sensitive workflows.

## Tech Stack

| Category | Tools |
|----------|-------|
| Languages | Python, TypeScript |
| Backend | FastAPI, Pydantic Settings, Uvicorn |
| Frontend | Streamlit, Vue 3, Vite, Pinia, Vue Router |
| AI / RAG | LangGraph, Pinecone, BM25, Ollama, Google Gemini |
| External tools | Tavily, Firecrawl, ECharts, Groq |
| Testing | Pytest, Playwright |

## Project Structure

```text
Contract-compliance-agent/
├─ backend/                 # FastAPI API, schema, and service adapters
├─ web/                     # Vue 3 + Vite frontend
├─ data/                    # Default files and sample contracts
├─ eval/                    # Eval datasets and execution scripts
├─ docs/                    # Update summaries and document index
├─ deploy/systemd/          # DGX / Linux service templates
├─ scripts/                 # Install and deployment scripts
├─ streamlit_app.py         # Streamlit entry point
├─ agent_router.py          # Agent routing core
├─ rag_graph.py             # RAG workflow
├─ rag_common.py            # Shared retrieval and embedding logic
└─ ingest_service.py        # File ingest and source management
```

## Core Modules

| Module | Purpose |
|--------|---------|
| `streamlit_app.py` | Fastest demo and operations entry point with chat, ingest, and Eval view |
| `backend/main.py` | FastAPI app entry point integrating chat, ingest, admin, eval, and health routes |
| `agent_router.py` | Routes user intent to RAG, contract review, legal lookup, or other tools |
| `rag_graph.py` | LangGraph-powered retrieval and generation workflow |
| `rag_common.py` | Shared logic for Pinecone, embedding providers, BM25, and ranking |
| `expert_agents.py` | Expert-agent logic for contract compliance, data analysis, and related tasks |
| `ingest_service.py` | Handles file ingest, source registration, and chunk writing |

## Eval and Quality Validation

This project is not only a demo UI; it includes repeatable validation flows as well.

### Run API Tests

```bash
uv sync --extra dev
uv run python -m pytest tests/test_chat_api.py tests/test_ingest_api.py -v
```

### Run Eval Datasets

```bash
uv run python eval/run_eval.py
```

```bash
uv run python eval/run_eval.py --eval-set eval/eval_set_contract.json
```

```bash
uv run python eval/run_eval.py --groq
```

Eval outputs:

- `eval/runs/run_<timestamp>_results.jsonl`
- `eval/runs/run_<timestamp>_metrics.json`

You can track:

- routing accuracy
- tool success rate
- latency P50 / P95

## Limits and Disclaimer

This project is a first-pass contract-review assistant, not a legal-advice system.

- Output from this system does not constitute legal advice or a formal legal opinion
- AI may misclassify, omit, or miscite, so results should always be reviewed by qualified legal professionals
- Legal lookup depends on external search results and model synthesis, so final applicability must still be checked by humans
- If the knowledge base is incomplete, the model configuration is wrong, or Pinecone dimensions do not match, answer quality will degrade significantly

## Further Reading

- [docs/README.md](docs/README.md): update index and supporting documents
- [docs/update-summary-2026-04-15.md](docs/update-summary-2026-04-15.md): integration and deployment summary
- [backend/README.md](backend/README.md): FastAPI API, testing, and deployment details
- [web/README.md](web/README.md): Vue frontend development and build notes
- [STREAMLIT至Vue前端改版說明.md](STREAMLIT至Vue前端改版說明.md): frontend migration background

## Contributing

Issues and pull requests are welcome, especially for:

- improving contract-risk rules and prompts
- improving legal lookup quality and citations
- expanding Eval datasets and regression coverage
- improving DGX / Linux deployment workflows
- improving the Vue admin UI and user experience

Before submitting changes, it is a good idea to:

1. run contract checks if you changed APIs or data structures
2. run the relevant pytest cases if you changed chat or ingest flows
3. manually verify `/chat`, `/upload`, and `/admin` if you changed frontend behavior
4. update `README` or `docs/` when startup or deployment steps change

## License

This project is licensed under the [MIT License](LICENSE).
