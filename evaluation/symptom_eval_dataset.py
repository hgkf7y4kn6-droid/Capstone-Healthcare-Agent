"""
Ground-truth test cases for evaluating symptom-analysis accuracy.

Each case is a plausible patient-presented symptom description (deliberately
NOT naming the condition) paired with the expected diagnosis and a set of
acceptable keyword variants. These are drawn from the conditions actually
present in the patient dataset (data/source_documents/patients.json) plus a
few independent general-knowledge cases, so the same ground truth used to
populate patient records is used to test whether the assistant's medical-
information retrieval + reasoning actually lands on the right condition.

Used by evaluation/symptom_evaluator.py.
"""

SYMPTOM_TEST_CASES = [
    {
        "id": "sx_001",
        "symptom_description": (
            "A 70-year-old man with long-standing high blood pressure and diabetes has "
            "been getting routine bloodwork. His creatinine has been slowly rising and "
            "his eGFR is around 42 mL/min. He has no symptoms yet. What kidney condition "
            "would this most likely indicate?"
        ),
        "expected_condition": "Chronic Kidney Disease",
        "acceptable_keywords": ["chronic kidney disease", "ckd", "renal insufficiency", "chronic renal"],
        "source_patient": "John Smith (P001)",
    },
    {
        "id": "sx_002",
        "symptom_description": (
            "A 45-year-old woman with a known wheat/dust allergy history is short of "
            "breath with wheezing after exercise, and her peak expiratory flow reading "
            "was noticeably low today. What respiratory condition does this suggest?"
        ),
        "expected_condition": "Asthma",
        "acceptable_keywords": ["asthma", "bronchospasm", "reactive airway"],
        "source_patient": "Sarah Johnson (P002)",
    },
    {
        "id": "sx_003",
        "symptom_description": (
            "A 58-year-old man had a coronary stent placed recently and now takes dual "
            "antiplatelet therapy. He previously had exertional chest pain that led to a "
            "positive stress test. What underlying heart condition caused this?"
        ),
        "expected_condition": "Coronary Artery Disease",
        "acceptable_keywords": ["coronary artery disease", "cad", "ischemic heart disease", "atherosclerosis"],
        "source_patient": "Robert Lee (P003)",
    },
    {
        "id": "sx_004",
        "symptom_description": (
            "A 32-year-old woman has recurring, throbbing one-sided headaches, sometimes "
            "with nausea and light sensitivity, that have been increasing in frequency. "
            "What neurological condition is most consistent with this pattern?"
        ),
        "expected_condition": "Migraine",
        "acceptable_keywords": ["migraine"],
        "source_patient": "Priya Patel (P004)",
    },
    {
        "id": "sx_005",
        "symptom_description": (
            "A 65-year-old man with a long smoking history has progressive shortness of "
            "breath, a chronic productive cough, and his FEV1 is 58% predicted. What "
            "chronic lung condition does this describe?"
        ),
        "expected_condition": "COPD",
        "acceptable_keywords": ["copd", "chronic obstructive pulmonary disease", "emphysema", "chronic bronchitis"],
        "source_patient": "Michael Brown (P005)",
    },
    {
        "id": "sx_006",
        "symptom_description": (
            "A patient has sudden severe flank pain radiating to the groin, with blood in "
            "the urine, and a CT scan shows a small stone in the left ureter. What "
            "urological condition is this?"
        ),
        "expected_condition": "Kidney Stones",
        "acceptable_keywords": ["kidney stone", "nephrolithiasis", "renal calculi", "ureteral stone"],
        "source_patient": "Johnny Smith (P013)",
    },
    {
        "id": "sx_007",
        "symptom_description": (
            "A 40-year-old woman has irregular periods (sometimes absent for months), "
            "difficulty losing weight, and mild acne. What endocrine/reproductive "
            "condition commonly causes this combination?"
        ),
        "expected_condition": "Polycystic Ovary Syndrome",
        "acceptable_keywords": ["polycystic ovary syndrome", "pcos"],
        "source_patient": "Rebeca Nagle",
    },
    {
        "id": "sx_008",
        "symptom_description": (
            "A patient reports sharp, reproducible chest wall pain that worsens with deep "
            "breathing or pressing on the sternum area, with a normal cardiac workup. What "
            "musculoskeletal condition explains this?"
        ),
        "expected_condition": "Costochondritis",
        "acceptable_keywords": ["costochondritis"],
        "source_patient": "Rebeca Nagle",
    },
    {
        "id": "sx_009",
        "symptom_description": (
            "A 52-year-old woman has symmetric swelling and stiffness in the small joints "
            "of both hands, worst in the morning and lasting over an hour, with positive "
            "rheumatoid factor and anti-CCP antibodies. What autoimmune condition is this?"
        ),
        "expected_condition": "Rheumatoid Arthritis",
        "acceptable_keywords": ["rheumatoid arthritis"],
        "source_patient": "Fatima Al-Sayed (P006)",
    },
    {
        "id": "sx_010",
        "symptom_description": (
            "A 41-year-old woman has well-demarcated, red, scaly plaques on her elbows and "
            "scalp that have been chronic and relapsing. What chronic skin condition is "
            "this most consistent with?"
        ),
        "expected_condition": "Psoriasis",
        "acceptable_keywords": ["psoriasis"],
        "source_patient": "Grace Mwangi (P008)",
    },
    {
        "id": "sx_011",
        "symptom_description": (
            "A 47-year-old man has recurring abdominal cramping and bloating that improves "
            "after a bowel movement, with alternating diarrhea and constipation, and "
            "negative celiac and infection workup. What functional GI condition fits best?"
        ),
        "expected_condition": "Irritable Bowel Syndrome",
        "acceptable_keywords": ["irritable bowel syndrome", "ibs"],
        "source_patient": "Carlos Mendoza (P009)",
    },
    {
        "id": "sx_012",
        "symptom_description": (
            "A 29-year-old man has an irresistible urge to move his legs in the evening "
            "and at night, partially relieved by movement, disrupting his sleep. What "
            "neurological/sleep condition is this?"
        ),
        "expected_condition": "Restless Leg Syndrome",
        "acceptable_keywords": ["restless leg syndrome", "restless legs syndrome", "rls"],
        "source_patient": "Daniel Kim (P007)",
    },
]
