import logging

from finvault.parsers.hdfc import HDFCParser

logger = logging.getLogger(__name__)


class TransactionProcessor:
    def __init__(self, database):
        self.database = database
        self.parser = HDFCParser()

    def process(self):
        emails = self.database.get_emails()

        parsed_count = 0
        failed_count = 0

        for email, source in emails:
            transaction = self.parser.parse(email, source)

            if transaction is None:
                logger.warning(
                    "Could not parse email: %s | %s",
                    email.gmail_id,
                    email.subject,
                )
                failed_count += 1
                continue

            parsed_count += 1

        logger.info(
            "Processing complete: %d parsed, %d failed",
            parsed_count,
            failed_count,
        )