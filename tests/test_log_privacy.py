"""Log scanner: no message text, chart text or tool output may reach the log files."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase
from data import patient_db as db
from monitoring import logging_utils, monitor
from agents import concierge
from agents.assistant import answer
from agents.feedback_crew import run_feedback_workflow

SENTINEL = "ZQX-PRIVATE-7731"


class LogPrivacyTests(IsolatedDBTestCase):
    def setUp(self):
        super().setUp()
        d = tempfile.mkdtemp()
        self.agent_log, self.mon_log = os.path.join(d, "a.jsonl"), os.path.join(d, "m.jsonl")
        self._p = [mock.patch.object(logging_utils, "AGENT_LOG_PATH", self.agent_log),
                   mock.patch.object(monitor, "MONITORING_LOG_PATH", self.mon_log),
                   mock.patch.object(concierge, "_medlineplus_lookup", return_value=[])]
        for p in self._p:
            p.start()
        self.patient = self.make_patient(name="Ada Lovelace", dob="1965-12-10")

    def tearDown(self):
        for p in self._p:
            p.stop()
        super().tearDown()

    def _logs(self) -> str:
        return "".join(open(f).read() for f in (self.agent_log, self.mon_log) if os.path.exists(f))

    def test_concierge_logs_no_message_text(self):
        answer(f"Tell me about my {SENTINEL} diabetes and book a cardiologist", self.patient["patient_id"], engine="concierge")
        logs = self._logs()
        self.assertTrue(logs)
        self.assertNotIn(SENTINEL, logs)
        self.assertNotIn("diabetes", logs)

    def test_feedback_workflow_logs_no_message_text(self):
        run_feedback_workflow(f"Thanks so much, {SENTINEL} was great", patient_name="Ada Lovelace")
        run_feedback_workflow(f"The wait was terrible {SENTINEL}", patient_name="Ada Lovelace")
        self.assertNotIn(SENTINEL, self._logs())

    def test_emergency_logs_rule_only(self):
        run_feedback_workflow(f"I have chest pain {SENTINEL}", patient_name="Ada Lovelace")
        logs = self._logs()
        self.assertIn("chest_pain", logs)
        self.assertNotIn(SENTINEL, logs)
        self.assertNotIn("chest pain", logs)

    def test_scrub_keeps_length_not_content(self):
        logging_utils.log_event("response", {"user_query": SENTINEL, "ok": True})
        rec = json.loads(open(self.agent_log).read().splitlines()[-1])
        self.assertEqual(rec["user_query"], {"chars": len(json.dumps(SENTINEL))})
        self.assertTrue(rec["ok"])


if __name__ == "__main__":
    unittest.main()
