"""Gmail parsing and OAuth tests use mocked services and synthetic token files."""

import base64
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

from google.auth.exceptions import RefreshError

from finvault.gmail.client import GmailClient


def encoded(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


class GmailRetrievalTests(unittest.TestCase):
    def setUp(self):
        # Bypass authentication entirely; every API response is a mock.
        self.client = object.__new__(GmailClient)
        self.client.service = Mock()
        self.user = self.client.service.users.return_value

    def test_label_lookup_and_missing_label(self):
        self.user.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "fixture-label", "name": "UPI"}],
        }
        self.assertEqual(self.client.get_label_id("UPI"), "fixture-label")
        with self.assertRaisesRegex(ValueError, "Label not found: Missing"):
            self.client.get_label_id("Missing")

    def test_message_listing_retrieves_every_page(self):
        self.user.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "fixture-label", "name": "UPI"}],
        }
        listing = self.user.messages.return_value.list
        listing.return_value.execute.side_effect = [
            {"messages": [{"id": "fixture-one"}], "nextPageToken": "fixture-page"},
            {"messages": [{"id": "fixture-two"}]},
        ]
        self.assertEqual(self.client.get_messages("UPI"), [
            {"id": "fixture-one"}, {"id": "fixture-two"},
        ])
        self.assertEqual(listing.call_args_list, [
            call(userId="me", labelIds=["fixture-label"], pageToken=None, maxResults=100),
            call(userId="me", labelIds=["fixture-label"], pageToken="fixture-page", maxResults=100),
        ])

    def test_empty_mailbox_response(self):
        self.user.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "fixture-label", "name": "UPI"}],
        }
        self.user.messages.return_value.list.return_value.execute.return_value = {}
        self.assertEqual(self.client.get_messages("UPI"), [])

    def test_nested_multipart_html_and_unicode_are_decoded(self):
        html = "<p>Synthetic café payment</p>"
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "application/octet-stream", "body": {}},
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/html", "body": {"data": encoded(html)}},
                ]},
            ],
        }
        self.assertEqual(self.client._extract_body(payload), (html, "Synthetic café payment"))
        self.assertEqual(self.client._extract_body({}), ("", ""))

    def test_email_preserves_headers_date_and_plain_text(self):
        self.user.messages.return_value.get.return_value.execute.return_value = {
            "id": "fixture-message",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "alerts@example.invalid"},
                    {"name": "Subject", "value": "Synthetic account update"},
                    {"name": "Date", "value": "Tue, 02 Jan 2001 12:00:00 +0000"},
                ],
                "body": {"data": encoded("Synthetic plain-text email")},
            },
        }
        record = self.client.get_email("fixture-message")
        self.assertEqual(record.gmail_id, "fixture-message")
        self.assertEqual(record.sender, "alerts@example.invalid")
        self.assertEqual(record.subject, "Synthetic account update")
        self.assertEqual(record.received_at, datetime(2001, 1, 2, 12, tzinfo=timezone.utc))
        self.assertEqual(record.body_text, "Synthetic plain-text email")
        self.assertEqual(record.body_html, "")


class GmailAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_path = Path(self.directory.name) / "token.json"
        self.credentials_path = Path(self.directory.name) / "credentials.json"
        self.token_path.write_text('{"fixture": "original"}')
        self.credentials = Mock(valid=True, expired=False, refresh_token=None)
        self.credentials.to_json.return_value = '{"fixture": "refreshed"}'
        self.loader = self.enterContext(patch(
            "finvault.gmail.client.Credentials.from_authorized_user_file",
            return_value=self.credentials,
        ))
        self.flow = self.enterContext(patch("finvault.gmail.client.InstalledAppFlow"))
        self.request = self.enterContext(patch("finvault.gmail.client.Request"))
        self.build = self.enterContext(patch("finvault.gmail.client.build"))

    def client(self):
        return GmailClient(str(self.credentials_path), str(self.token_path))

    def test_valid_token_avoids_refresh_and_browser_authorization(self):
        original = self.token_path.read_text()
        self.client()
        self.credentials.refresh.assert_not_called()
        self.flow.from_client_secrets_file.assert_not_called()
        self.assertEqual(self.token_path.read_text(), original)
        self.build.assert_called_once_with("gmail", "v1", credentials=self.credentials)

    def test_expired_token_refreshes_without_browser_and_persists_result(self):
        self.credentials.valid = False
        self.credentials.expired = True
        self.credentials.refresh_token = True  # Mock availability, not a real token.
        self.client()
        self.credentials.refresh.assert_called_once_with(self.request.return_value)
        self.flow.from_client_secrets_file.assert_not_called()
        self.assertEqual(self.token_path.read_text(), self.credentials.to_json.return_value)

    def test_revoked_token_failure_does_not_overwrite_token_or_open_browser(self):
        self.credentials.valid = False
        self.credentials.expired = True
        self.credentials.refresh_token = True
        self.credentials.refresh.side_effect = RefreshError("Synthetic revocation")
        original = self.token_path.read_text()
        with self.assertRaises(RefreshError):
            self.client()
        self.flow.from_client_secrets_file.assert_not_called()
        self.build.assert_not_called()
        self.assertEqual(self.token_path.read_text(), original)

    def test_missing_token_uses_mocked_authorization_and_saves_result(self):
        self.token_path.unlink()
        self.flow.from_client_secrets_file.return_value.run_local_server.return_value = self.credentials
        self.client()
        self.loader.assert_not_called()
        self.flow.from_client_secrets_file.assert_called_once_with(
            self.credentials_path, GmailClient.SCOPES,
        )
        self.flow.from_client_secrets_file.return_value.run_local_server.assert_called_once_with(port=0)
        self.assertEqual(self.token_path.read_text(), self.credentials.to_json.return_value)
