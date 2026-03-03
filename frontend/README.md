## Frontend – Salon Chat UI (Next.js)

This folder contains the **face** of the salon assistant: a polished Next.js app with a chat-style interface that talks to the FastAPI backend.

---

### What you get

- **Chat interface** that feels like messaging a human receptionist
- **Markdown rendering** for assistant responses
- A footer that gently reminds users of **opening hours**
- Modern UI stack with:
  - **Next.js 16**
  - **React 19**
  - **Tailwind CSS**
  - **Radix UI** components
  - **Lucide icons**

---

### Environment configuration

Create a file called `.env.local` in `frontend/` with (at minimum):

- **`NEXT_PUBLIC_BACKEND_URL`** – base URL of the backend, e.g.

```bash
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000
```

The chat hook uses this to call the backend’s `/chat` endpoint.

---

### Running the frontend

From the `frontend/` folder:

```bash
npm install
npm run dev
```

Then open the printed URL in your browser (usually `http://localhost:3000`) and start chatting.

---

### Where to look in the code

- **Chat logic** – the React hook that handles API calls and client-side message state lives in `use-salon-chat` and related files.
- **UI layout and theming** – main pages, layout components, and Tailwind styles define the look and feel.
- **Components** – buttons, inputs, chat bubbles, and layout primitives are built with Radix + Tailwind for consistency and accessibility.

This frontend is meant to be **easy to reskin**: change colors, typography, or layout without touching the backend logic.

