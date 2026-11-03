"""get_doc_content reads a Doc through the Docs API alone.

Aize does not request drive.readonly (a restricted scope), and the /docs server runs with
TOOLS="docs sheets slides", so a tool that asks for a Drive service fails the scope check
before it starts. The `documents` scope covers any Doc the user can open.
"""

import inspect
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from auth.scopes import DOCS_READONLY_SCOPE, get_current_scopes, set_enabled_tools
from gdocs import docs_tools


def _undecorated(tool):
    return inspect.unwrap(getattr(tool, "fn", tool))


def _http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


def _docs_service(title="Demo brief", text="Hello from the demo doc\n"):
    service = MagicMock()
    service.documents().get().execute.return_value = {
        "title": title,
        "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": text}}]}}]},
    }
    return service


@pytest.mark.asyncio
async def test_reads_the_doc_through_the_docs_api():
    result = await _undecorated(docs_tools.get_doc_content)(
        service=_docs_service(),
        user_google_email="demo@example.com",
        document_id="doc123",
    )

    assert 'File: "Demo brief"' in result
    assert "https://docs.google.com/document/d/doc123/edit" in result
    assert "Hello from the demo doc" in result


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_google_doc_gets_a_plain_answer():
    service = MagicMock()
    service.documents().get().execute.side_effect = _http_error(400)

    result = await _undecorated(docs_tools.get_doc_content)(
        service=service, user_google_email="demo@example.com", document_id="docx1"
    )

    assert "not a Google Doc" in result
    assert "Save as Google Docs" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 500])
async def test_other_docs_api_errors_still_raise(status):
    service = MagicMock()
    service.documents().get().execute.side_effect = _http_error(status)

    with pytest.raises(HttpError):
        await _undecorated(docs_tools.get_doc_content)(
            service=service, user_google_email="demo@example.com", document_id="doc123"
        )


def test_needs_no_drive_service_and_only_a_scope_the_docs_server_grants():
    params = inspect.signature(_undecorated(docs_tools.get_doc_content)).parameters
    assert "drive_service" not in params

    set_enabled_tools(["docs", "sheets", "slides"])
    try:
        assert DOCS_READONLY_SCOPE in get_current_scopes()
        assert not any("/auth/drive" in s for s in get_current_scopes())
    finally:
        set_enabled_tools(None)
