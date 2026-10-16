import logging

from finvault.parsers.hdfc import HDFCParser

logger = logging.getLogger(__name__)


class TransactionProcessor:
    def __init__(self, database):
        self.database = database
        self.parser = HDFCParser()

    def process(self):
        emails = self.database.get_emails(unprocessed_only=True)

        parsed_count = 0
        failed_count = 0
        skipped_count = 0

        for email, source in emails:
            transaction = self.parser.parse(email, source)

            if transaction is None:
                reason = self.parser.get_skip_reason(email)
                if reason:
                    logger.debug(
                        "Skipping email: %s | source=%s | reason=%s",
                        email.gmail_id,
                        source,
                        reason,
                    )
                    skipped_count += 1
                    continue
                logger.warning(
                    "Could not parse email: %s | %s | source=%s",
                    email.gmail_id,
                    email.subject,
                    source,
                )
                failed_count += 1
                continue

            self.database.save_transaction(transaction)
            parsed_count += 1

        logger.info(
            "Processing complete: %d parsed, %d skipped, %d failed",
            parsed_count,
            skipped_count,
            failed_count,
        )
