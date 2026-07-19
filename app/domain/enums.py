"""Domain enumerations.

These are the controlled vocabularies of the system. Using ``StrEnum`` means they
serialize to plain strings over the API and into the database while still giving
us exhaustiveness checking in the type checker.
"""

from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    """Where an inbound request originated."""

    EMAIL = "email"
    SUPPORT_TICKET = "support_ticket"
    SLACK = "slack"
    PDF = "pdf"
    INVOICE = "invoice"
    MEETING_NOTES = "meeting_notes"


class RequestType(StrEnum):
    """The classified intent of an inbound request."""

    BILLING = "billing"
    TECHNICAL_SUPPORT = "technical_support"
    ACCOUNT = "account"
    SALES = "sales"
    COMPLAINT = "complaint"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class RunStatus(StrEnum):
    """Lifecycle of a request as it moves through the pipeline."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


# Jira-facing priority mapping kept next to the enum it derives from, so the two
# never drift silently.
JIRA_PRIORITY_BY_DOMAIN: dict[Priority, str] = {
    Priority.LOW: "Low",
    Priority.MEDIUM: "Medium",
    Priority.HIGH: "High",
    Priority.URGENT: "Highest",
}
