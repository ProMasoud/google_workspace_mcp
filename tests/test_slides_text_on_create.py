"""create_presentation writes slide text with requests the Slides API accepts.

Asked for "a deck with two titled slides", models wrote raw batch_update_presentation
requests with invented fields ("pageObjectId", "createParagraphStyle") or inserted past the
end, and Google answered 400 on every try (37 calls in one turn). create_presentation now
takes the slides and builds the requests itself.
"""

import inspect
from unittest.mock import MagicMock

import pytest

from gslides import slides_tools


def _undecorated(tool):
    return inspect.unwrap(getattr(tool, "fn", tool))


def _service(default_slides=({"objectId": "p"},)):
    service = MagicMock()
    service.presentations().create().execute.return_value = {
        "presentationId": "deck1",
        "slides": list(default_slides),
    }
    service.presentations().batchUpdate().execute.return_value = {"replies": []}
    return service


def _sent_requests(service):
    return service.presentations().batchUpdate.call_args.kwargs["body"]["requests"]


@pytest.mark.asyncio
async def test_titled_slides_land_in_placeholders_and_the_blank_slide_goes():
    service = _service()

    result = await _undecorated(slides_tools.create_presentation)(
        service=service,
        user_google_email="demo@example.com",
        title="Aize demo deck",
        slides=[
            {"title": "Team workspace launch"},
            {"title": "Next steps", "body": "Design, hosting, ads"},
        ],
    )

    requests = _sent_requests(service)
    kinds = [next(iter(r)) for r in requests]
    assert kinds == [
        "createSlide",
        "insertText",
        "createSlide",
        "insertText",
        "insertText",
        "deleteObject",
    ]

    first, second = requests[0]["createSlide"], requests[2]["createSlide"]
    assert first["slideLayoutReference"] == {"predefinedLayout": "TITLE_ONLY"}
    assert second["slideLayoutReference"] == {"predefinedLayout": "TITLE_AND_BODY"}
    assert "insertionIndex" not in first

    # Text goes into the placeholder ids the same request assigned.
    title_id = first["placeholderIdMappings"][0]["objectId"]
    assert requests[1]["insertText"] == {"objectId": title_id, "text": "Team workspace launch"}
    body_map = second["placeholderIdMappings"][1]
    assert body_map["layoutPlaceholder"] == {"type": "BODY", "index": 0}
    assert requests[4]["insertText"] == {
        "objectId": body_map["objectId"],
        "text": "Design, hosting, ads",
    }
    assert requests[5] == {"deleteObject": {"objectId": "p"}}

    # Slides API object ids: 5-50 chars of [A-Za-z0-9_].
    ids = [first["objectId"], title_id, second["objectId"], body_map["objectId"]]
    assert all(5 <= len(i) <= 50 and i.replace("_", "").isalnum() for i in ids)

    assert "Slides: 2 slide(s) created" in result
    assert "Slide 1: Team workspace launch" in result
    assert "https://docs.google.com/presentation/d/deck1/edit" in result


@pytest.mark.asyncio
async def test_without_slides_nothing_but_the_create_is_sent():
    service = _service()

    result = await _undecorated(slides_tools.create_presentation)(
        service=service, user_google_email="demo@example.com", title="Empty deck"
    )

    service.presentations().batchUpdate().execute.assert_not_called()
    assert "Slides: 1 slide(s) created" in result


@pytest.mark.asyncio
async def test_an_empty_title_is_not_sent_as_empty_insert_text():
    service = _service(default_slides=())

    await _undecorated(slides_tools.create_presentation)(
        service=service,
        user_google_email="demo@example.com",
        title="Deck",
        slides=[{"title": ""}],
    )

    assert [next(iter(r)) for r in _sent_requests(service)] == ["createSlide"]


@pytest.mark.parametrize(
    "slides", [[], "Team workspace launch", ["Team workspace launch"], [{"title": "x"}] * 21]
)
def test_bad_slide_lists_are_refused_before_anything_is_created(slides):
    with pytest.raises(ValueError):
        slides_tools._slide_requests(slides)


@pytest.mark.asyncio
async def test_get_presentation_names_the_placeholder_ids():
    service = MagicMock()
    service.presentations().get().execute.return_value = {
        "title": "Aize demo deck",
        "slides": [
            {
                "objectId": "aize_s0",
                "pageElements": [
                    {
                        "objectId": "aize_s0_title",
                        "shape": {
                            "placeholder": {"type": "TITLE"},
                            "text": {"textElements": [{"textRun": {"content": "Launch\n"}}]},
                        },
                    }
                ],
            }
        ],
    }

    result = await _undecorated(slides_tools.get_presentation)(
        service=service, user_google_email="demo@example.com", presentation_id="deck1"
    )

    assert "[aize_s0_title (TITLE)]" in result
    assert "> Launch" in result
