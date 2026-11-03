"""
Google Slides MCP Tools

This module provides MCP tools for interacting with Google Slides API.
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional

from googleapiclient.errors import HttpError


from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors
from core.comments import create_comment_tools

logger = logging.getLogger(__name__)


@server.tool()
@handle_http_errors("create_presentation", service_type="slides")
@require_google_service("slides", "slides")
async def create_presentation(
    service,
    user_google_email: str,
    title: str = "Untitled Presentation",
    slides: Optional[List[Dict[str, Optional[str]]]] = None,
) -> str:
    """
    Create a new Google Slides presentation, optionally with titled slides.

    Args:
        user_google_email (str): The user's Google email address. Required.
        title (str): The title for the new presentation. Defaults to "Untitled Presentation".
        slides (List[Dict[str, str]]): Optional slides in order, each {"title": "...", "body": "..."}
            ("body" optional). Use this to create a deck with text instead of batch_update_presentation.

    Returns:
        str: Details about the created presentation including ID and URL.
    """
    logger.info(
        f"[create_presentation] Invoked. Email: '{user_google_email}', Title: '{title}', Slides: {len(slides or [])}"
    )

    requests = _slide_requests(slides) if slides is not None else []

    body = {"title": title}

    result = await asyncio.to_thread(service.presentations().create(body=body).execute)

    presentation_id = result.get("presentationId")
    presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    slide_count = len(result.get("slides", []))

    if requests:
        # The new deck starts with one blank title slide; the requested slides replace it.
        requests += [
            {"deleteObject": {"objectId": s["objectId"]}}
            for s in result.get("slides", [])
            if s.get("objectId")
        ]
        try:
            await asyncio.to_thread(
                service.presentations()
                .batchUpdate(
                    presentationId=presentation_id, body={"requests": requests}
                )
                .execute
            )
        except HttpError as error:
            if error.resp.status != 400:
                raise
            # The deck exists; say so, so a retry edits it instead of creating another one.
            logger.error(
                f"[create_presentation] Slides not written to {presentation_id}: {error}"
            )
            return f"""Presentation created for {user_google_email}, but its slides could not be written:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: {presentation_url}
- Error: {error}
Edit this presentation with batch_update_presentation; do not create another one."""
        slide_count = len(slides)

    confirmation_message = f"""Presentation Created Successfully for {user_google_email}:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: {presentation_url}
- Slides: {slide_count} slide(s) created"""
    for i, s in enumerate(slides or [], 1):
        confirmation_message += f"\n  Slide {i}: {s.get('title') or ''}"

    logger.info(f"Presentation created successfully for {user_google_email}")
    return confirmation_message


MAX_SLIDES_ON_CREATE = 20


def _slide_requests(slides: List[Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
    """Valid batchUpdate requests for titled slides.

    Models asked to write slide text through batch_update_presentation invent request
    shapes ("pageObjectId", "createParagraphStyle", insertion past the end) and Google
    answers 400. Here the layout placeholders get ids we choose, so the text lands in them.
    """
    if not isinstance(slides, list) or not slides:
        raise UserInputError(
            "slides must be a non-empty list of {'title': ..., 'body': ...}"
        )
    if len(slides) > MAX_SLIDES_ON_CREATE:
        raise UserInputError(
            f"At most {MAX_SLIDES_ON_CREATE} slides can be created at once."
        )

    requests: List[Dict[str, Any]] = []
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise UserInputError(
                f"Slide {i + 1} must be an object with 'title' and optional 'body'."
            )
        slide_title = str(slide.get("title") or "")
        slide_body = str(slide.get("body") or "")
        slide_id, title_id, body_id = (
            f"aize_s{i}",
            f"aize_s{i}_title",
            f"aize_s{i}_body",
        )
        mappings = [
            {"layoutPlaceholder": {"type": "TITLE", "index": 0}, "objectId": title_id}
        ]
        if slide_body:
            mappings.append(
                {"layoutPlaceholder": {"type": "BODY", "index": 0}, "objectId": body_id}
            )
        requests.append(
            {
                "createSlide": {
                    "objectId": slide_id,
                    "slideLayoutReference": {
                        "predefinedLayout": "TITLE_AND_BODY"
                        if slide_body
                        else "TITLE_ONLY"
                    },
                    "placeholderIdMappings": mappings,
                }
            }
        )
        # insertText rejects empty text, so an empty title stays an empty placeholder.
        if slide_title:
            requests.append({"insertText": {"objectId": title_id, "text": slide_title}})
        if slide_body:
            requests.append({"insertText": {"objectId": body_id, "text": slide_body}})
    return requests


@server.tool()
@handle_http_errors("get_presentation", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_presentation(
    service, user_google_email: str, presentation_id: str
) -> str:
    """
    Get details about a Google Slides presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation to retrieve.

    Returns:
        str: Details about the presentation including title, slides count, and metadata.
    """
    logger.info(
        f"[get_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}'"
    )

    result = await asyncio.to_thread(
        service.presentations().get(presentationId=presentation_id).execute
    )

    title = result.get("title", "Untitled")
    slides = result.get("slides", [])
    page_size = result.get("pageSize", {})

    slides_info = []
    for i, slide in enumerate(slides, 1):
        slide_id = slide.get("objectId", "Unknown")
        page_elements = slide.get("pageElements", [])

        # Collect text from the slide whose JSON structure is very complicated
        # https://googleapis.github.io/google-api-python-client/docs/dyn/slides_v1.presentations.html#get
        slide_text = ""
        try:
            texts_from_elements = []
            for page_element in slide.get("pageElements", []):
                shape = page_element.get("shape", None)
                if shape and shape.get("text", None):
                    text = shape.get("text", None)
                    if text:
                        text_elements_in_shape = []
                        for text_element in text.get("textElements", []):
                            text_run = text_element.get("textRun", None)
                            if text_run:
                                content = text_run.get("content", None)
                                if content:
                                    start_index = text_element.get("startIndex", 0)
                                    text_elements_in_shape.append(
                                        (start_index, content)
                                    )

                        if text_elements_in_shape:
                            # Sort text elements within a single shape
                            text_elements_in_shape.sort(key=lambda item: item[0])
                            full_text_from_shape = "".join(
                                [item[1] for item in text_elements_in_shape]
                            )
                            texts_from_elements.append(full_text_from_shape)

            # cleanup text we collected
            slide_text = "\n".join(texts_from_elements)
            slide_text_rows = slide_text.split("\n")
            slide_text_rows = [row for row in slide_text_rows if len(row.strip()) > 0]
            if slide_text_rows:
                slide_text_rows = ["    > " + row for row in slide_text_rows]
                slide_text = "\n" + "\n".join(slide_text_rows)
            else:
                slide_text = ""
        except Exception as e:
            logger.warning(f"Failed to extract text from the slide {slide_id}: {e}")
            slide_text = f"<failed to extract text: {type(e)}, {e}>"

        # Element ids (and placeholder types) are what insertText and deleteText target.
        element_ids = ", ".join(
            pe.get("objectId", "?")
            + (
                f" ({pe['shape']['placeholder'].get('type')})"
                if pe.get("shape", {}).get("placeholder")
                else ""
            )
            for pe in page_elements
        )
        slides_info.append(
            f"  Slide {i}: ID {slide_id}, {len(page_elements)} element(s)"
            + (f" [{element_ids}]" if element_ids else "")
            + f", text: {slide_text if slide_text else 'empty'}"
        )

    confirmation_message = f"""Presentation Details for {user_google_email}:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: https://docs.google.com/presentation/d/{presentation_id}/edit
- Total Slides: {len(slides)}
- Page Size: {page_size.get("width", {}).get("magnitude", "Unknown")} x {page_size.get("height", {}).get("magnitude", "Unknown")} {page_size.get("width", {}).get("unit", "")}

Slides Breakdown:
{chr(10).join(slides_info) if slides_info else "  No slides found"}"""

    logger.info(f"Presentation retrieved successfully for {user_google_email}")
    return confirmation_message


@server.tool()
@handle_http_errors("batch_update_presentation", service_type="slides")
@require_google_service("slides", "slides")
async def batch_update_presentation(
    service,
    user_google_email: str,
    presentation_id: str,
    requests: List[Dict[str, Any]],
) -> str:
    """
    Apply batch updates to a Google Slides presentation.

    Requests are raw Slides API requests and must match its schema exactly. To create a deck
    with titled slides, use create_presentation(slides=[...]) instead. Element and placeholder
    ids for insertText come from get_presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation to update.
        requests (List[Dict[str, Any]]): List of update requests to apply.

    Returns:
        str: Details about the batch update operation results.
    """
    logger.info(
        f"[batch_update_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}', Requests: {len(requests)}"
    )

    body = {"requests": requests}

    result = await asyncio.to_thread(
        service.presentations()
        .batchUpdate(presentationId=presentation_id, body=body)
        .execute
    )

    replies = result.get("replies", [])

    confirmation_message = f"""Batch Update Completed for {user_google_email}:
- Presentation ID: {presentation_id}
- URL: https://docs.google.com/presentation/d/{presentation_id}/edit
- Requests Applied: {len(requests)}
- Replies Received: {len(replies)}"""

    if replies:
        confirmation_message += "\n\nUpdate Results:"
        for i, reply in enumerate(replies, 1):
            if "createSlide" in reply:
                slide_id = reply["createSlide"].get("objectId", "Unknown")
                confirmation_message += (
                    f"\n  Request {i}: Created slide with ID {slide_id}"
                )
            elif "createShape" in reply:
                shape_id = reply["createShape"].get("objectId", "Unknown")
                confirmation_message += (
                    f"\n  Request {i}: Created shape with ID {shape_id}"
                )
            else:
                confirmation_message += f"\n  Request {i}: Operation completed"

    logger.info(f"Batch update completed successfully for {user_google_email}")
    return confirmation_message


@server.tool()
@handle_http_errors("get_page", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_page(
    service, user_google_email: str, presentation_id: str, page_object_id: str
) -> str:
    """
    Get details about a specific page (slide) in a presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide to retrieve.

    Returns:
        str: Details about the specific page including elements and layout.
    """
    logger.info(
        f"[get_page] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .get(presentationId=presentation_id, pageObjectId=page_object_id)
        .execute
    )

    page_type = result.get("pageType", "Unknown")
    page_elements = result.get("pageElements", [])

    elements_info = []
    for element in page_elements:
        element_id = element.get("objectId", "Unknown")
        if "shape" in element:
            shape_type = element["shape"].get("shapeType", "Unknown")
            elements_info.append(f"  Shape: ID {element_id}, Type: {shape_type}")
        elif "table" in element:
            table = element["table"]
            rows = table.get("rows", 0)
            cols = table.get("columns", 0)
            elements_info.append(f"  Table: ID {element_id}, Size: {rows}x{cols}")
        elif "line" in element:
            line_type = element["line"].get("lineType", "Unknown")
            elements_info.append(f"  Line: ID {element_id}, Type: {line_type}")
        else:
            elements_info.append(f"  Element: ID {element_id}, Type: Unknown")

    confirmation_message = f"""Page Details for {user_google_email}:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Page Type: {page_type}
- Total Elements: {len(page_elements)}

Page Elements:
{chr(10).join(elements_info) if elements_info else "  No elements found"}"""

    logger.info(f"Page retrieved successfully for {user_google_email}")
    return confirmation_message


@server.tool()
@handle_http_errors("get_page_thumbnail", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_page_thumbnail(
    service,
    user_google_email: str,
    presentation_id: str,
    page_object_id: str,
    thumbnail_size: str = "MEDIUM",
) -> str:
    """
    Generate a thumbnail URL for a specific page (slide) in a presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide.
        thumbnail_size (str): Size of thumbnail ("LARGE", "MEDIUM", "SMALL"). Defaults to "MEDIUM".

    Returns:
        str: URL to the generated thumbnail image.
    """
    logger.info(
        f"[get_page_thumbnail] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}', Size: '{thumbnail_size}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .getThumbnail(
            presentationId=presentation_id,
            pageObjectId=page_object_id,
            thumbnailProperties_thumbnailSize=thumbnail_size,
            thumbnailProperties_mimeType="PNG",
        )
        .execute
    )

    thumbnail_url = result.get("contentUrl", "")

    confirmation_message = f"""Thumbnail Generated for {user_google_email}:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Thumbnail Size: {thumbnail_size}
- Thumbnail URL: {thumbnail_url}

You can view or download the thumbnail using the provided URL."""

    logger.info(f"Thumbnail generated successfully for {user_google_email}")
    return confirmation_message


# Create comment management tools for slides
_comment_tools = create_comment_tools("presentation", "presentation_id")
read_presentation_comments = _comment_tools["read_comments"]
create_presentation_comment = _comment_tools["create_comment"]
reply_to_presentation_comment = _comment_tools["reply_to_comment"]
resolve_presentation_comment = _comment_tools["resolve_comment"]

# Aliases for backwards compatibility and intuitive naming
read_slide_comments = read_presentation_comments
create_slide_comment = create_presentation_comment
reply_to_slide_comment = reply_to_presentation_comment
resolve_slide_comment = resolve_presentation_comment
