from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import base64
from datetime import datetime
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime

from finvault.models.email import Email


class GmailClient:
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(
        self,
        credentials_path="credentials.json",
        token_path="token.json",
    ):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(
                self.token_path,
                self.SCOPES,
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path,
                    self.SCOPES,
                )
                creds = flow.run_local_server(port=0)

            self.token_path.write_text(creds.to_json())

        return build("gmail", "v1", credentials=creds)
        
    def _extract_body(self, payload) -> tuple[str, str]:
        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data")

        if body_data:
            body = base64.urlsafe_b64decode(body_data).decode(
                "utf-8",
                errors="replace",
            )

            if mime_type == "text/html":
                text = BeautifulSoup(body, "html.parser").get_text(
                    separator="\n",
                    strip=True,
                )
                return body, text

            if mime_type == "text/plain":
                return "", body

        for part in payload.get("parts", []):
            body_html, body_text = self._extract_body(part)

            if body_html or body_text:
                return body_html, body_text

        return "", ""

    def get_labels(self):
        response = (
            self.service.users()
            .labels()
            .list(userId="me")
            .execute()
        )

        return response.get("labels", [])

    def get_label_id(self, label_name):
        for label in self.get_labels():
            if label["name"] == label_name:
                return label["id"]

        raise ValueError(f"Label not found: {label_name}")

    def get_messages(self, label_name):
        label_id = self.get_label_id(label_name)

        response = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                labelIds=[label_id],
            )
            .execute()
        )

        return response.get("messages", [])

    def get_message(self, message_id):
        return (
            self.service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
            .execute()
        )
        
    def get_email(self, message_id: str) -> Email:
        message = (
            self.service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
            .execute()
        )

        headers = {
            header["name"]: header["value"]
            for header in message["payload"].get("headers", [])
        }

        body_html, body_text = self._extract_body(message["payload"])

        return Email(
            gmail_id=message["id"],
            sender=headers.get("From", ""),
            subject=headers.get("Subject", ""),
            received_at=parsedate_to_datetime(headers["Date"]),
            body_html=body_html,
            body_text=body_text,
        )
        