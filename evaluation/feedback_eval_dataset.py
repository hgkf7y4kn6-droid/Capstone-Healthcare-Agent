"""
Ground-truth test cases for evaluating the feedback/support-ticket triage
workflow's classification accuracy and routing success rate (Part 2, step
5 of the spec: "Use QA-based scoring and test case coverage for
classification logic" + "Evaluate agent routing success rate").

Includes the three worked examples from the spec itself, plus additional
cases covering ambiguous phrasing, edge cases (no ticket number in a query,
mixed sentiment), and each category represented multiple times.
"""

from workflows.feedback_support import POSITIVE_FEEDBACK, NEGATIVE_FEEDBACK, QUERY, PRESCRIPTION_REFILL

FEEDBACK_TEST_CASES = [
    # --- From the spec's own worked examples ---
    {
        "id": "spec_example_1",
        "message": "Thanks for sorting out my medication refill issue.",
        "expected_category": POSITIVE_FEEDBACK,
    },
    {
        "id": "spec_example_2",
        "message": "My medication refill still hasn't been filled.",
        "expected_category": NEGATIVE_FEEDBACK,
    },
    {
        "id": "spec_example_3",
        "message": "Could you check the status of ticket 650932?",
        "expected_category": QUERY,
        "requires_ticket_number": "650932",
    },

    # --- Additional positive feedback cases ---
    {
        "id": "positive_2",
        "message": "You guys are amazing, this was resolved so quickly!",
        "expected_category": POSITIVE_FEEDBACK,
    },
    {
        "id": "positive_3",
        "message": "I really appreciate how helpful the support team was today.",
        "expected_category": POSITIVE_FEEDBACK,
    },

    # --- Additional negative feedback cases ---
    {
        "id": "negative_2",
        "message": "I've been waiting three weeks and nobody has responded to me.",
        "expected_category": NEGATIVE_FEEDBACK,
    },
    {
        "id": "negative_3",
        "message": "This is unacceptable, my appointment was cancelled with no notice.",
        "expected_category": NEGATIVE_FEEDBACK,
    },

    # --- Additional query cases ---
    {
        "id": "query_2",
        "message": "Any update on ticket 112233?",
        "expected_category": QUERY,
        "requires_ticket_number": "112233",
    },
    {
        "id": "query_3",
        "message": "What's the status of my ticket #556677",
        "expected_category": QUERY,
        "requires_ticket_number": "556677",
    },

    # --- Edge cases ---
    {
        "id": "edge_query_no_number",
        "message": "What's happening with my ticket?",
        "expected_category": QUERY,
        "requires_ticket_number": None,  # deliberately no number present
    },

    # --- Prescription refill requests (neutral tone, distinct from a
    # complaint ABOUT a refill, which stays negative_feedback -- see
    # spec_example_2 above) ---
    {
        "id": "refill_1",
        "message": "I need to refill my Lisinopril prescription.",
        "expected_category": PRESCRIPTION_REFILL,
    },
    {
        "id": "refill_2",
        "message": "Can I get a renewal on my medication?",
        "expected_category": PRESCRIPTION_REFILL,
    },
    {
        "id": "refill_3",
        "message": "I'm running low on my prescription and need a refill soon.",
        "expected_category": PRESCRIPTION_REFILL,
    },
]
