"""get_doc_content reads a Doc that Drive cannot see (no drive.readonly) through the Docs API."""

import inspect
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gdocs import docs_tools


def _undecorated(tool):
    fn = getattr(tool, "fn", tool)
    return inspect.unwrap(fn)


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
@pytest.mark.parametrize("status", [403, 404])
async def test_falls_back_to_docs_api_when_drive_cannot_see_the_file(status):
    drive = MagicMock()
    drive.files().get().execute.side_effect = _http_error(status)

    result = await _undecorated(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=_docs_service(),
        user_google_email="demo@example.com",
        document_id="doc123",
    )

    assert 'File: "Demo brief"' in result
    assert "https://docs.google.com/document/d/doc123/edit" in result
    assert "Hello from the demo doc" in result


@pytest.mark.asyncio
async def test_other_drive_errors_still_raise():
    drive = MagicMock()
    drive.files().get().execute.side_effect = _http_error(500)

    with pytest.raises(HttpError):
        await _undecorated(docs_tools.get_doc_content)(
            drive_service=drive,
            docs_service=_docs_service(),
            user_google_email="demo@example.com",
            document_id="doc123",
        )


@pytest.mark.asyncio
async def test_drive_metadata_is_used_when_drive_can_see_the_file():
    drive = MagicMock()
    drive.files().get().execute.return_value = {
        "name": "Name from Drive",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/doc123/edit?usp=drivesdk",
    }

    result = await _undecorated(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=_docs_service(),
        user_google_email="demo@example.com",
        document_id="doc123",
    )

    assert 'File: "Name from Drive"' in result
    assert "usp=drivesdk" in result
