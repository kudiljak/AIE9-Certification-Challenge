from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from lib.agent import get_agent
from pydantic import BaseModel
from typing import Any
import json
from fastapi.responses import StreamingResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    input: Any | None = None
    message: str | None = None
    thread_id: str | None = None
    context: dict[str, Any] | None = None
    config: dict[str, Any] | None = None

def _extract_content(msg: dict) -> str:
    """Extract text content from a message dump (handles string or content_blocks)."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", b.get("content", ""))
            for b in content
            if isinstance(b, dict)
        )
    return ""

def _prepare_input(request: ChatRequest):
    """Shared input preparation (DRY)."""
    if request.input is not None:
        return request.input, request.config or {}

    if not request.message:
        return None, None

    input_data = {
        "messages": [{"role": "user", "content": request.message}]
    }

    config = dict(request.config or {})
    config.setdefault("configurable", {})
    config["configurable"]["thread_id"] = request.thread_id or "default"

    return input_data, config

@app.get("/")
@app.get("/health")
def root():
    return {"status": "ok"}

@app.post("/api/chat")
async def chat_api(request: ChatRequest):
    return await _chat(request)

@app.post("/chat")
async def chat(request: ChatRequest):
    return await _chat(request)

async def _chat(request: ChatRequest):
    """Handle chat - accept either {input, config} or {message, thread_id}."""
    if request.input is not None:
        input_data = request.input
        config = request.config or {}
    else:
        if not request.message:
            return {"error": "message or input required"}
        input_data = {"messages": [{"role": "user", "content": request.message}]}
        config = dict(request.config or {})
        config.setdefault("configurable", {})
        config["configurable"]["thread_id"] = request.thread_id or "default"

    agent = await get_agent()
    result = await agent.ainvoke(
        input_data, context=request.context or {}, config=config
    )
    messages = result.get("messages", [])

    final_content = ""
    for msg in reversed(messages):
        dump = msg.model_dump(mode="json") if hasattr(msg, "model_dump") else msg
        if isinstance(dump, dict) and dump.get("type") == "ai":
            final_content = _extract_content(dump)
            break
    return {"message": final_content}

@app.post("/chat-stream")
async def chat_stream(request: ChatRequest):
    input_data, config = _prepare_input(request)

    if input_data is None:
        return {"error": "message or input required"}

    agent = await get_agent()

    async def generator():
        async for chunk in agent.astream(
            input_data,
            context=request.context or {},
            config=config,
        ):
            messages = chunk.get("messages", [])

            for msg in messages:
                dump = msg.model_dump(mode="json") if hasattr(msg, "model_dump") else msg

                if isinstance(dump, dict) and dump.get("type") == "ai":
                    content = _extract_content(dump)

                    if content:
                        # SSE format (frontend-friendly)
                        yield f"data: {content}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")