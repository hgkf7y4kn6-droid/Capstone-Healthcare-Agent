"""
One-time (idempotent) data import: loads the structured patient roster in
patients.json plus the scanned clinical PDFs in docs/ into:
  1. The local SQLite EHR (data/patient_db.py)
  2. The long-term FAISS memory store (memory/vector_memory.py), so the
     agent can retrieve relevant patient context during conversations.

Usage:
    python -m data.ingest_patient_data
    # or point at a different export:
    python -m data.ingest_patient_data --source /path/to/Agentic_healthcare_data

Safe to re-run: patients are matched/updated by external_id, and history
entries are de-duplicated by (patient_id, entry_type, content).
"""

import os
import sys
import re
import json
import argparse
import subprocess
import datetime as dt
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import patient_db as db

DEFAULT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source_documents")


# ---------------------------------------------------------------------------
# PDF text extraction (subprocess pdftotext if available, else pypdf, else stdlib)
# ---------------------------------------------------------------------------

def _extract_pdf_text_stdlib(path: str) -> str:
    """Fallback text extractor using only standard library (base64, zlib, re)
    to parse unencrypted Flate/ASCII85 streams from PDFs without external tools."""
    import base64
    import zlib
    try:
        with open(path, "rb") as f:
            data = f.read()
        extracted = []
        for match in re.finditer(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\n?endstream", data, re.DOTALL):
            header, body = match.group(1), match.group(2)
            try:
                if b"ASCII85Decode" in header and b"~>" in body:
                    body = base64.a85decode(body[:body.find(b"~>") + 2], adobe=True)
                if b"FlateDecode" in header:
                    body = zlib.decompress(body)
                chunks = []
                for tm in re.finditer(rb"\((.*?)\)\s*(?:Tj|\'|\")|\[(.*?)\]\s*TJ", body, re.DOTALL):
                    if tm.group(1) is not None:
                        chunk = tm.group(1).replace(rb"\(", b"(").replace(rb"\)", b")").decode("latin1", "ignore")
                        chunks.append(chunk)
                    elif tm.group(2) is not None:
                        parts = []
                        for part in re.finditer(rb"\((.*?)\)", tm.group(2), re.DOTALL):
                            parts.append(part.group(1).replace(rb"\(", b"(").replace(rb"\)", b")").decode("latin1", "ignore"))
                        chunks.append("".join(parts))
                if chunks:
                    extracted.append(" ".join(chunks))
            except Exception:
                pass
        result = "\n".join(extracted)
        return re.sub(r"[ \t]+", " ", result)
    except Exception:
        return ""


def extract_pdf_text(path: str) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        if result.stdout.strip():
            return result.stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        if extracted.strip():
            return extracted
    except Exception:
        pass

    # Pure standard library fallback (handles ReportLab and standard unencrypted PDFs)
    stdlib_text = _extract_pdf_text_stdlib(path)
    if stdlib_text.strip():
        return stdlib_text

    return f"[Could not extract text from {os.path.basename(path)}]"


# ---------------------------------------------------------------------------
# Step 1: import patients.json (structured roster)
# ---------------------------------------------------------------------------

def import_patients_json(path: str) -> dict:
    """Returns a map of external_id ('P001') -> local patient_id (int)."""
    if not os.path.exists(path):
        print(f"  (no patients.json found at {path}, skipping)")
        return {}

    with open(path, "r") as f:
        roster = json.load(f)

    id_map = {}
    for external_id, p in roster.items():
        dob = p.get("dob") or _approximate_dob(p.get("age"))
        patient_id = db.upsert_patient(
            full_name=p.get("name", "Unknown"),
            external_id=external_id,
            age=p.get("age"),
            sex=p.get("gender"),
            relation_to_user="self",
            contact=p.get("contact"),
            email=p.get("email"),
            blood_type=p.get("blood_type"),
            primary_doctor=p.get("doctor"),
            next_appointment=p.get("next_appointment"),
            dob=dob,
            address=p.get("address"),
            ssn_last4=p.get("ssn_last4"),
        )
        id_map[external_id] = patient_id
        print(f"  Upserted {p.get('name')} ({external_id}) -> patient_id={patient_id}")

        for condition in p.get("conditions", []):
            db.add_history_entry(patient_id, "diagnosis", {"condition": condition},
                                  is_structured=True, skip_if_duplicate=True)

        for med in p.get("medications", []):
            db.add_history_entry(patient_id, "treatment", {"medication": med},
                                  is_structured=True, skip_if_duplicate=True)

        for allergy in p.get("allergies", []):
            db.add_history_entry(patient_id, "alert", {"allergy": allergy, "severity": "unspecified"},
                                  is_structured=True, skip_if_duplicate=True)

        if p.get("lab_results"):
            db.add_history_entry(
                patient_id, "lab_result", {"results": p["lab_results"], "as_of": p.get("last_visit")},
                is_structured=True, created_at=_to_iso(p.get("last_visit")), skip_if_duplicate=True,
            )

        for visit in p.get("visit_history", []):
            db.add_history_entry(
                patient_id, "visit",
                {"reason": visit.get("reason"), "notes": visit.get("notes"), "date": visit.get("date")},
                is_structured=True, created_at=_to_iso(visit.get("date")), skip_if_duplicate=True,
            )

        if p.get("emergency_contact"):
            db.add_history_entry(patient_id, "emergency_contact", p["emergency_contact"],
                                  is_structured=True, skip_if_duplicate=True)

        for note in p.get("notes", []):
            ts = _to_iso(note.get("date"), note.get("time"))
            db.add_history_entry(patient_id, "note", note.get("note", ""),
                                  is_structured=False, created_at=ts, skip_if_duplicate=True)

        for doc in p.get("documents", []):
            ts = _to_iso(doc.get("date"), doc.get("time"))
            db.add_history_entry(
                patient_id, "note",
                f"Document on file: {doc.get('filename')} ({doc.get('type', 'uploaded_document')})",
                is_structured=False, created_at=ts, skip_if_duplicate=True,
            )

        if p.get("next_appointment"):
            db.record_appointment(
                patient_id=patient_id,
                doctor_name=p.get("doctor", "Unknown"),
                specialty=_extract_specialty(p.get("doctor", "")),
                calcom_booking_uid=None,
                start_time=p["next_appointment"],
                status="scheduled (imported)",
                skip_if_duplicate=True,
            )

    return id_map


def _extract_specialty(doctor_field: str) -> str:
    m = re.search(r"\(([^)]+)\)", doctor_field or "")
    return m.group(1) if m else ""


def _approximate_dob(age) -> Optional[str]:
    """Used only when the source record has no explicit DOB. Produces a
    synthetic-but-plausible DOB (Jan 1 of the birth year) so identity
    verification has something to check against — clearly an approximation,
    not a real recorded birthdate."""
    if not age:
        return None
    birth_year = dt.datetime.now(dt.timezone.utc).year - int(age)
    return f"{birth_year}-01-01"


def _to_iso(date_str, time_str=None) -> str:
    if not date_str:
        return dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        if time_str:
            d = dt.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        else:
            d = dt.datetime.strptime(date_str, "%Y-%m-%d")
        return d.replace(tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Step 2: import clinical PDFs (lab reports / H&P notes)
# ---------------------------------------------------------------------------

LAB_REPORT_PATTERNS = {
    "name": re.compile(r"Patient Name:\s*([A-Za-z .'-]+?)\s{2,}"),
    "external_id": re.compile(r"Patient ID:\s*(P\d+)"),
    "dob": re.compile(r"Date of Birth:\s*([A-Za-z]+ \d{1,2}, \d{4})"),
    "gender": re.compile(r"Gender:\s*(Male|Female|Other)"),
    "doctor": re.compile(r"Ordering Doctor:\s*([A-Za-z. ]+?)\s{2,}"),
    "specialty": re.compile(r"Specialty:\s*([A-Za-z]+)"),
}

HP_NOTE_PATTERNS = {
    "name": re.compile(r"Patient:\s*([A-Za-z .'-]+?)(?:\s+(?:DOB|Gender|Phone|Address|Visit)|\n|$)"),
    "name_fallback": re.compile(r"([A-Z][a-zA-Z'-]+ [A-Z][a-zA-Z'-]+)\s+(?:\n\s*)?\d{1,2}/\d{1,2}/\d{4}"),
    "dob": re.compile(r"DOB:\s*([\d/]+)"),
    "gender": re.compile(r"Gender:\s*(Male|Female|Other)"),
}


def _find_or_register_patient(name: str, external_id: str = None, dob: str = None,
                               gender: str = None, source_filename: str = "") -> int:
    if external_id:
        existing = db.get_patient_by_external_id(external_id)
        if existing:
            return existing["patient_id"]

    existing = db.find_patient(name) if name else None
    if existing:
        return existing["patient_id"]

    # Truly new patient discovered only via an uploaded document.
    new_external_id = external_id or f"DOC-{re.sub(r'[^A-Za-z0-9]', '', name)[:12]}"
    patient_id = db.upsert_patient(
        full_name=name or "Unknown Patient",
        external_id=new_external_id,
        sex=gender,
        relation_to_user="self",
    )
    print(f"  Registered new patient discovered in {source_filename}: {name} "
          f"({new_external_id}) -> patient_id={patient_id}")
    return patient_id


def import_pdf_documents(docs_dir: str) -> None:
    if not os.path.isdir(docs_dir):
        print(f"  (no docs/ directory found at {docs_dir}, skipping)")
        return

    seen_lab_content = {}  # detect the identical-boilerplate issue across files

    for filename in sorted(os.listdir(docs_dir)):
        if not filename.lower().endswith(".pdf"):
            continue
        path = os.path.join(docs_dir, filename)
        text = extract_pdf_text(path)

        if not text or text.startswith("[Could not extract text from"):
            print(f"  [!] Could not extract text from '{filename}', skipping.")
            continue

        if "Laboratory Investigation Report" in text:
            name_m = LAB_REPORT_PATTERNS["name"].search(text)
            id_m = LAB_REPORT_PATTERNS["external_id"].search(text)
            dob_m = LAB_REPORT_PATTERNS["dob"].search(text)
            gender_m = LAB_REPORT_PATTERNS["gender"].search(text)

            name = name_m.group(1).strip() if name_m else None
            external_id = id_m.group(1).strip() if id_m else None
            gender = gender_m.group(1) if gender_m else None

            patient_id = _find_or_register_patient(name, external_id, dob_m and dob_m.group(1), gender, filename)

            # flag if this exact report body has already been seen under a different patient
            # (strip all identifying header fields so we're comparing the actual clinical content)
            body_fingerprint = re.sub(
                r"(Patient Name|Patient ID|Date of Birth|Ordering Doctor):.*", "", text
            )[:3000]
            if body_fingerprint in seen_lab_content and seen_lab_content[body_fingerprint] != name:
                print(f"  [!] DATA QUALITY FLAG: '{filename}' has identical lab content to the report "
                      f"already ingested for '{seen_lab_content[body_fingerprint]}' — likely a reused "
                      f"template/test fixture, not real per-patient results. Stored as-is with a caveat.")
                caveat = ("CAVEAT: This report's content is byte-identical to another patient's report "
                          "in the source data — likely a test-fixture artifact, not verified per-patient "
                          "lab results. Treat clinically with caution.\n\n")
                text = caveat + text
            seen_lab_content[body_fingerprint] = name

            db.add_history_entry(patient_id, "lab_result", text, is_structured=False,
                                  skip_if_duplicate=True)
            print(f"  Ingested lab report '{filename}' -> patient_id={patient_id} ({name})")

        elif "History and Physical Note" in text or "Subjective Notes" in text:
            name_m = HP_NOTE_PATTERNS["name"].search(text) or HP_NOTE_PATTERNS["name_fallback"].search(text)
            gender_m = HP_NOTE_PATTERNS["gender"].search(text)
            name = name_m.group(1).strip() if name_m else os.path.splitext(filename)[0]
            gender = gender_m.group(1) if gender_m else None

            patient_id = _find_or_register_patient(name, None, None, gender, filename)
            db.add_history_entry(patient_id, "clinical_note", text, is_structured=False,
                                  skip_if_duplicate=True)
            print(f"  Ingested clinical note '{filename}' -> patient_id={patient_id} ({name})")

        else:
            print(f"  [!] Unrecognized document format for '{filename}' — skipped structured parsing, "
                  f"stored as a generic note instead.")
            patient_id = _find_or_register_patient(os.path.splitext(filename)[0], None, None, None, filename)
            db.add_history_entry(patient_id, "note", text, is_structured=False, skip_if_duplicate=True)


# ---------------------------------------------------------------------------
# Step 3: push summaries into long-term (FAISS) memory
# ---------------------------------------------------------------------------

def index_into_vector_memory() -> None:
    try:
        from memory.vector_memory import add_patient_summary
    except ImportError as e:
        print(f"  Skipping vector memory indexing — dependency not installed ({e}). "
              f"Run `pip install -r requirements.txt` then re-run this script.")
        return

    for patient in db.list_patients():
        history = db.get_history(patient["patient_id"])
        if not history:
            continue
        lines = [f"Patient: {patient['full_name']} (age {patient.get('age')}, {patient.get('sex')})"]
        for h in history:
            if h["entry_type"] in ("diagnosis", "treatment", "alert"):
                lines.append(f"- {h['entry_type']}: {h['content']}")
        summary = "\n".join(lines)
        try:
            add_patient_summary(patient["patient_id"], patient["full_name"], summary, tag="imported_profile")
        except Exception as e:
            print(f"  Could not index patient {patient['full_name']} into vector memory: {e}")
            return
    print("  Vector memory indexing complete.")


# ---------------------------------------------------------------------------
# Step 4: validate no duplicate ailments across patients
# ---------------------------------------------------------------------------

def validate_no_duplicate_conditions() -> bool:
    """Scan every patient's structured diagnosis entries and report any
    condition string that appears for more than one patient. Returns True
    if the dataset is clean (no duplicates)."""
    condition_to_patients = {}
    for patient in db.list_patients():
        history = db.get_history(patient["patient_id"])
        for h in history:
            if h["entry_type"] != "diagnosis":
                continue
            content = h["content"]
            condition = content.get("condition") if isinstance(content, dict) else str(content)
            if not condition:
                continue
            key = condition.strip().lower()
            condition_to_patients.setdefault(key, set()).add(patient["full_name"])

    duplicates = {cond: names for cond, names in condition_to_patients.items() if len(names) > 1}
    if duplicates:
        print("  [!] DUPLICATE CONDITIONS DETECTED across patients:")
        for cond, names in duplicates.items():
            print(f"    - '{cond}' shared by: {', '.join(sorted(names))}")
        return False
    print(f"  No duplicate conditions found across {len(condition_to_patients)} unique ailments "
          f"and {len(db.list_patients())} patients.")
    return True


# ---------------------------------------------------------------------------
# Step 3b: seed a demonstration prescription roster
# ---------------------------------------------------------------------------

def seed_prescriptions(id_map: dict) -> None:
    """Seeds a small set of prescriptions per patient deliberately covering
    all four branches of the refill pipeline: golden path (no lab check),
    golden path (lab check passed), zero-refills failure, controlled-
    substance failure, missing-lab failure, and out-of-range-lab failure.
    Matched/skipped by (patient, medication) so re-running is idempotent."""
    if not id_map:
        return

    def pid(external_id):
        return id_map.get(external_id)

    seeds = [
        # (external_id, medication, controlled, schedule, refills, requires_lab, lab_type, safe_range, doctor)
        # --- Golden path: no lab check required ---
        ("P001", "Metformin 500mg", False, None, 3, False, None, None, "Dr. Mehta"),
        ("P002", "Sertraline 25mg", False, None, 2, False, None, None, "Dr. Sharma"),
        ("P003", "Atorvastatin 40mg", False, None, 3, False, None, None, "Dr. Nair"),
        ("P009", "Omeprazole 20mg", False, None, 2, False, None, None, "Dr. Castillo"),

        # --- Golden path: lab check required AND satisfied ---
        ("P004", "Ferrous Sulfate 325mg", False, None, 2, True, "hemoglobin", "8-16", "Dr. Singh"),

        # --- Failure: zero refills remaining ---
        ("P002", "Albuterol inhaler", False, None, 0, False, None, None, "Dr. Sharma"),
        ("P008", "Topical Calcipotriene", False, None, 0, False, None, None, "Dr. Osei"),

        # --- Failure: controlled substance (always manual review) ---
        ("P010", "Lorazepam 0.5mg", True, "CIV", 2, False, None, None, "Dr. Whitfield"),
        ("P005", "Codeine/Guaifenesin Syrup", True, "CV", 1, False, None, None, "Dr. Kumar"),

        # --- Failure: missing required lab data ---
        ("P001", "Lisinopril 10mg", False, None, 2, True, "potassium", "3.5-5.0", "Dr. Mehta"),

        # --- Failure: lab value out of therapeutic range ---
        ("P006", "Methotrexate 15mg weekly", False, None, 3, True, "CRP", "<10", "Dr. Iyer"),
    ]

    for external_id, med, controlled, schedule, refills, needs_lab, lab_type, lab_range, doctor in seeds:
        patient_id = pid(external_id)
        if not patient_id:
            continue
        result = db.add_prescription(
            patient_id=patient_id, medication_name=med, is_controlled_substance=controlled,
            controlled_schedule=schedule, remaining_refills=refills, requires_lab_check=needs_lab,
            lab_check_type=lab_type, lab_safe_range=lab_range, prescribing_doctor=doctor,
            skip_if_duplicate=True,
        )
        if result:
            print(f"  Seeded prescription: {med} for {external_id}")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import patient data into the healthcare assistant.")
    parser.add_argument("--source", default=DEFAULT_SOURCE,
                         help="Path to the extracted Agentic_healthcare_data folder "
                              "(containing patients.json and docs/)")
    args = parser.parse_args()

    db.init_db(seed=False)  # do not seed the old demo patient over real data

    print(f"\n[1/5] Importing structured roster from {os.path.join(args.source, 'patients.json')} ...")
    id_map = import_patients_json(os.path.join(args.source, "patients.json"))

    print(f"\n[2/5] Importing clinical PDFs from {os.path.join(args.source, 'docs')} ...")
    import_pdf_documents(os.path.join(args.source, "docs"))

    print("\n[3/5] Seeding demonstration prescriptions (refill automation pipeline) ...")
    seed_prescriptions(id_map)

    print("\n[4/5] Indexing patient summaries into long-term vector memory ...")
    index_into_vector_memory()

    print("\n[5/5] Validating dataset for duplicate ailments across patients ...")
    validate_no_duplicate_conditions()

    print("\nDone. Patients now in the system:")
    for p in db.list_patients():
        print(f"  - {p['full_name']} ({p.get('external_id', 'no external id')}), patient_id={p['patient_id']}")


if __name__ == "__main__":
    main()
