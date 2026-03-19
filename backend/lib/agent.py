import os
import re
import threading
from datetime import datetime, timedelta, date
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
import sqlite3

from lib.google_calendar import (
    get_calendar_service,
    create_calendar_event,
    delete_calendar_event,
    update_calendar_event_time,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env.local", override=True)

agent: Any | None = None
checkpointer: MemorySaver | None = None


def get_db():
    return sqlite3.connect(
        "data/salon.db",
        check_same_thread=False,
        isolation_level=None
    )


def _ensure_bookings_event_id_column() -> None:
    """Ensure bookings table has event_id column for Google Calendar sync."""
    with get_db() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(bookings)").fetchall()]
        if "event_id" not in cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN event_id TEXT")
            conn.commit()
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_date_start ON bookings(date, start_minutes)"
        )
        conn.commit()


_ensure_bookings_event_id_column()


GOOGLE_CALENDAR_ENABLED = (os.getenv("GOOGLE_CALENDAR_ENABLED") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_calendar_service = None


def _get_calendar_service_cached():
    """Return cached calendar service or None if disabled/unavailable."""
    global _calendar_service
    if not GOOGLE_CALENDAR_ENABLED:
        return None
    if _calendar_service is not None:
        return _calendar_service
    try:
        token_path = ROOT_DIR / "data" / "token.json"
        credentials_path = ROOT_DIR / "data" / "credentials.json"
        _calendar_service = get_calendar_service(
            token_path=token_path, credentials_path=credentials_path
        )
        return _calendar_service
    except Exception:
        # Don't block bookings if calendar auth isn't configured
        return None


def _normalize_for_match(text: str) -> str:
    """Lowercase, replace hyphens with spaces, collapse spaces. So 'Blow-Dry' and 'blow dry' both become 'blow dry'."""
    return re.sub(r"[\s\-]+", " ", (text or "").lower()).strip()


def _resolve_service_name(user_input: str) -> str | None:
    s = (user_input or "").strip()
    if not s:
        return None

    with get_db() as conn:
        rows = conn.execute("SELECT name FROM services").fetchall()

    names = [r[0] for r in rows]

    for name in names:
        if s == name:
            return name

    for name in names:
        if s.lower() == name.lower():
            return name

    norm_input = _normalize_for_match(s)

    for name in names:
        if norm_input == _normalize_for_match(name):
            return name

    input_tokens = set(norm_input.split())

    scored = []
    for name in names:
        name_tokens = set(_normalize_for_match(name).split())

        overlap = len(input_tokens & name_tokens)

        if overlap == 0:
            continue

        score = overlap / len(name_tokens)

        scored.append((score, name))

    if not scored:
        return None

    scored.sort(reverse=True)

    best_score, best_name = scored[0]

    if best_score < 0.6:
        return None

    if len(scored) > 1:
        second_score, _ = scored[1]
        if abs(best_score - second_score) < 0.2:
            return None

    return best_name


def _get_service_duration_from_db(service_name: str) -> int | None:
    """Get duration in minutes for a service. Prefer resolved (canonical) name for exact match."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT duration FROM services WHERE name = ?",
            (service_name,),
        ).fetchone()
        if row:
            return int(row[0])
        canonical = _resolve_service_name(service_name)
        if canonical is None:
            return None
        row = conn.execute(
            "SELECT duration FROM services WHERE name = ?",
            (canonical,),
        ).fetchone()
    return int(row[0]) if row else None


def _get_business_hours_for_date(day: date) -> tuple[int, int] | None:
    weekday_str = day.strftime("%A").lower()

    with get_db() as conn:
        row = conn.execute(
            "SELECT open_time, close_time FROM business_hours WHERE day = ?",
            (weekday_str,),
        ).fetchone()

    if not row:
        return None

    return int(row[0]), int(row[1])


def _get_combined_duration_from_db(services: list[str]) -> int | None:
    """Return total duration in minutes for a list of services, or None if any is unknown."""
    total = 0
    for s in services:
        d = _get_service_duration_from_db(s)
        if d is None:
            return None
        total += int(d)
    return total


@tool
async def get_combined_duration(services: list[str]) -> str:
    """Return total duration (minutes) for one or more services, resolving user phrasing to DB names.

    Input: list of service names (e.g. ["Women's Haircut", "Scalp Treatment"] or ["haircut", "scalp treatment"])
    Output: a short string including resolved names and total minutes.
    """
    resolved: list[str] = []
    for s in services:
        c = _resolve_service_name(s)
        if c is None:
            return f"Unknown service: {s}"
        resolved.append(c)
    total = _get_combined_duration_from_db(resolved)
    if total is None:
        return "Could not determine total duration."
    return f"Services: {', '.join(resolved)} | total_minutes={int(total)}"


def _get_bookings_for_date(day: date) -> list[tuple[int, int]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT start_minutes, end_minutes FROM bookings WHERE date = ?",
            (day.isoformat(),),
        ).fetchall()

    return [(int(r[0]), int(r[1])) for r in rows]


def _insert_booking_row(
    phone: str,
    name: str,
    service: str,
    day: date,
    start_minutes: int,
    end_minutes: int,
    event_id: str | None = None,
) -> None:

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO bookings (name, phone, service, date, start_minutes, end_minutes, event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, phone, service, day.isoformat(), start_minutes, end_minutes, event_id),
        )

        conn.commit()


def _insert_booking_row_atomic(
    *,
    phone: str,
    name: str,
    service: str,
    day: date,
    start_minutes: int,
    end_minutes: int,
) -> bool:
    """Atomically insert booking if it doesn't overlap existing ones.

    Uses BEGIN IMMEDIATE so two concurrent requests cannot both pass the overlap check.
    """
    with get_db() as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT start_minutes, end_minutes FROM bookings WHERE date = ?",
            (day.isoformat(),),
        ).fetchall()
        overlap = any(
            start_minutes < int(be) and end_minutes > int(bs) for bs, be in rows
        )
        if overlap:
            conn.execute("ROLLBACK")
            return False
        conn.execute(
            """
            INSERT INTO bookings (name, phone, service, date, start_minutes, end_minutes, event_id)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (name, phone, service, day.isoformat(), start_minutes, end_minutes),
        )
        conn.execute("COMMIT")
        return True


def _set_booking_event_id(
    *,
    phone: str,
    name: str,
    day: date,
    start_minutes: int,
    event_id: str,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE bookings
            SET event_id = ?
            WHERE phone = ? AND LOWER(name) = ? AND date = ? AND start_minutes = ?
            """,
            (event_id, phone, (name or "").strip().lower(), day.isoformat(), start_minutes),
        )
        conn.commit()


COLLECTION_NAME = "salon_info"

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

SYSTEM_PROMPT = """You are Lumi, the friendly AI booking assistant for Maison Lumiere. You help with scheduling, cancelling, and questions about services, hours, and prices.

CRITICAL: Never quote or paste the raw output of the retrieve tool. It gives you internal context only. Answer in your own words in 1-4 short sentences (e.g. "We're open Mon-Fri 9-6, Sat 9-4, closed Sunday" or "That service is 45 euros."). If the tool did not contain the answer, say so briefly.

You must:
0. In your first response to the customer, introduce yourself once as Lumi (e.g. "Hi! I'm Lumi, Maison Lumiere's booking assistant."). Do not repeat your name in every message.
1. Use the retrieve tool to get information from the salon's knowledge base whenever a customer asks about information, such as business hours, service durations, stylists or pricing.
2. Do NOT use retrieve to decide whether we offer a service. The database (services table) is the source of truth. When a user asks for a service (e.g. "keratin", "blow dry", "women's haircut"), call get_available_slots (or create_booking when ready)—the system resolves natural wording to our service list. Only if the tool returns "We don't have that service in our system" should you tell the user we don't offer it. Never say "we don't offer that" based solely on retrieve; the database is authoritative.
3. ALWAYS use get_current_datetime to know today's date and current time when discussing availability or confirming bookings. If the user says "today", "tomorrow", a weekday ("Friday"), or a partial date ("April 8"), you MUST call get_current_datetime first and base all date resolution on that output. Never guess the year or date from memory.
4. When a customer asks for "a haircut" or "haircut" without specifying which type, ask once in a single short sentence: "Which type of haircut would you like: Women's, Men's, or Children's (under 12)?" Then wait for their answer. Do not repeat this clarification twice in different sentences, and do not offer slots or create a booking until the service is clear.
5. When the user gives a partial date (e.g. "April 8" or "Friday" without a year), always interpret it relative to today using get_current_datetime. Use the next matching calendar date on or after today. Never pick a past year (e.g. 2024) when a future date with the same month and day exists (e.g. 2026‑04‑08).
6. If today is already after that month/day in the current year, use the same month/day in the next year (e.g. if today is 2026‑04‑10 and the user says "April 8", interpret as 2027‑04‑08). For day names like "Friday", resolve to the next upcoming Friday (or "next Friday" if the user says so).
7. When resolving any natural-language date, always derive it from get_current_datetime and state the resolved date back to the user in full ISO form (YYYY‑MM‑DD) before offering slots or booking (e.g. "That would be 2026‑04‑08. Does that work?"). Never propose a past date for "today/tomorrow/next Friday" requests. If you somehow compute a past date, stop and re-run get_current_datetime, then ask the user to clarify.
8. Before calling get_available_slots or create_booking (or upsell_booking), repeat and confirm the exact date and time with the user (e.g. "So you'd like a Women's Haircut on 2026‑04‑08 at 12:00?") and only proceed after they agree.
9. ALWAYS search the salon knowledge base for relevant bundle deals before offering appointment slots. If the customer is booking a service that has a relevant combined option in the salon knowledge base, ALWAYS offer the combined option before offering appointment slots. Phrase it naturally (do not use words like "upsell", "bundle", or "deal", and do not mention system limitations). If the knowledge base includes a savings amount (e.g. "(save $5)"), mention the savings explicitly (e.g. "…for $55 (save $5)"). Use retrieve to look up the exact option names, prices, and savings. If you mention the total duration for a combined option, you MUST call get_combined_duration with the included services and use the returned total_minutes (do not guess). Keep this to one short sentence, then ask which they prefer.
10. If the customer declines or chooses the base service, proceed to get_available_slots for that base service. If they choose the combined option, first resolve each included service name to the exact DB service (e.g. Women's Haircut, Blow-Dry), then use the system's duration lookup to calculate the total combined duration (base + add-ons). Call get_available_slots for the base service with duration_override set to that total, so the chosen start time fits the full appointment, and then create the combined appointment with upsell_booking (base_service + extra_services). Do not say we "can't" book it—just book it as a combined appointment.
11. Be concise and professional. Always confirm the chosen slot by specifying the date, time, service (or combined service), name, and phone before creating a booking, and confirm cancellation after cancelling. If the user wants to change the day of the appointment, use reschedule_booking to cancel the old appointment and create a new one on the new day.
12. Once the user confirms a slot, collect their name and phone number, then create the booking with create_booking (or upsell_booking for combined services). Before the user confirms, ALWAYS include this short privacy notice once: "By confirming, you agree that we use your name and phone number for booking purposes." Then proceed. Save bookings by phone number and name as specified.
13. Never call create_booking with a generic service like "haircut" or "cut". Only call create_booking with an exact service name from our system (e.g. "Women's Haircut", "Men's Haircut", "Children's Haircut (under 12)", "Blow-Dry", etc.). If the service is not exact yet, ask a clarifying question instead of booking.
14. Immediately before calling create_booking, restate the exact service name you will book and then pass that exact same string as the service argument (e.g. say "Booking a Women's Haircut…" and call create_booking(service="Women's Haircut", ...)). Do not paraphrase the service name in the tool call.
15. If the user wants to cancel, use list_bookings to find their booking(s) by phone or name, then cancel_booking with the exact start time of the appointment to cancel.
16. Do not answer requests that are unrelated to salon booking (scheduling, cancelling, rescheduling), services/prices/hours/stylists, or general hair/beauty care tips. If the user asks something unrelated (e.g. non-hair topics), respond with a short refusal/redirect ONLY (for example: "I can only help with salon bookings and hair/beauty questions."). Do NOT include the answer to the unrelated question, even partially, and do not add extra facts after the refusal.
17. If the user asks for information that is not in the knowledge base, use search_web_current to search the web for current/recent hairstyling and beauty information, trends, and tips. Cite the source of the information in your response by providing the URL of the source.
18. If you cannot find relevant information, say so in one sentence.
19. When you call tools (especially retrieve), do not paste the full tool output to the user. Read it and answer in your own words, giving only the specific facts the user asked for (e.g. "We're open Mon–Fri 9:00–18:00, Sat 9:00–16:00, closed Sunday").
20. When the user requests a booking at a specific time (e.g. "book tomorrow at 9.10", "at 9 AM", "how about 17:00?", "in the evening?"), you must call get_available_slots with that date and with specific_time set to the requested time (e.g. "9:10 AM", "17:00", "5:00 PM") in the same call. Do not infer from the short list of suggested slots—that list is only a sample; many other times may be available.
21. Never tell the user that a specific time is unavailable (e.g. "we don't have availability at 17:00" or "the last slot is 15:40") unless you have just called get_available_slots with that exact time in the specific_time parameter. If the user asks "how about 17:00?" or "and in the evening?", call get_available_slots(..., specific_time="17:00") first, then report what the tool returns. Otherwise you may wrongly say a time is taken when it is not.
22. Never change the service name after it has been confirmed with the user. The service name must remain exactly the same when calling create_booking.
"""


def _normalize_phone(phone: str) -> str:
    phone = re.sub(r"\D", "", phone)
    if phone.startswith("0"):
        phone = "385" + phone[1:]
    return phone


def _parse_time_to_minutes(s: str) -> int | None:
    """Parse a time string like '1:00 PM', '13:00', '9.10', '9.10 AM' to minutes since midnight. Returns None if invalid."""
    s = (s or "").strip()
    if not s:
        return None
    # Allow "9.10" or "9.10 AM" (dot as hour:minute separator)
    s = s.replace(".", ":", 1) if "." in s and ":" not in s else s
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
        try:
            t = datetime.strptime(s, fmt)
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    return None

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
    response = await cohere_client.rerank(
        model="rerank-v3.5",
        query=query,
        documents=doc_texts,
        top_n=3,
    ) # retruns a list of indices of the documents in the order of relevance
    if not response.results:
        return "No information found in knowledge base."
    parts = []
    for i, result in enumerate(response.results):
        doc = docs[result.index]
        parts.append(f"[KB Source {i+1}]: {doc.page_content}")
    return "\n\n".join(parts)

@tool
async def get_available_slots(
    service_name: str,
    date_iso: str | None = None,
    time_preference: str | None = None,
    specific_time: str | None = None,
    duration_override: int | None = None,
) -> str:
    """Get available appointment time slots for a service on a given date.

    Uses salon.db (services, business_hours, bookings) as the source of truth
    for durations, business hours, and existing bookings.
    If date_iso is omitted, uses today. date_iso should be YYYY-MM-DD.
    If the user asks about a specific time (e.g. 'Is 1:00 PM available?'), pass that time in specific_time (e.g. '1:00 PM' or '13:00') to get a definitive yes/no answer for that slot.
    For combined appointments, pass duration_override (minutes) so availability accounts for the full combined duration.
    """
    canonical = _resolve_service_name(service_name)
    if canonical is None:
        return (
            "We don't have that service in our system. "
            "Please ask for one of our listed services (e.g. Women's Haircut, Men's Haircut, Balayage, Keratin Treatment)."
        )
    if duration_override is not None:
        duration_min = int(duration_override)
    else:
        duration_min = _get_service_duration_from_db(canonical)
        if duration_min is None:
            return (
                "We don't have that service in our system. "
                "Please ask for one of our listed services (e.g. Women's Haircut, Men's Haircut, Balayage)."
            )

    now = datetime.now()
    if date_iso:
        try:
            day = datetime.fromisoformat(date_iso.replace("Z", "+00:00").split("T")[0]).date()
        except Exception:
            day = now.date()
    else:
        day = now.date()

    hours = _get_business_hours_for_date(day)
    if not hours:
        return f"{day.isoformat()} is closed or has no defined business hours."

    open_minutes, close_minutes = hours
    if open_minutes >= close_minutes:
        return f"{day.isoformat()} is closed."

    existing = _get_bookings_for_date(day)

    slots: list[str] = []
    slot_start_min = open_minutes
    date_str = day.isoformat()

    while slot_start_min + duration_min <= close_minutes:
        slot_end_min = slot_start_min + duration_min

        if day == now.date():
            now_min = now.hour * 60 + now.minute
            if slot_start_min < now_min:
                slot_start_min += 10
                continue

        overlap = any(
            (slot_start_min < be and slot_end_min > bs) for (bs, be) in existing
        )
        if not overlap:
            hour = slot_start_min // 60
            minute = slot_start_min % 60
            slot_dt = datetime(day.year, day.month, day.day, hour, minute)
            slots.append(
                slot_dt.strftime("%Y-%m-%dT%H:%M")
                + f" ({slot_dt.strftime('%I:%M %p')})"
            )

        slot_start_min += 10

    if not slots:
        return (
            f"No available slots for {canonical} on {date_str} "
            f"(duration {duration_min} min). Try another day."
        )

    # When user asks for a specific time, give a definitive answer (snap to 10-min steps if needed)
    if specific_time:
        req_min = _parse_time_to_minutes(specific_time)
        if req_min is not None:
            slot_starts_list = []
            for slot_str in slots:
                iso_part = slot_str.split(" ")[0]
                t = datetime.fromisoformat(iso_part)
                slot_starts_list.append(t.hour * 60 + t.minute)
            slot_starts = set(slot_starts_list)

            if req_min in slot_starts:
                hour = req_min // 60
                minute = req_min % 60
                slot_dt = datetime(day.year, day.month, day.day, hour, minute)
                iso_time = slot_dt.strftime("%Y-%m-%dT%H:%M")
                return (
                    f"Yes, {slot_dt.strftime('%I:%M %p')} on {date_str} is available for {canonical}. "
                    f"Use service={canonical!r} and start_iso={iso_time} when booking (e.g. create_booking or upsell_booking)."
                )
            # Not on the 10-min grid (e.g. 13:05) – offer nearest bookable slot
            nearest_min = min(slot_starts_list, key=lambda m: abs(m - req_min))
            hour = nearest_min // 60
            minute = nearest_min % 60
            slot_dt = datetime(day.year, day.month, day.day, hour, minute)
            iso_time = slot_dt.strftime("%Y-%m-%dT%H:%M")
            return (
                f"We book in 10-minute steps. The nearest time to {specific_time} is {slot_dt.strftime('%I:%M %p')}, and that's available for {canonical}. "
                f"Use service={canonical!r} and start_iso={iso_time} when booking (e.g. create_booking or upsell_booking)."
            )

    time_ranges = {
    "morning": (9 * 60, 12 * 60),
    "midday": (12 * 60, 14 * 60),
    "afternoon": (14 * 60, 17 * 60),
    "evening": (17 * 60, 20 * 60),
    }

    # prioritize preferred time of day instead of filtering
    if time_preference and time_preference in time_ranges:
        start_pref, end_pref = time_ranges[time_preference]

        def slot_priority(slot: str):
            t = datetime.fromisoformat(slot.split(" ")[0])
            minutes = t.hour * 60 + t.minute

            # slots inside preferred window get higher priority
            if start_pref <= minutes < end_pref:
                return 0
            return 1

        slots = sorted(slots, key=slot_priority)


    def pick_best_slots(slots: list[str], n: int = 5) -> list[str]:
        if len(slots) <= n:
            return slots

        step = max(1, len(slots) // n)
        return [slots[i * step] for i in range(n)]


    best_slots = pick_best_slots(slots, 5)

    return (
        f"Suggested slots for {canonical} on {date_str}: "
        + ", ".join(best_slots)
        + ". Use service={canonical!r} when booking. Other times may also be available; use specific_time if the user asks for one."
    )


async def _create_booking(
    phone: str,
    name: str,
    service: str,
    start_iso: str,
    duration_override: int | None = None,
) -> str:
    """Create a booking and save it in salon.db.

    If duration_override is provided, use that; otherwise look up the service in the services table.
    User phrasing (e.g. 'keratin') is resolved to the canonical DB service name for storage.
    """
    # For bundles created via upsell_booking we pass duration_override and a combined service label
    # (e.g. "Women's Haircut + Blow-Dry"). In that case, store the label as-is.
    if duration_override is None:
        canonical = _resolve_service_name(service)
        if canonical is None:
            return (
                "We don't have that service in our system. "
                "Please choose one of our listed services."
            )
    else:
        canonical = service.strip()

    phone_clean = _normalize_phone(phone)
    if not phone_clean or not name.strip():
        return "Error: phone number and name are required."

    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00").strip())
    except Exception:
        return "Error: invalid start_iso. Use format like 2025-03-02T10:00."

    if duration_override is not None:
        duration_min = duration_override
    else:
        duration_min = _get_service_duration_from_db(canonical)
        if duration_min is None:
            return (
                "We don't have that service in our system. "
                "Please choose one of our listed services."
            )

    day = start_dt.date()
    start_minutes = start_dt.hour * 60 + start_dt.minute
    end_minutes = start_minutes + duration_min

    inserted = _insert_booking_row_atomic(
        phone=phone_clean,
        name=name.strip(),
        service=canonical,
        day=day,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
    )
    if not inserted:
        return "That time is already booked. Please choose another slot."

    # Create Google Calendar event after booking succeeds (best-effort)
    cal = _get_calendar_service_cached()
    if cal is not None:
        try:
            event_id = create_calendar_event(
                cal,
                canonical,
                start_dt,
                int(duration_min),
                name.strip(),
                phone_clean,
            )
            if event_id:
                _set_booking_event_id(
                    phone=phone_clean,
                    name=name.strip(),
                    day=day,
                    start_minutes=start_minutes,
                    event_id=event_id,
                )
        except Exception:
            pass

    return (
        f"Booked: {canonical} for {name.strip()} (phone {phone_clean}) on "
        f"{start_dt.strftime('%Y-%m-%d at %I:%M %p')} "
        f"for {duration_min} minutes."
    )


@tool
async def create_booking(phone: str, name: str, service: str, start_iso: str) -> str:
    """Create a booking in salon.db."""
    return await _create_booking(
        phone=phone, name=name, service=service, start_iso=start_iso
    )


@tool
async def upsell_booking(
    phone: str,
    name: str,
    base_service: str,
    extra_services: list[str],
    start_iso: str,
) -> str:
    """Create a booking that includes a base service plus one or more upsell services.

    - Resolves user phrasing (e.g. 'keratin') to canonical DB names; combines into \"Base + Extra1 + Extra2\" for the stored service.
    - Uses salon.db services table to sum all durations.
    - Saves a single booking row via the core booking logic.
    """
    all_inputs = [base_service] + list(extra_services)
    canonicals: list[str] = []
    for s in all_inputs:
        c = _resolve_service_name(s)
        if c is None:
            return f"We don't have the service '{s}' in our system. Please pick a different option."
        canonicals.append(c)

    total_duration = _get_combined_duration_from_db(canonicals)
    if total_duration is None:
        return "Could not determine the full duration for that combination of services."
    combined_name = " + ".join(canonicals)

    return await _create_booking(
        phone=phone,
        name=name,
        service=combined_name,
        start_iso=start_iso,
        duration_override=int(total_duration),
    )

@tool
async def list_bookings(phone: str | None = None, name: str | None = None) -> str:
    """List bookings from salon.db, optionally filtered by phone number and/or name.

    Include start_iso in the output so you can pass it to cancel_booking.
    """
    q = "SELECT name, phone, service, date, start_minutes FROM bookings"
    clauses: list[str] = []
    params: list[str] = []

    phone_clean = _normalize_phone(phone) if phone else None
    if phone_clean:
        clauses.append("phone = ?")
        params.append(phone_clean)
    if name:
        clauses.append("LOWER(name) LIKE ?")
        params.append(f"%{(name or '').strip().lower()}%")

    if clauses:
        q += " WHERE " + " AND ".join(clauses)

    with get_db() as conn:
        rows = conn.execute(q, params).fetchall()
    if not rows:
        return "No matching bookings found."

    lines: list[str] = []
    for n, p, service, date_str, start_min in rows:
        start_min = int(start_min)
        hour = start_min // 60
        minute = start_min % 60
        start_iso = f"{date_str}T{hour:02d}:{minute:02d}"
        lines.append(f"{n} | {p} | {service} | start_iso={start_iso}")

    return "Bookings:\n" + "\n".join(lines)

async def _cancel_booking(phone: str, name: str, start_iso: str) -> str:
    """Cancel an existing booking. Provide the exact phone number, name, and start_iso of the appointment (as returned by list_bookings)."""
    phone_clean = _normalize_phone(phone)
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00").strip())
    except Exception:
        return "Error: invalid start_iso. Use format like 2025-03-02T10:00."

    date_str = dt.date().isoformat()
    start_minutes = dt.hour * 60 + dt.minute

    event_id: str | None = None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT event_id FROM bookings
            WHERE phone = ? AND LOWER(name) = ? AND date = ? AND start_minutes = ?
            """,
            (phone_clean, (name or "").strip().lower(), date_str, start_minutes),
        ).fetchone()
        if row:
            event_id = row[0]

        cur = conn.execute(
            """
            DELETE FROM bookings
            WHERE phone = ? AND LOWER(name) = ? AND date = ? AND start_minutes = ?
            """,
            (phone_clean, (name or "").strip().lower(), date_str, start_minutes),
        )
        conn.commit()

    if cur.rowcount == 0:
        return "No booking found for that phone, name, and time. Use list_bookings to get the exact start_iso."

    cal = _get_calendar_service_cached()
    if cal is not None and event_id:
        try:
            delete_calendar_event(cal, event_id)
        except Exception:
            pass
    return "Booking cancelled successfully."

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
    try:
        old_dt = datetime.fromisoformat(old_start_iso.replace("Z", "+00:00").strip())
    except Exception:
        return "Error: invalid old_start_iso. Use format like 2025-03-02T10:00."

    date_str = old_dt.date().isoformat()
    old_start_minutes = old_dt.hour * 60 + old_dt.minute

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT service, event_id FROM bookings
            WHERE phone = ? AND LOWER(name) = ? AND date = ? AND start_minutes = ?
            """,
            (phone_clean, (name or "").strip().lower(), date_str, old_start_minutes),
        ).fetchone()

    if not row:
        return "No existing booking found to reschedule for that phone, name, and time."

    service, event_id = row[0], row[1]

    try:
        new_dt = datetime.fromisoformat(new_start_iso.replace("Z", "+00:00").strip())
    except Exception:
        return "Error: invalid new_start_iso. Use format like 2025-03-02T10:00."

    # Support both single services and combined labels like "Women's Haircut + Blow-Dry"
    if " + " in service:
        parts = [p.strip() for p in service.split("+") if p.strip()]
        duration_min = _get_combined_duration_from_db(parts)
    else:
        duration_min = _get_service_duration_from_db(service)
    if duration_min is None:
        return "Could not determine service duration for rescheduling."

    new_day = new_dt.date()
    new_start_minutes = new_dt.hour * 60 + new_dt.minute
    new_end_minutes = new_start_minutes + int(duration_min)

    # Overlap check on new date (excluding the current booking)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT start_minutes, end_minutes FROM bookings WHERE date = ?",
            (new_day.isoformat(),),
        ).fetchall()
        for bs, be in rows:
            bs, be = int(bs), int(be)
            if old_dt.date().isoformat() == new_day.isoformat() and bs == int(old_start_minutes):
                continue
            if new_start_minutes < be and new_end_minutes > bs:
                return "That new time overlaps an existing booking. Please choose another slot."

        conn.execute(
            """
            UPDATE bookings
            SET date = ?, start_minutes = ?, end_minutes = ?
            WHERE phone = ? AND LOWER(name) = ? AND date = ? AND start_minutes = ?
            """,
            (
                new_day.isoformat(),
                new_start_minutes,
                new_end_minutes,
                phone_clean,
                (name or "").strip().lower(),
                date_str,
                old_start_minutes,
            ),
        )
        conn.commit()

    # Update calendar event if present (best-effort)
    cal = _get_calendar_service_cached()
    if cal is not None and event_id:
        try:
            # Treat new_dt as local wall-clock time in the salon timezone; the calendar
            # helper attaches SALON_TIMEZONE, so we keep this naive to avoid DST drift.
            update_calendar_event_time(cal, event_id, new_dt, int(duration_min))
        except Exception:
            pass

    return "Rescheduled booking successfully."

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
            model="openai:gpt-4.1",
            checkpointer=checkpointer,
            tools=[
                get_current_datetime,
                advanced_retrieve,
                get_combined_duration,
                get_available_slots,
                create_booking,
                upsell_booking,
                list_bookings,
                cancel_booking,
                reschedule_booking,
                search_web_current,
            ],
            system_prompt=SYSTEM_PROMPT,
            store=store,
        )

    return agent
