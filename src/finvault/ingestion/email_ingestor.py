from finvault.gmail.client import GmailClient

class EmailIngestor:
    def __init__(self, gmail_client):
        self.gmail_client = gmail_client

    def ingest_label(self, label_name):
        messages = self.gmail_client.get_messages(label_name)

        print(f"{label_name}: {len(messages)} messages")

        for message in messages[:10]:
            full_message = self.gmail_client.get_message(message["id"])

            print("=" * 80)
            print("ID:", full_message["id"])
            print("Snippet:", full_message.get("snippet", ""))


def main():
    gmail = GmailClient()
    ingestor = EmailIngestor(gmail)

    ingestor.ingest_label("UPI")
    ingestor.ingest_label("Credit card")


if __name__ == "__main__":
    main()