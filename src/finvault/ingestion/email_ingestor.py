import logging

from finvault.database.database import Database
from finvault.gmail.client import GmailClient

logger = logging.getLogger(__name__)

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

        logger.info(
            "%s: %d messages found",
            label_name,
            len(messages),
        )

        new_count = 0
        existing_count = 0

        for message in messages:
            gmail_id = message["id"]

            if self.database.email_exists(gmail_id):
                existing_count += 1
                continue

            logger.debug("Fetching email %s", gmail_id)

            email = self.gmail_client.get_email(gmail_id)
            self.database.save_email(email, label_name)
            new_count += 1

        logger.info(
            "%s: %d new emails ingested, %d already present",
            label_name,
            new_count,
            existing_count,
        )
