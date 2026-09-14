from finvault.database.database import Database
from finvault.gmail.client import GmailClient


class EmailIngestor:
    def __init__(
        self,
        gmail_client: GmailClient,
        database: Database,
    ):
        self.gmail_client = gmail_client
        self.database = database

    def ingest_label(self, label_name: str):
        messages = self.gmail_client.get_messages(label_name)

        print(f"{label_name}: {len(messages)} messages")

        for message in messages:
            gmail_id = message["id"]

            if self.database.email_exists(gmail_id):
                continue

            print(f"Fetching {gmail_id}...")

            email = self.gmail_client.get_email(gmail_id)
            self.database.save_email(email, label_name)

        print(f"Processed {len(messages)} emails")
