"""create_event / modify_event: a date-time without an offset gets the calendar's time zone.

Google rejects such a time ("Missing time zone definition for start time") unless the event
names a time zone; on 2026-11-03 that cost a demo 26 calls for one event.
"""

import inspect
from unittest.mock import MagicMock

import pytest

from gcalendar import calendar_tools as ct


def _fn(tool):
    return inspect.unwrap(getattr(tool, "fn", tool))


def _service(calendar_tz="Asia/Tehran", zone_read_error=None):
    service = MagicMock()
    # The zone is read from events.list, which the calendar.events scope allows.
    if zone_read_error:
        service.events().list().execute.side_effect = zone_read_error
    else:
        service.events().list().execute.return_value = {"timeZone": calendar_tz}
    service.events().insert().execute.return_value = {"id": "e1", "htmlLink": "https://cal/e1"}
    service.events().get().execute.return_value = {"id": "e1", "summary": "Old"}
    service.events().update().execute.return_value = {"id": "e1", "htmlLink": "https://cal/e1"}
    service.events().patch().execute.return_value = {"id": "e1", "htmlLink": "https://cal/e1"}
    return service


def _inserted_body(service):
    return service.events().insert.call_args.kwargs["body"]


@pytest.mark.parametrize(
    "value,naive",
    [
        ("2026-11-04T10:00:00", True),
        ("2026-11-04T10:00", True),
        ("2026-11-04T10:00:00.000", True),
        ("2026-11-04T10:00:00.000Z", False),
        ("2026-11-04T10:00:00.123+03:30", False),
        ("2026-11-04T10:00:00Z", False),
        ("2026-11-04T10:00:00-05:00", False),
        ("2026-11-04T10:00:00+0330", False),
        ("2026-11-04", False),
    ],
)
def test_is_naive_datetime(value, naive):
    assert ct._is_naive_datetime(value) is naive


@pytest.mark.asyncio
async def test_create_event_uses_the_calendar_time_zone_for_naive_times():
    service = _service("Asia/Tehran")
    await _fn(ct.create_event)(
        service=service,
        user_google_email="demo@example.com",
        summary="Demo",
        start_time="2026-11-04T10:00:00",
        end_time="2026-11-04T10:30:00",
    )
    body = _inserted_body(service)
    assert body["start"] == {"dateTime": "2026-11-04T10:00:00", "timeZone": "Asia/Tehran"}
    assert body["end"] == {"dateTime": "2026-11-04T10:30:00", "timeZone": "Asia/Tehran"}


@pytest.mark.asyncio
async def test_create_event_guesses_nothing_when_the_zone_cannot_be_read():
    service = _service(zone_read_error=Exception("403"))
    await _fn(ct.create_event)(
        service=service,
        user_google_email="demo@example.com",
        summary="Demo",
        start_time="2026-11-04T10:00:00",
        end_time="2026-11-04T10:30:00",
    )
    # Sent as given: Google fails loudly instead of the event landing at a guessed hour.
    assert _inserted_body(service)["start"] == {"dateTime": "2026-11-04T10:00:00"}


@pytest.mark.asyncio
async def test_create_event_leaves_offsets_explicit_zones_and_all_day_alone():
    service = _service("Asia/Tehran")
    await _fn(ct.create_event)(
        service=service,
        user_google_email="demo@example.com",
        summary="Demo",
        start_time="2026-11-04T10:00:00-05:00",
        end_time="2026-11-04T10:30:00",
        timezone="Europe/Berlin",
    )
    body = _inserted_body(service)
    assert body["start"]["timeZone"] == "Europe/Berlin"
    assert body["end"]["timeZone"] == "Europe/Berlin"

    service = _service("Asia/Tehran")
    await _fn(ct.create_event)(
        service=service,
        user_google_email="demo@example.com",
        summary="All day",
        start_time="2026-11-04",
        end_time="2026-11-05",
    )
    assert _inserted_body(service)["start"] == {"date": "2026-11-04"}


@pytest.mark.asyncio
async def test_modify_event_fills_the_zone_for_a_naive_new_start():
    service = _service("Asia/Tehran")
    await _fn(ct.modify_event)(
        service=service,
        user_google_email="demo@example.com",
        event_id="e1",
        start_time="2026-11-04T11:00:00",
        end_time="2026-11-04T11:30:00",
    )
    sent = [c.kwargs.get("body") for c in service.events().update.call_args_list] + [
        c.kwargs.get("body") for c in service.events().patch.call_args_list
    ]
    sent = [b for b in sent if b]
    assert sent, "modify_event sent no body"
    assert sent[-1]["start"]["timeZone"] == "Asia/Tehran"


@pytest.mark.asyncio
async def test_the_zone_lookup_uses_events_list_not_calendars_get():
    service = _service("Asia/Tehran")
    await _fn(ct.create_event)(
        service=service,
        user_google_email="demo@example.com",
        summary="Demo",
        start_time="2026-11-04T10:00:00",
        end_time="2026-11-04T10:30:00",
    )
    kwargs = service.events().list.call_args.kwargs
    assert kwargs["calendarId"] == "primary" and kwargs["maxResults"] == 1
    service.calendars().get.assert_not_called()
