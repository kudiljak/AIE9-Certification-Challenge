import os
from datetime import timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

DEFAULT_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DEFAULT_TIMEZONE = os.getenv("SALON_TIMEZONE", "Europe/Zagreb")


def get_calendar_service(
    token_path: str | Path | None = None,
    credentials_path: str | Path | None = None,
):
    """Return an authenticated Google Calendar service.

    Paths default to files in the backend working directory unless provided.
    """
    token_path = Path(token_path) if token_path is not None else Path("token.json")
    credentials_path = Path(credentials_path) if credentials_path is not None else Path("credentials.json")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def create_calendar_event(
    service,
    service_name: str,
    start_dt,
    duration_min: int,
    name: str,
    phone: str,
    *,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    """Create a calendar event and return the Google event id."""
    end_dt = start_dt + timedelta(minutes=duration_min)

    event = {
        "summary": f"{service_name} - {name}",
        "description": f"Client: {name}\nPhone: {phone}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
    }

    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created.get("id", "")


def delete_calendar_event(
    service,
    event_id: str,
    *,
    calendar_id: str = DEFAULT_CALENDAR_ID,
) -> None:
    """Delete a calendar event (no-op if event_id empty)."""
    if not event_id:
        return
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


def update_calendar_event_time(
    service,
    event_id: str,
    start_dt,
    duration_min: int,
    *,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    timezone: str = DEFAULT_TIMEZONE,
) -> None:
    """Update an event's start/end times (no-op if event_id empty)."""
    if not event_id:
        return
    end_dt = start_dt + timedelta(minutes=duration_min)
    body = {
        "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
    }
    service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()