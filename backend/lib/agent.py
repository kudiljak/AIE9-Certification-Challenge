import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_tavily import TavilySearch
from langchain_openai import ChatOpenAI
import cohere

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env.local", override=True)

agent: Any | None = None
store: BaseStore | None = None
checkpointer: MemorySaver | None = None

BOOKINGS_NS = ("bookings",)

COLLECTION_NAME = "documents"

qdrant_client = QdrantClient(url=os.getenv("QDRANT_URL", ""), api_key=os.getenv("QDRANT_API_KEY", ""))
qdrant_collection = qdrant_client.get_collection(collection_name=COLLECTION_NAME)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vector_store = QdrantVectorStore(
    client=qdrant_client,
    collection_name=COLLECTION_NAME,
    embedding=embeddings,
)
retriever = vector_store.as_retriever(search_kwargs={"k": 3})
wide_retriever = vector_store.as_retriever(search_kwargs={"k": 9})
cohere_client = cohere.AsyncClientV2(api_key=os.getenv("COHERE_API_KEY", ""))

store = InMemoryStore()

SYSTEM_PROMPT = """You are a friendly booking assistant for a hair salon. You help with scheduling, cancelling, and questions about services, hours, and prices.

CRITICAL: Never quote or paste the raw output of the retrieve tool. It gives you internal context only. Answer in your own words in 1-4 short sentences (e.g. "We're open Mon-Fri 9-6, Sat 9-4, closed Sunday" or "That service is 45 euros."). If the tool did not contain the answer, say so briefly.

You must:
1. Use the retrieve tool to get business hours and service durations from the salon's knowledge base whenever you need to offer times or create a booking.
2. Use get_current_datetime to know today's date and current time when discussing availability or confirming bookings.
3. Offer available appointment times for the requested service using get_available_slots. If the user does not accept a slot, propose alternative times (e.g. different day or time) until they confirm.
4. Once the user confirms a slot, collect their name and phone number, then create the booking with create_booking. Save bookings by phone number and name as specified.
5. If the user wants to cancel, use list_bookings to find their booking(s) by phone or name, then cancel_booking with the exact start time of the appointment to cancel.
6. Be concise and professional. Always confirm the chosen slot, service, name, and phone before creating a booking, and confirm cancellation after cancelling.
7. If the user asks for information that is not in the knowledge base, use search_web_current to search the web for current/recent hairstyling and beauty information, trends, and tips. Cite the source of the information in your response by providing the URL of the source.
8. If you cannot find relevant information, say so in one sentence.
9. When you call tools (especially retrieve), do not paste the full tool output to the user. Read it and answer in your own words, giving only the specific facts the user asked for (e.g. “We’re open Mon–Fri 9:00–18:00, Sat 9:00–16:00, closed Sunday”).
"""

# --- Parsing helpers for salon info from retrieved text ---

def _parse_duration_minutes(text: str, service_name: str) -> int:
    """Extract service duration in minutes from retrieved text. Service name is matched case-insensitively."""
    service_lower = service_name.lower()
    for line in text.replace("\n", " ").split(":"):
        if service_lower in line.lower():
            m = re.search(r"(\d+)\s*min", line, re.I)
            if m:
                return int(m.group(1))
    fallbacks = {
        "haircut": 30, "trim": 20, "coloring": 90, "highlights": 120,
        "styling": 45, "blow": 45, "conditioning": 60,
    }
    for k, v in fallbacks.items():
        if k in service_lower:
            return v
    return 60

def _parse_business_hours(text: str) -> dict[int, tuple[int, int]]:
    """Return map: weekday (0=Mon..6=Sun) -> (open_hour, open_min), (close_hour, close_min). We use simple (open_hr, close_hr) in 24h."""
    out: dict[int, tuple[int, int]] = {}
    # Monday to Friday: 9:00–18:00
    m = re.search(r"Monday to Friday:\s*(\d{1,2}):(\d{2})\s*[–\-]\s*(\d{1,2}):(\d{2})", text, re.I)
    if m:
        open_hr, open_min, close_hr, close_min = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        for w in range(5):  # 0-4 Mon-Fri
            out[w] = (open_hr * 60 + open_min, close_hr * 60 + close_min)
    # Saturday: 9:00–16:00
    m = re.search(r"Saturday:\s*(\d{1,2}):(\d{2})\s*[–\-]\s*(\d{1,2}):(\d{2})", text, re.I)
    if m:
        open_hr, open_min, close_hr, close_min = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        out[5] = (open_hr * 60 + open_min, close_hr * 60 + close_min)
    # Sunday: closed
    # If nothing parsed, use defaults
    if not out:
        out = {0: (9*60, 18*60), 1: (9*60, 18*60), 2: (9*60, 18*60), 3: (9*60, 18*60), 4: (9*60, 18*60), 5: (9*60, 16*60)}
    else:
        out[6] = (0, 0)  # Sunday closed -> 0-0
    return out

def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone.strip())

@tool
async def get_current_datetime() -> str:
    """Return the current date and time in ISO format and readable form. Use this to know today's date and current time during the conversation."""
    now = datetime.now()
    return f"Current date and time: {now.isoformat()} ({now.strftime('%A, %B %d, %Y at %I:%M %p')})"

@tool
async def retrieve(query: str) -> str:
    """Retrieve information from the salon's knowledge base. Returns only the relevant excerpt for the assistant to use when answering; do not forward this raw to the user."""
    docs = await retriever.ainvoke(query)
    if not docs:
        return "No information found in knowledge base."
    return "\n\n".join([f"[KB Source {i+1}]: {doc.page_content}" for i, doc in enumerate(docs)])

@tool
async def advanced_retrieve(query: str) -> str:
    """Retrieve information from the salon's knowledge base. Returns only the relevant excerpt for the assistant to use when answering; do not forward this raw to the user."""
    docs = await wide_retriever.ainvoke(query)
    if not docs:
        return "No information found in knowledge base."
    doc_texts = [doc.page_content for doc in docs]
    reranked = await cohere_client.rerank(
        model="rerank-v3.5",
        query=query,
        documents=doc_texts,
        top_n=3,
    )
    if not docs:
        return "No information found in knowledge base."
    return "\n\n".join([f"[KB Source {i+1}]: {doc.page_content}" for i, doc in enumerate(docs)])

@tool
async def get_available_slots(service_name: str, date_iso: str | None = None) -> str:
    """Get available appointment time slots for a service on a given date. If date_iso is omitted, use today. date_iso should be YYYY-MM-DD. Use retrieve first to get business hours and service durations."""
    now = datetime.now()
    if date_iso:
        try:
            day = datetime.fromisoformat(date_iso.replace("Z", "+00:00").split("T")[0]).date()
        except Exception:
            day = now.date()
    else:
        day = now.date()

    docs = await retriever.ainvoke("business hours and service durations")
    text = "\n\n".join([d.page_content for d in docs]) if docs else ""
    hours_map = _parse_business_hours(text)
    duration_min = _parse_duration_minutes(text, service_name)

    weekday = day.weekday()  # 0=Mon, 6=Sun
    if weekday not in hours_map or hours_map[weekday][0] >= hours_map[weekday][1]:
        return f"{day.isoformat()} is closed (e.g. Sunday or outside business hours)."

    open_minutes, close_minutes = hours_map[weekday]
    open_dt = datetime(day.year, day.month, day.day) + timedelta(minutes=open_minutes)
    close_dt = datetime(day.year, day.month, day.day) + timedelta(minutes=close_minutes)

    # Existing bookings on this day
    all_items = await store.asearch(BOOKINGS_NS, limit=200)
    booked: list[tuple[datetime, datetime]] = []
    for item in all_items:
        v = getattr(item, "value", item) if hasattr(item, "value") else item
        if isinstance(v, dict) and "start_iso" in v and "end_iso" in v:
            start_str = v["start_iso"]
            if start_str.startswith(day.isoformat()):
                try:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(v["end_iso"].replace("Z", "+00:00"))
                    booked.append((start_dt, end_dt))
                except Exception:
                    pass

    slots: list[str] = []
    slot_start = open_dt
    while slot_start + timedelta(minutes=duration_min) <= close_dt:
        slot_end = slot_start + timedelta(minutes=duration_min)
        if now.tzinfo:
            slot_start = slot_start.replace(tzinfo=now.tzinfo)
            slot_end = slot_end.replace(tzinfo=now.tzinfo)
        overlap = any(
            (slot_start < be and slot_end > bs)
            for bs, be in booked
        )
        if not overlap and slot_start >= now:
            slots.append(slot_start.strftime("%Y-%m-%dT%H:%M") + f" ({slot_start.strftime('%I:%M %p')})")
        slot_start += timedelta(minutes=15)

    if not slots:
        return f"No available slots for {service_name} on {day.isoformat()} (duration {duration_min} min). Try another day."
    return f"Available slots for {service_name} on {day.isoformat()} (duration {duration_min} min): " + ", ".join(slots[:15]) + ("..." if len(slots) > 15 else "")

async def _create_booking(phone: str, name: str, service: str, start_iso: str) -> str:
    """Create a booking and save it in the store by phone number and name. start_iso should be the chosen slot start in ISO format (e.g. 2025-03-02T10:00)."""
    phone_clean = _normalize_phone(phone)
    if not phone_clean or not name.strip():
        return "Error: phone number and name are required."
    docs = await retriever.ainvoke("service duration " + service)
    text = "\n\n".join([d.page_content for d in docs]) if docs else ""
    duration_min = _parse_duration_minutes(text, service)
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00").strip())
    except Exception:
        return "Error: invalid start_iso. Use format like 2025-03-02T10:00."
    end_dt = start_dt + timedelta(minutes=duration_min)
    key = f"{phone_clean}_{name.strip()}_{start_dt.isoformat()}"
    await store.aput(
        BOOKINGS_NS,
        key,
        {
            "phone": phone_clean,
            "name": name.strip(),
            "service": service,
            "start_iso": start_dt.isoformat(),
            "end_iso": end_dt.isoformat(),
        },
    )
    return f"Booked: {service} for {name} (phone {phone_clean}) on {start_dt.strftime('%Y-%m-%d at %I:%M %p')}."


@tool
async def create_booking(phone: str, name: str, service: str, start_iso: str) -> str:
    """Create a booking and save it in the store by phone number and name. start_iso should be the chosen slot start in ISO format (e.g. 2025-03-02T10:00)."""
    return await _create_booking(phone=phone, name=name, service=service, start_iso=start_iso)

@tool
async def list_bookings(phone: str | None = None, name: str | None = None) -> str:
    """List bookings, optionally filtered by phone number and/or name. Use to find a client's appointment before cancelling, rescheduling, confirming a new appointment, or if client asks for their booking. Include start_iso in the output so you can pass it to cancel_booking. """
    all_items = await store.asearch(BOOKINGS_NS, limit=200)
    results: list[str] = []
    phone_clean = _normalize_phone(phone) if phone else None
    name_lower = (name or "").strip().lower()
    for item in all_items:
        v = getattr(item, "value", item) if hasattr(item, "value") else item
        if not isinstance(v, dict):
            continue
        if phone_clean and v.get("phone") != phone_clean:
            continue
        if name_lower and name_lower not in (v.get("name") or "").strip().lower():
            continue
        start_iso = v.get("start_iso", "")
        results.append(f"{v.get('name')} | {v.get('phone')} | {v.get('service')} | start_iso={start_iso}")
    if not results:
        return "No matching bookings found."
    return "Bookings:\n" + "\n".join(results)

async def _cancel_booking(phone: str, name: str, start_iso: str) -> str:
    """Cancel an existing booking. Provide the exact phone number, name, and start_iso of the appointment (as returned by list_bookings)."""
    phone_clean = _normalize_phone(phone)
    start_iso = start_iso.strip()
    all_items = await store.asearch(BOOKINGS_NS, limit=200)
    for item in all_items:
        v = getattr(item, "value", item) if hasattr(item, "value") else item
        if not isinstance(v, dict):
            continue
        if v.get("phone") != phone_clean:
            continue
        if (v.get("name") or "").strip().lower() != (name or "").strip().lower():
            continue
        if v.get("start_iso", "").startswith(start_iso) or start_iso in v.get("start_iso", ""):
            store_key = getattr(item, "key", None)
            if store_key:
                await store.adelete(BOOKINGS_NS, store_key)
                return "Booking cancelled successfully."
    return "No booking found for that phone, name, and time. Use list_bookings to get the exact start_iso."

@tool
async def cancel_booking(phone: str, name: str, start_iso: str) -> str:
    """Cancel an existing booking. Provide the exact phone number, name, and start_iso of the appointment (as returned by list_bookings)."""
    return await _cancel_booking(phone=phone, name=name, start_iso=start_iso)


@tool
async def reschedule_booking(phone: str, name: str, old_start_iso: str, new_start_iso: str) -> str:
    """Reschedule an existing booking by cancelling the old time and creating a new one.

    Uses the same phone, name, and service as the original booking.
    - phone: client's phone number
    - name: client's name
    - old_start_iso: existing appointment start time (as from list_bookings)
    - new_start_iso: desired new start time (e.g. 2025-03-02T10:00)
    """
    phone_clean = _normalize_phone(phone)
    old_start_iso = old_start_iso.strip()

    # Find the existing booking and remember its service
    all_items = await store.asearch(BOOKINGS_NS, limit=200)
    booking_service: str | None = None
    for item in all_items:
        v = getattr(item, "value", item) if hasattr(item, "value") else item
        if not isinstance(v, dict):
            continue
        if v.get("phone") != phone_clean:
            continue
        if (v.get("name") or "").strip().lower() != (name or "").strip().lower():
            continue
        if v.get("start_iso", "").startswith(old_start_iso) or old_start_iso in v.get("start_iso", ""):
            booking_service = v.get("service")
            break

    if not booking_service:
        return "No existing booking found to reschedule for that phone, name, and time."

    cancel_result = await _cancel_booking(phone=phone, name=name, start_iso=old_start_iso)
    if "successfully" not in cancel_result.lower():
        return f"Could not cancel existing booking to reschedule: {cancel_result}"

    create_result = await _create_booking(phone=phone, name=name, service=booking_service, start_iso=new_start_iso)
    return f"Rescheduled booking. {create_result}"

tavily_search = TavilySearch(
    max_results=3,
    topic="general"
)

@tool
def search_web_current(query: str) -> str:
    """Search the web for current/recent hairstyling and beauty information, trends, and tips.
    Use this when you need the latest research, news, or information not in the knowledge base.
    """
    response = tavily_search.invoke(query)
    if not response or not response.get('results'):
        return "No web results found."
    formatted = []
    for i, r in enumerate(response['results'][:3]):
        formatted.append(f"[Web Source {i+1}]: {r.get('content', 'N/A')}\nURL: {r.get('url', 'N/A')}")
    return "\n\n".join(formatted)


async def get_agent():
    global agent, checkpointer

    if agent is None or checkpointer is None:
        checkpointer = MemorySaver()

        agent = create_agent(
            model="openai:gpt-4o-mini",
            checkpointer=checkpointer,
            tools=[
                get_current_datetime,
                advanced_retrieve,
                get_available_slots,
                create_booking,
                list_bookings,
                cancel_booking,
                reschedule_booking,
                search_web_current,
            ],
            system_prompt=SYSTEM_PROMPT,
            store=store,
        )

    return agent
