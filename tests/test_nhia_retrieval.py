import unittest

from agent import agent
from agent.clinical_guidelines.retrieval import resolve_diagnoses, retrieve_guidance_for_pa


class NHIARetrievalTests(unittest.TestCase):
    def test_exact_icd_codes_are_resolved_locally(self):
        resolved = resolve_diagnoses({"diagnosis": ["J18.9", "E11.9", "N11.1"]})
        self.assertEqual([row["description"] for row in resolved], [
            "Pneumonia, unspecified organism",
            "Type 2 diabetes mellitus without complications",
            "Chronic obstructive pyelonephritis",
        ])
        self.assertTrue(all(row["resolved"] for row in resolved))

    def test_dotless_icd_code_is_canonicalized(self):
        resolved = resolve_diagnoses({"diagnosis": ["J189"]})[0]
        self.assertEqual(resolved["code"], "J18.9")
        self.assertEqual(resolved["description"], "Pneumonia, unspecified organism")

    def test_icd_only_pneumonia_retrieves_respiratory_investigation(self):
        result = retrieve_guidance_for_pa({
            "diagnosis": ["J18.9"],
            "items": [{"claim_item_id": 1, "item_name": "Chest X-ray"}],
        })
        evidence = result["item_evidence"][0]["evidence"]
        self.assertTrue(any(row["chapter_number"] == 22 for row in evidence))
        self.assertTrue(any(row["field"] == "investigation" for row in evidence))
        self.assertTrue(any(row["pdf_page"] == 262 for row in evidence))
        self.assertEqual(evidence[0]["field"], "investigation")

    def test_diabetes_hba1c_retrieves_diabetes_page(self):
        result = retrieve_guidance_for_pa({
            "diagnosis": ["E11.9"],
            "items": [{"claim_item_id": 2, "item_name": "HbA1c test"}],
        })
        evidence = result["item_evidence"][0]["evidence"]
        self.assertEqual(evidence[0]["chapter_number"], 16)
        self.assertEqual(evidence[0]["pdf_page"], 186)
        self.assertEqual(evidence[0]["field"], "investigation")

    def test_references_are_page_traceable(self):
        result = retrieve_guidance_for_pa({
            "diagnosis": ["N11.1"],
            "items": [{"claim_item_id": 3, "item_name": "Abdominal Scan"}],
        })
        evidence = result["item_evidence"][0]["evidence"]
        self.assertTrue(evidence)
        for row in evidence:
            self.assertTrue(row["section_id"].startswith("nhia-book-3-p"))
            self.assertIsInstance(row["pdf_page"], int)
            self.assertIsInstance(row["printed_page"], int)
            self.assertTrue(row["chapter"])

    def test_model_cannot_invent_source_references(self):
        pa = {
            "diagnosis": ["J18.9"],
            "items": [{"claim_item_id": 4, "item_name": "Chest X-ray", "status": "pending"}],
        }
        retrieval = retrieve_guidance_for_pa(pa)
        valid_id = retrieval["item_evidence"][0]["evidence"][0]["section_id"]
        normalized = agent._normalize_clinical_review(pa, retrieval, {
            "item_assessments": [{
                "claim_item_id": 4,
                "item_name": "Chest X-ray",
                "clinical_status": "SUPPORTED",
                "diagnosis_code": "J18.9",
                "diagnosis_description": "Pneumonia, unspecified organism",
                "rationale": "Evidence supports the investigation.",
                "confidence": "HIGH",
                "evidence_section_ids": [valid_id, "invented-section"],
            }]
        })
        references = normalized["item_assessments"][0]["references"]
        self.assertEqual([reference["section_id"] for reference in references], [valid_id])

    def test_multi_diagnosis_retrieval_keeps_evidence_scoped(self):
        result = retrieve_guidance_for_pa({
            "diagnosis": ["J18.9", "E11.9", "N11.1"],
            "items": [
                {"claim_item_id": 11, "item_name": "Chest X-ray"},
                {"claim_item_id": 12, "item_name": "HbA1c test"},
                {"claim_item_id": 13, "item_name": "Urinalysis"},
            ],
        })
        rows = {row["item_name"]: row for row in result["item_evidence"]}
        by_code = lambda row: {candidate["diagnosis_code"]: candidate for candidate in row["diagnosis_candidates"]}
        self.assertTrue(any(e["pdf_page"] == 262 for e in by_code(rows["Chest X-ray"])["J18.9"]["evidence"]))
        self.assertTrue(any(e["pdf_page"] == 186 for e in by_code(rows["HbA1c test"])["E11.9"]["evidence"]))
        self.assertTrue(all(e["chapter_number"] == 21 for e in by_code(rows["Urinalysis"])["N11.1"]["evidence"]))

    def test_citation_from_another_diagnosis_is_rejected(self):
        pa = {
            "diagnosis": ["J18.9", "E11.9"],
            "items": [{"claim_item_id": 14, "item_name": "Chest X-ray", "status": "pending"}],
        }
        retrieval = retrieve_guidance_for_pa(pa)
        candidates = retrieval["item_evidence"][0]["diagnosis_candidates"]
        diabetes_id = next(row for row in candidates if row["diagnosis_code"] == "E11.9")["evidence"][0]["section_id"]
        normalized = agent._normalize_clinical_review(pa, retrieval, {"item_assessments": [{
            "claim_item_id": 14,
            "item_name": "Chest X-ray",
            "diagnosis_code": "J18.9",
            "clinical_status": "SUPPORTED",
            "rationale": "Invalid cross-diagnosis citation.",
            "confidence": "HIGH",
            "evidence_section_ids": [diabetes_id],
        }]})
        assessment = normalized["item_assessments"][0]
        self.assertEqual(assessment["clinical_status"], "INSUFFICIENT_INFORMATION")
        self.assertEqual(assessment["references"], [])

    def test_supported_review_keeps_approval_and_adds_rationale(self):
        decisions = [{"claim_item_id": 5, "item_name": "FBC", "decision": "APPROVE"}]
        agent._apply_clinical_review(decisions, {"item_assessments": [{
            "claim_item_id": 5,
            "item_name": "FBC",
            "clinical_status": "SUPPORTED",
            "rationale": "FBC is listed as an investigation.",
            "references": [{"chapter": "Respiratory System Conditions", "printed_page": 260}],
        }]})
        self.assertEqual(decisions[0]["decision"], "APPROVE")
        self.assertIn("NHIA Book 3", decisions[0]["reason"])

    def test_not_supported_review_rejects_line(self):
        decisions = [{"claim_item_id": 6, "item_name": "Unsupported test", "decision": "APPROVE", "recommended_approved_cost": 1000}]
        agent._apply_clinical_review(decisions, {"item_assessments": [{
            "claim_item_id": 6, "item_name": "Unsupported test", "clinical_status": "NOT_SUPPORTED",
            "rationale": "The guideline explicitly advises against this test.", "references": [],
        }]})
        self.assertEqual(decisions[0]["decision"], "DENY")
        self.assertEqual(decisions[0]["recommended_approved_cost"], 0)

    def test_insufficient_information_escalates_approvable_line(self):
        decisions = [{"claim_item_id": 7, "item_name": "MRI", "decision": "APPROVE", "recommended_approved_cost": 5000}]
        agent._apply_clinical_review(decisions, {"item_assessments": [{
            "claim_item_id": 7, "item_name": "MRI", "clinical_status": "INSUFFICIENT_INFORMATION",
            "rationale": "The indication is not present.", "missing_information": ["clinical indication"], "references": [],
        }]})
        self.assertEqual(decisions[0]["decision"], "ESCALATE")
        self.assertIn("clinical indication", decisions[0]["reason"])


if __name__ == "__main__":
    unittest.main()
