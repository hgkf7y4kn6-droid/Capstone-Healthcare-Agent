import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db
from tests._helpers import IsolatedDBTestCase


class RefillTestCase(IsolatedDBTestCase):
    """Refills need a visit in the last 12 months, so patients get one by default here."""

    def make_patient(self, *a, recent_visit=True, **kw):
        return super().make_patient(*a, recent_visit=recent_visit, **kw)

from workflows import prescription_refill as wf


class TestRuleEngineGates(RefillTestCase):
    def test_controlled_substance_always_fails_regardless_of_refills(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], is_controlled_substance=True,
                                     controlled_schedule="CIV", remaining_refills=5)
        result = wf.run_clinical_rule_engine(rx)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "Controlled Substance Flag")

    def test_zero_refills_fails(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], remaining_refills=0)
        result = wf.run_clinical_rule_engine(rx)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "Zero Refills Remaining")

    def test_no_lab_check_required_passes(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2, requires_lab_check=False)
        result = wf.run_clinical_rule_engine(rx)
        self.assertTrue(result["passed"])

    def test_missing_required_lab_fails(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2, requires_lab_check=True,
                                     lab_check_type="potassium", lab_safe_range="3.5-5.0")
        result = wf.run_clinical_rule_engine(rx)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "Missing Required Lab Data")

    def test_lab_value_within_range_passes(self):
        patient = self.make_patient()
        db.add_history_entry(patient["patient_id"], "lab_result",
                              {"results": {"hemoglobin": "11.2 g/dL"}}, is_structured=True)
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2, requires_lab_check=True,
                                     lab_check_type="hemoglobin", lab_safe_range="8-16")
        result = wf.run_clinical_rule_engine(rx)
        self.assertTrue(result["passed"])

    def test_lab_value_out_of_range_fails(self):
        patient = self.make_patient()
        db.add_history_entry(patient["patient_id"], "lab_result",
                              {"results": {"CRP": "18 mg/L"}}, is_structured=True)
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2, requires_lab_check=True,
                                     lab_check_type="CRP", lab_safe_range="<10")
        result = wf.run_clinical_rule_engine(rx)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "Lab Value Outside Therapeutic Threshold")

    def test_gate_order_controlled_substance_beats_missing_lab(self):
        # A controlled substance with ALSO a missing lab should report the
        # controlled-substance reason first — gate order matters.
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], is_controlled_substance=True,
                                     remaining_refills=2, requires_lab_check=True,
                                     lab_check_type="potassium", lab_safe_range="3.5-5.0")
        result = wf.run_clinical_rule_engine(rx)
        self.assertEqual(result["reason"], "Controlled Substance Flag")


class TestSafeRangeParsing(unittest.TestCase):
    def test_range_format(self):
        pred = wf._parse_safe_range("3.5-5.0")
        self.assertTrue(pred(4.0))
        self.assertFalse(pred(5.5))
        self.assertFalse(pred(3.0))

    def test_less_than_format(self):
        pred = wf._parse_safe_range("<10")
        self.assertTrue(pred(5))
        self.assertFalse(pred(10))
        self.assertFalse(pred(15))

    def test_greater_than_format(self):
        pred = wf._parse_safe_range(">60")
        self.assertTrue(pred(70))
        self.assertFalse(pred(60))
        self.assertFalse(pred(50))

    def test_parse_lab_value_extracts_number_from_units(self):
        self.assertEqual(wf._parse_lab_value("18 mg/L"), 18.0)
        self.assertEqual(wf._parse_lab_value("3.8 mmol/L"), 3.8)
        self.assertEqual(wf._parse_lab_value(11.2), 11.2)
        self.assertIsNone(wf._parse_lab_value("Positive"))


class TestGoldenPath(RefillTestCase):
    def test_golden_path_writes_queue_and_decrements_refill(self):
        patient = self.make_patient()
        rx = self.make_prescription(patient["patient_id"], remaining_refills=3)
        result = wf.golden_path_queue_injection(rx, "PHARM-001")
        self.assertIn("confirmation_id", result)
        self.assertEqual(len(db.list_erx_queue(patient["patient_id"])), 1)
        updated_rx = db.get_prescription(rx["prescription_id"])
        self.assertEqual(updated_rx["remaining_refills"], 2)


class TestContextPreAssembly(RefillTestCase):
    def test_payload_shape_and_no_pii_leak(self):
        patient = self.make_patient(address="123 Secret St", ssn_last4="9999")
        db.add_history_entry(patient["patient_id"], "visit", "Checkup")
        rx = self.make_prescription(patient["patient_id"], is_controlled_substance=True)
        rule_result = wf.run_clinical_rule_engine(rx)
        review = wf.context_pre_assembly(rx, rule_result)

        payload = review["context_payload"]
        self.assertEqual(payload["failed_rule"], "Controlled Substance Flag")
        self.assertIn("patient_profile", payload)
        self.assertIn("medication_reconciliation", payload)
        self.assertIn("last_office_visit", payload)

        # Data minimization: address/SSN must never appear in the provider payload.
        payload_str = str(payload)
        self.assertNotIn("123 Secret St", payload_str)
        self.assertNotIn("9999", payload_str)

        # Also confirm it was actually persisted to the review queue.
        reviews = db.list_provider_review(patient["patient_id"])
        self.assertEqual(len(reviews), 1)


class TestOrchestration(RefillTestCase):
    def test_process_refill_request_dob_only_golden_path(self):
        patient = self.make_patient(dob="1979-03-15")
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2)
        result = wf.process_refill_request(patient["patient_id"], "1979-03-15",
                                            rx["prescription_id"], "PHARM-001")
        self.assertEqual(result["status"], "processed")
        self.assertIn("confirmation_id", result)

    def test_process_refill_request_wrong_dob_unauthorized(self):
        patient = self.make_patient(dob="1979-03-15")
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2)
        result = wf.process_refill_request(patient["patient_id"], "1900-01-01",
                                            rx["prescription_id"], "PHARM-001")
        self.assertEqual(result["status"], "unauthorized")

    def test_process_refill_request_names_failed_extra_factor(self):
        patient = self.make_patient(dob="1979-03-15", address="1 Main St", ssn_last4="1234")
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2)
        result = wf.process_refill_request(patient["patient_id"], "1979-03-15",
                                            rx["prescription_id"], "PHARM-001",
                                            address="1 Main St", ssn_last4="0000")
        self.assertEqual(result["status"], "unauthorized")
        self.assertIn("ssn_last4", result["message"])

    def test_process_refill_request_routes_controlled_substance_to_review(self):
        patient = self.make_patient(dob="1979-03-15")
        rx = self.make_prescription(patient["patient_id"], is_controlled_substance=True, remaining_refills=2)
        result = wf.process_refill_request(patient["patient_id"], "1979-03-15",
                                            rx["prescription_id"], "PHARM-001")
        self.assertEqual(result["status"], "routed_to_review")
        self.assertEqual(result["reason"], "Controlled Substance Flag")

    def test_process_refill_request_unknown_prescription_errors(self):
        patient = self.make_patient(dob="1979-03-15")
        result = wf.process_refill_request(patient["patient_id"], "1979-03-15", 99999, "PHARM-001")
        self.assertEqual(result["status"], "error")

    def test_authenticated_path_skips_dob_but_still_runs_rule_engine(self):
        patient = self.make_patient(dob="1979-03-15")
        # Controlled substance should still be caught even though no DOB was checked.
        rx = self.make_prescription(patient["patient_id"], is_controlled_substance=True, remaining_refills=2)
        result = wf.process_refill_request_authenticated(patient["patient_id"], rx["prescription_id"], "PHARM-001")
        self.assertEqual(result["status"], "routed_to_review")
        self.assertEqual(result["reason"], "Controlled Substance Flag")

    def test_authenticated_path_golden_path(self):
        patient = self.make_patient(dob="1979-03-15")
        rx = self.make_prescription(patient["patient_id"], remaining_refills=2)
        result = wf.process_refill_request_authenticated(patient["patient_id"], rx["prescription_id"], "PHARM-001")
        self.assertEqual(result["status"], "processed")


if __name__ == "__main__":
    unittest.main()


class TestVisitAndAllergyGates(RefillTestCase):
    def test_no_visit_on_record_routes_to_review(self):
        p = self.make_patient(recent_visit=False)
        rx = self.make_prescription(p["patient_id"])
        r = wf.run_clinical_rule_engine(rx)
        self.assertFalse(r["passed"])
        self.assertEqual(r["reason"], "No Recent Visit")

    def test_visit_older_than_12_months_routes_to_review(self):
        p = self.make_patient(recent_visit=False)
        db.add_history_entry(p["patient_id"], "visit", {"reason": "old"}, True, created_at="2020-01-01T00:00:00+00:00")
        r = wf.run_clinical_rule_engine(self.make_prescription(p["patient_id"]))
        self.assertEqual(r["reason"], "No Recent Visit")

    def test_recent_visit_passes(self):
        p = self.make_patient()
        self.assertTrue(wf.run_clinical_rule_engine(self.make_prescription(p["patient_id"]))["passed"])

    def test_documented_allergy_routes_to_review(self):
        p = self.make_patient()
        db.add_history_entry(p["patient_id"], "alert", {"allergy": "Penicillin", "severity": "unspecified"}, True)
        for med in ("Amoxicillin 500mg", "Penicillin VK"):
            r = wf.run_clinical_rule_engine(self.make_prescription(p["patient_id"], medication_name=med))
            self.assertEqual(r["reason"], "Documented Allergy Match", med)
        ok = wf.run_clinical_rule_engine(self.make_prescription(p["patient_id"], medication_name="Lisinopril 10mg"))
        self.assertTrue(ok["passed"])
