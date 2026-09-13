from finvault.gmail.client import GmailClient


class EmailIngestor:
    def __init__(self, gmail_client: GmailClient):
        self.gmail_client = gmail_client

    def ingest_label(self, label_name: str):
        messages = self.gmail_client.get_messages(label_name)

        print(f"{label_name}: {len(messages)} messages")

        for message in messages[:10]:
            email = self.gmail_client.get_email(message["id"])

            print("=" * 80)
            print("ID:", email.gmail_id)
            print("Date:", email.received_at)
            print("From:", email.sender)
            print("Subject:", email.subject)
            print("Body:")
            print(email.body_text[:500])
            