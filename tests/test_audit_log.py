"""The audit trail is append-only and tamper-evident."""

import os
import sqlite3
import sys
import threading
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers import IsolatedDBTestCase
from data import audit_log as audit
from data import patient_db as db


class AuditLogTests(IsolatedDBTestCase):
    def _fill(self, n=4):
        for i in range(n):
            audit.record("Dr. Rao", "provider", "chart.read", resource=f"patient/{i}", patient_id=i + 1)

    def test_empty_chain_is_valid(self):
        self.assertEqual(audit.verify_chain(), {"ok": True, "entries": 0, "head": audit.GENESIS, "broken_at": None})

    def test_entries_chain_and_verify(self):
        self._fill()
        v = audit.verify_chain()
        self.assertTrue(v["ok"])
        self.assertEqual(v["entries"], 4)
        rows = list(reversed(audit.list_entries()))
        self.assertEqual(rows[0]["prev_hash"], audit.GENESIS)
        for a, b in zip(rows, rows[1:]):
            self.assertEqual(b["prev_hash"], a["hash"])

    def test_application_cannot_update_or_delete(self):
        self._fill(2)
        with db.get_conn() as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE audit_log SET actor = 'someone else' WHERE seq = 1")
        with db.get_conn() as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM audit_log WHERE seq = 1")

    def test_tampering_with_the_file_is_detected(self):
        self._fill()
        raw = sqlite3.connect(db.PATIENT_DB_PATH)  # an attacker with file access drops the triggers first
        raw.execute("DROP TRIGGER audit_log_no_update")
        raw.execute("UPDATE audit_log SET patient_id = 99 WHERE seq = 2")
        raw.commit(); raw.close()
        v = audit.verify_chain()
        self.assertFalse(v["ok"])
        self.assertEqual(v["broken_at"], 2)

    def test_deleting_an_entry_is_detected(self):
        self._fill()
        raw = sqlite3.connect(db.PATIENT_DB_PATH)
        raw.execute("DROP TRIGGER audit_log_no_delete")
        raw.execute("DELETE FROM audit_log WHERE seq = 2")
        raw.commit(); raw.close()
        self.assertEqual(audit.verify_chain()["broken_at"], 3)

    def test_detail_is_flat_and_short(self):
        audit.record("a", "admin", "x", detail={"n": 3, "nested": {"ssn": "123"}, "long": "y" * 500})
        d = audit.list_entries(1)[0]["detail"]
        self.assertNotIn("123", d)
        self.assertLess(len(d), 200)

    def test_concurrent_writers_do_not_fork_the_chain(self):
        def work():
            for _ in range(10):
                audit.record("a", "admin", "chart.read")
        threads = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        v = audit.verify_chain()
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["entries"], 40)


if __name__ == "__main__":
    unittest.main()
