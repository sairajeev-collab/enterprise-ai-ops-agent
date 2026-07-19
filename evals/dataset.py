"""Labeled evaluation dataset.

Hand-written, realistic inbound messages with human ground-truth labels for
request type, priority, and (where present) the customer email. Small on purpose:
a curated, inspectable golden set beats a large noisy one for catching
regressions and comparing models.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    id: str
    channel: str
    subject: str
    body: str
    expected_type: str
    expected_priority: str
    expected_email: str = ""


CASES: list[EvalCase] = [
    EvalCase(
        "c01",
        "email",
        "Refund needed",
        "I need a refund for my invoice immediately, the product is not what I expected. "
        "Reach me at dana@acme.com",
        "billing",
        "urgent",
        "dana@acme.com",
    ),
    EvalCase(
        "c02",
        "email",
        "Invoice question",
        "Could you clarify a line item on my latest invoice? Thanks, sam@globex.com",
        "billing",
        "medium",
        "sam@globex.com",
    ),
    EvalCase(
        "c03",
        "support_ticket",
        "Charged twice",
        "I was charged twice for my subscription this month. This is important, please fix.",
        "billing",
        "high",
    ),
    EvalCase(
        "c04",
        "email",
        "Payment failed",
        "My payment keeps failing at checkout when I try to renew my billing plan.",
        "billing",
        "medium",
    ),
    EvalCase(
        "c05",
        "support_ticket",
        "App crash",
        "The app crashes on startup after the latest update — this is a critical outage for us.",
        "technical_support",
        "urgent",
    ),
    EvalCase(
        "c06",
        "email",
        "Bug report",
        "Found a bug: the export button throws an error. Details from raj@initech.com",
        "technical_support",
        "medium",
        "raj@initech.com",
    ),
    EvalCase(
        "c07",
        "support_ticket",
        "Feature broken",
        "The reporting feature is not working and it's blocking my team's month-end close.",
        "technical_support",
        "high",
    ),
    EvalCase(
        "c08",
        "slack",
        "Error on save",
        "Getting a 500 error every time I try to save my dashboard.",
        "technical_support",
        "medium",
    ),
    EvalCase(
        "c09",
        "support_ticket",
        "Cannot login",
        "I can't login to my account and it's blocking my work today.",
        "account",
        "high",
    ),
    EvalCase(
        "c10",
        "email",
        "Password reset",
        "Please help me reset my password, I'm locked out. My email is lee@umbrella.com",
        "account",
        "medium",
        "lee@umbrella.com",
    ),
    EvalCase(
        "c11",
        "email",
        "Update account details",
        "I'd like to update the account owner on our organization profile.",
        "account",
        "medium",
    ),
    EvalCase(
        "c12",
        "email",
        "Pricing",
        "Can you share pricing for the enterprise tier? Contact me at cfo@waystar.com",
        "sales",
        "medium",
        "cfo@waystar.com",
    ),
    EvalCase(
        "c13",
        "email",
        "Demo request",
        "We'd love a demo of your platform for our 50-person team.",
        "sales",
        "medium",
    ),
    EvalCase(
        "c14",
        "email",
        "Enterprise quote",
        "Requesting a quote for 200 seats — this is a priority for our Q3 rollout.",
        "sales",
        "high",
    ),
    EvalCase(
        "c15",
        "support_ticket",
        "Unacceptable",
        "This is unacceptable, I've waited a week with no response and I need this resolved urgently.",
        "complaint",
        "urgent",
    ),
    EvalCase(
        "c16",
        "email",
        "Poor experience",
        "I want to file a complaint about the terrible support I received. jo@hooli.com",
        "complaint",
        "medium",
        "jo@hooli.com",
    ),
    EvalCase(
        "c17",
        "email",
        "Thanks",
        "Just wanted to say thanks for the great onboarding call yesterday.",
        "other",
        "medium",
    ),
    EvalCase(
        "c18",
        "email",
        "Partnership",
        "We run a community of designers and wondered if you'd be open to a collaboration.",
        "other",
        "medium",
    ),
    EvalCase(
        "c19",
        "meeting_notes",
        "Sync notes",
        "Notes from today's sync: reviewed roadmap, agreed to revisit timelines next month.",
        "other",
        "medium",
    ),
    EvalCase(
        "c20",
        "support_ticket",
        "Chargeback",
        "I initiated a chargeback because my refund for the invoice never arrived.",
        "billing",
        "high",
    ),
    EvalCase(
        "c21",
        "slack",
        "Outage",
        "Production is down, we're seeing an outage across all regions right now.",
        "technical_support",
        "urgent",
    ),
    EvalCase(
        "c22",
        "email",
        "SSO login",
        "Our SSO login stopped working for the whole team. From: admin@stark.com",
        "account",
        "high",
        "admin@stark.com",
    ),
    EvalCase(
        "c23",
        "email",
        "Ready to buy",
        "We're ready to purchase the pro plan asap, please send the contract.",
        "sales",
        "urgent",
    ),
    EvalCase(
        "c24",
        "email",
        "General question",
        "Where can I find your data processing agreement for our legal review?",
        "other",
        "medium",
    ),
]
