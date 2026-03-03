## Backend – FastAPI + LangGraph Agent

This folder contains the **brains** of the salon booking assistant: a FastAPI app wrapped around a LangGraph agent with tools for search and scheduling.

---

### Features

- **Chat endpoint** (`/chat` and `/api/chat`) backed by a LangGraph agent
- **Tools** for:
  - Current date/time
  - Salon knowledge base retrieval (Qdrant + OpenAI embeddings + optional Cohere rerank)
  - Listing available slots based on opening hours and service duration
  - Creating, listing, cancelling, and rescheduling bookings
- **In-memory bookings store** using LangGraph’s `InMemoryStore`
- **Notebook-driven ingestion** (`notebooks/ingest.ipynb`) to push salon content into Qdrant

---

### Tech stack

- **Python** `>=3.11` managed via `uv`
- **FastAPI** + **Uvicorn** for the HTTP API
- **LangChain / LangGraph** for the agent and tools
- **Qdrant** for vector search
- **OpenAI embeddings** and **Cohere rerank**
- **Tavily** for web search (optional)

See `pyproject.toml` for the full dependency list.

---

### Environment variables

Create a `.env` and/or `.env.local` in `backend/` with at least:

- **`OPENAI_API_KEY`** – for embeddings and chat model
- **`COHERE_API_KEY`** – for reranking knowledge base results
- **`TAVILY_API_KEY`** – for web search
- **`QDRANT_URL`** – Qdrant HTTP endpoint
- **`QDRANT_API_KEY`** – Qdrant API key

You can add more variables as you extend the project.

---

### Running the backend

From the `backend/` folder:

```bash
# Install dependencies via uv
uv sync

# Run the API
uv run uvicorn api.index:app --reload --host 127.0.0.1 --port 8000
```

The main chat endpoint is exposed under `/chat`. The frontend assumes the backend is available on `http://127.0.0.1:8000`.

---

### Development tips

- To adjust the assistant’s **personality or rules**, edit the system prompt and tools wiring in `lib/agent.py`.
- To change salon content (services, prices, hours), update `data/data.txt` and rerun the ingestion notebook in `notebooks/`.
- For persistence beyond process lifetime, you can swap `InMemoryStore` for a durable store (e.g., Redis or Postgres-backed) without changing the tools’ surface area.

Treat this backend as your **sandbox for agent behaviors**—add tools, tweak prompts, and experiment freely.

