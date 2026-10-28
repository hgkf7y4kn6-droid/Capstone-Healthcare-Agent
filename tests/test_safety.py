"""The deterministic safety screen: every red flag must be caught, and
ordinary requests must pass straight through."""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workflows.safety import screen_message, is_emergency, EMERGENCY, URGENT


class SafetyScreenTests(unittest.TestCase):
    EMERGENCIES = {
        "I have crushing chest pain and my arm hurts": "chest_pain",
        "her face is drooping and she has slurred speech": "stroke",
        "I can't breathe": "breathing",
        "my throat is swelling after eating peanuts": "anaphylaxis",
        "I want to kill myself": "self_harm",
        "I don't want to be alive anymore": "self_harm",
        "my son took too many of my pills": "overdose_poisoning",
        "he's unresponsive": "bleeding_unconscious_seizure",
        "the cut won't stop bleeding": "bleeding_unconscious_seizure",
    }
    URGENT = {
        "my 2 month old baby has a fever": "infant_fever",
        "I'm pregnant and having bleeding": "pregnancy",
        "fever of 104 since last night": "high_fever",
    }
    ORDINARY = [
        "I need to book a nephrologist for my father",
        "Can I refill my lisinopril?",
        "What is chronic kidney disease?",
        "Thanks for sorting out my refill",
        "Show me my lab results",
        "I'd like a checkup next week",
    ]

    def test_emergencies_are_caught(self):
        for text, rule in self.EMERGENCIES.items():
            with self.subTest(text=text):
                hit = screen_message(text)
                self.assertIsNotNone(hit)
                self.assertEqual(hit["rule_id"], rule)
                self.assertEqual(hit["level"], EMERGENCY)
                self.assertTrue(is_emergency(text))
                self.assertTrue(hit["contacts"], "an emergency always offers a number to call")

    def test_urgent_flags(self):
        for text, rule in self.URGENT.items():
            with self.subTest(text=text):
                hit = screen_message(text)
                self.assertEqual(hit["rule_id"], rule)
                self.assertEqual(hit["level"], URGENT)

    def test_ordinary_requests_pass(self):
        for text in self.ORDINARY:
            with self.subTest(text=text):
                self.assertIsNone(screen_message(text))

    def test_self_harm_points_to_988(self):
        hit = screen_message("I keep thinking about suicide")
        self.assertIn("988", hit["guidance"])
        self.assertEqual(hit["contacts"][0]["href"], "tel:988")


if __name__ == "__main__":
    unittest.main()
