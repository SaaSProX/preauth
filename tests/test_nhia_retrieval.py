import unittest

from agent import agent
from agent.clinical_guidelines.retrieval import retrieve_guidance_for_pa


class NHIARetrievalTests(unittest.TestCase):
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
                "rationale": "Evidence supports the investigation.",
                "confidence": "HIGH",
                "evidence_section_ids": [valid_id, "invented-section"],
            }]
        })
        references = normalized["item_assessments"][0]["references"]
        self.assertEqual([reference["section_id"] for reference in references], [valid_id])

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
