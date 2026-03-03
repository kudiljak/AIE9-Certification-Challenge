## AIE9 Certification Challenge – Salon Booking Assistant

Welcome to your **AI-powered hair salon booking assistant**. This project combines a FastAPI backend with a modern Next.js frontend to let customers chat with an assistant that can:

- Help them discover services, prices, and opening hours
- Offer **real, time-aware appointment slots**
- Create, list, cancel, and reschedule bookings by **name + phone**

The goal is to feel like texting a friendly receptionist who never gets tired.

---

### Project structure

- **`backend/`** – FastAPI + LangGraph agent, tools, vector store, and notebooks
- **`frontend/`** – Next.js app with a chat-style UI

You can work on each part independently, but they’re designed to run together.

---

### Quick start

**1. Backend**

- Go to `backend/`
- Create `.env` and `.env.local` (see `backend/README.md` for variables)
- Install dependencies and run the API server:

```bash
cd backend
uv sync
uv run uvicorn api.index:app --reload --host 127.0.0.1 --port 8000
```

**2. Frontend**

- Go to `frontend/`
- Create `.env.local` (see `frontend/README.md`)
- Install dependencies and start the dev server:

```bash
cd frontend
npm install
npm run dev
```

With both servers running, open the frontend in your browser and start chatting with the assistant.

---

### What the assistant can do

- **Understand salon knowledge** from a vector store (services, durations, business rules)
- **Be date/time aware** via tools that return the current date and time
- **Offer and book slots** only during opening hours and within business constraints
- **Manage bookings**: list, cancel, and reschedule existing appointments

The assistant uses tools defined in `backend/lib/agent.py` to keep its behavior reliable and auditable.

---

### For reviewers and tinkerers

- Want to see how RAG is wired? Check the ingestion notebook in `backend/notebooks/`.
- Want to extend tools (e.g., add stylist preferences)? Add tools in `backend/lib/agent.py` and wire them into the agent.
- Want to re-skin the UI? The chat and layout live in the `frontend` app; Tailwind and Radix UI are already set up.

Have fun experimenting—this project is meant to be both **practical** and **playful**.

