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

    def test_shadow_attachment_does_not_change_insurance_decision(self):
        decisions = [{"claim_item_id": 5, "item_name": "FBC", "decision": "APPROVE"}]
        agent._attach_clinical_review(decisions, {"item_assessments": [{
            "claim_item_id": 5,
            "item_name": "FBC",
            "clinical_status": "UNCLEAR",
            "shadow_only": True,
            "references": [],
        }]})
        self.assertEqual(decisions[0]["decision"], "APPROVE")
        self.assertEqual(decisions[0]["nhia_clinical_review"]["clinical_status"], "UNCLEAR")


if __name__ == "__main__":
    unittest.main()
