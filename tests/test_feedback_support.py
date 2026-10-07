import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from tests._helpers import IsolatedDBTestCase
from workflows import feedback_support as fs


class TestTicketExtraction(unittest.TestCase):
    def test_extracts_six_digit_number(self):
        self.assertEqual(fs.extract_ticket_number("check ticket 650932 please"), "650932")

    def test_extracts_with_hash(self):
        self.assertEqual(fs.extract_ticket_number("status of #556677"), "556677")

    def test_no_number_returns_none(self):
        self.assertIsNone(fs.extract_ticket_number("what's going on with my ticket"))

    def test_ignores_non_six_digit_numbers(self):
        # a 5-digit or 7-digit number shouldn't be mistaken for a ticket number
        self.assertIsNone(fs.extract_ticket_number("I've called 3 times about this, order 12345"))


class TestHeuristicClassifier(unittest.TestCase):
    def test_spec_example_1_positive(self):
        self.assertEqual(
            fs.classify_message_heuristic("Thanks for sorting out my medication refill issue."),
            fs.POSITIVE_FEEDBACK,
        )

    def test_spec_example_2_negative(self):
        self.assertEqual(
            fs.classify_message_heuristic("My medication refill still hasn't been filled."),
            fs.NEGATIVE_FEEDBACK,
        )

    def test_spec_example_3_query(self):
        self.assertEqual(
            fs.classify_message_heuristic("Could you check the status of ticket 650932?"),
            fs.QUERY,
        )

    def test_query_language_without_ticket_number(self):
        self.assertEqual(
            fs.classify_message_heuristic("What's happening with my ticket?"),
            fs.QUERY,
        )

    def test_plain_complaint_defaults_negative(self):
        self.assertEqual(
            fs.classify_message_heuristic("This is unacceptable, nobody responded to me."),
            fs.NEGATIVE_FEEDBACK,
        )

    def test_neutral_refill_request_classifies_as_prescription_refill(self):
        self.assertEqual(
            fs.classify_message_heuristic("I need to refill my Lisinopril prescription."),
            fs.PRESCRIPTION_REFILL,
        )

    def test_renewal_phrasing_classifies_as_prescription_refill(self):
        self.assertEqual(
            fs.classify_message_heuristic("Can I get a renewal on my medication?"),
            fs.PRESCRIPTION_REFILL,
        )

    def test_complaint_about_refill_stays_negative_not_prescription_refill(self):
        # Tone (complaint) takes precedence over topic (refill) -- this is
        # the spec's own Example 2, which must route through Negative
        # Feedback first, not go straight to the refill agent.
        self.assertEqual(
            fs.classify_message_heuristic("My medication refill still hasn't been filled."),
            fs.NEGATIVE_FEEDBACK,
        )


class TestPositiveFeedbackHandler(unittest.TestCase):
    def test_format_with_name(self):
        result = fs.handle_positive_feedback("Jane")
        self.assertEqual(result["message"], "Thank you for your kind words, Jane! We're delighted to assist you.")
        self.assertEqual(result["category"], fs.POSITIVE_FEEDBACK)

    def test_format_without_name(self):
        result = fs.handle_positive_feedback(None)
        self.assertEqual(result["message"], "Thank you for your kind words! We're delighted to assist you.")


class TestNegativeFeedbackHandler(IsolatedDBTestCase):
    def test_creates_ticket_and_matches_spec_format(self):
        result = fs.handle_negative_feedback("Refill still not filled", patient_name="Jane")
        self.assertEqual(result["category"], fs.NEGATIVE_FEEDBACK)
        self.assertIsNotNone(result["ticket_number"])
        self.assertEqual(len(result["ticket_number"]), 6)
        expected = (f"We apologize for the inconvenience. A new ticket #{result['ticket_number']} "
                    f"has been generated, and our team will follow up shortly.")
        self.assertEqual(result["message"], expected)

    def test_ticket_actually_persisted(self):
        result = fs.handle_negative_feedback("Something broke")
        ticket = db.get_support_ticket(result["ticket_number"])
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["status"], "Unresolved")
        self.assertEqual(ticket["complaint_text"], "Something broke")

    def test_two_tickets_get_different_numbers(self):
        r1 = fs.handle_negative_feedback("issue one")
        r2 = fs.handle_negative_feedback("issue two")
        self.assertNotEqual(r1["ticket_number"], r2["ticket_number"])


class TestQueryHandler(IsolatedDBTestCase):
    def test_resolved_ticket_matches_spec_format(self):
        ticket_number = db.create_support_ticket("test")
        db.update_ticket_status(ticket_number, "Resolved")
        result = fs.handle_query(f"Could you check the status of ticket {ticket_number}?")
        self.assertEqual(result["message"], f"Your ticket #{ticket_number} is currently marked as: Resolved.")
        self.assertTrue(result["found"])

    def test_unresolved_ticket(self):
        ticket_number = db.create_support_ticket("test")
        result = fs.handle_query(f"status of {ticket_number}")
        self.assertEqual(result["message"], f"Your ticket #{ticket_number} is currently marked as: Unresolved.")

    def test_missing_ticket_number_in_message(self):
        result = fs.handle_query("what's going on with my ticket")
        self.assertFalse(result["found"])
        self.assertIsNone(result["ticket_number"])
        self.assertIn("couldn't find a ticket number", result["message"])

    def test_ticket_number_not_in_database(self):
        result = fs.handle_query("check ticket 999999")
        self.assertFalse(result["found"])
        self.assertIn("999999", result["message"])


class TestRouteAndHandle(IsolatedDBTestCase):
    def test_routes_positive(self):
        result = fs.route_and_handle(fs.POSITIVE_FEEDBACK, "thanks!", "Jane")
        self.assertEqual(result["category"], fs.POSITIVE_FEEDBACK)

    def test_routes_negative(self):
        result = fs.route_and_handle(fs.NEGATIVE_FEEDBACK, "this is broken", "Jane")
        self.assertEqual(result["category"], fs.NEGATIVE_FEEDBACK)
        self.assertIn("ticket_number", result)

    def test_routes_query(self):
        ticket_number = db.create_support_ticket("test")
        result = fs.route_and_handle(fs.QUERY, f"check {ticket_number}")
        self.assertEqual(result["category"], fs.QUERY)

    def test_invalid_category_raises(self):
        with self.assertRaises(ValueError):
            fs.route_and_handle("not_a_real_category", "some message")


class TestSupportTicketDB(IsolatedDBTestCase):
    def test_ticket_numbers_are_six_digits(self):
        for _ in range(5):
            tn = db.create_support_ticket("complaint")
            self.assertEqual(len(tn), 6)
            self.assertTrue(tn.isdigit())

    def test_list_filters_by_status(self):
        t1 = db.create_support_ticket("a")
        t2 = db.create_support_ticket("b")
        db.update_ticket_status(t1, "Resolved")
        unresolved = db.list_support_tickets(status="Unresolved")
        resolved = db.list_support_tickets(status="Resolved")
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(unresolved[0]["ticket_number"], t2)

    def test_update_nonexistent_ticket_returns_false(self):
        self.assertFalse(db.update_ticket_status("000000", "Resolved"))

    def test_get_nonexistent_ticket_returns_none(self):
        self.assertIsNone(db.get_support_ticket("111111"))


if __name__ == "__main__":
    unittest.main()
