"""Deterministic, local retrieval for the NHIA Book 3 clinical guideline."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path


GUIDELINE_PATH = Path(__file__).with_name("nhia_book3") / "clinical_sections.jsonl"
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "and", "are", "for", "from", "has", "have", "into", "item", "items",
    "of", "per", "the", "this", "to", "with", "without", "service", "services",
    "general", "initial", "follow", "visit", "patient", "care",
}

SYNONYMS = {
    "xray": {"radiograph", "radiology"},
    "radiograph": {"xray", "radiology"},
    "ultrasound": {"scan", "sonography"},
    "scan": {"ultrasound", "imaging"},
    "caesarean": {"cesarean", "section", "delivery"},
    "cesarean": {"caesarean", "section", "delivery"},
    "csection": {"caesarean", "cesarean", "delivery"},
    "labour": {"labor", "delivery"},
    "labor": {"labour", "delivery"},
    "fbc": {"blood", "count"},
    "cbc": {"blood", "count", "fbc"},
    "urinalysis": {"urine"},
    "malaria": {"plasmodium"},
    "hypertension": {"blood", "pressure"},
}

# ICD-10 chapter prefixes provide a coarse clinical domain when AMAN sends a
# code without its human-readable description. They only guide retrieval; they
# are never treated as clinical evidence themselves.
ICD_CHAPTER_HINTS = {
    "A": {13, 14, 23, 27}, "B": {13, 14, 23, 27},
    "C": {4}, "D": {10, 18}, "E": {16, 18}, "F": {15},
    "G": {6}, "H": {7, 8}, "I": {5}, "J": {22}, "K": {9},
    "L": {24}, "M": {17}, "N": {21}, "O": {19}, "P": {19},
    "S": {1, 26}, "T": {1, 20, 26},
}

FIELD_HINTS = {
    "investigation": {
        "assay", "blood", "culture", "diagnostic", "fbc", "imaging", "laboratory",
        "mri", "radiograph", "scan", "screening", "test", "ultrasound", "urinalysis", "xray",
    },
    "treatment_and_stabilisation": {
        "capsule", "drug", "injection", "medication", "procedure", "surgery", "tablet",
        "therapy", "treatment", "delivery", "caesarean", "cesarean",
    },
}


def _normalize_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(part or "") for part in value)
    raw = (
        str(value or "").lower()
        .replace("c-section", "csection")
        .replace("x-ray", "xray")
        .replace("x ray", "xray")
        .replace("ultra-sound", "ultrasound")
    )
    tokens = [_normalize_token(token) for token in TOKEN_RE.findall(raw)]
    expanded = []
    for token in tokens:
        if token in STOP_WORDS or len(token) < 2:
            continue
        expanded.append(token)
        expanded.extend(SYNONYMS.get(token, ()))
    return expanded


def _item_id(item: dict):
    return item.get("claim_item_id") or item.get("id") or item.get("facility_tariff_item_id")


def _item_name(item: dict) -> str:
    return str(item.get("item_name") or item.get("name") or item.get("description") or _item_id(item) or "Unknown item")


def _pa_items(pa: dict) -> list[dict]:
    items = pa.get("items") or pa.get("requested_items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (json.JSONDecodeError, ValueError):
            items = []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _diagnosis_values(pa: dict) -> list[str]:
    diagnosis = pa.get("diagnosis") or pa.get("diagnoses") or []
    if isinstance(diagnosis, dict):
        diagnosis = list(diagnosis.values())
    if not isinstance(diagnosis, list):
        diagnosis = [diagnosis]
    return [str(value).strip() for value in diagnosis if str(value or "").strip()]


def _icd_chapter_hints(diagnoses: list[str]) -> set[int]:
    chapters: set[int] = set()
    for diagnosis in diagnoses:
        match = re.search(r"\b([A-TV-Z])\d{2}(?:\.\d+)?\b", diagnosis.upper())
        if match:
            chapters.update(ICD_CHAPTER_HINTS.get(match.group(1), set()))
    return chapters


class GuidelineIndex:
    def __init__(self, path: Path):
        self.documents = []
        document_frequency: Counter[str] = Counter()
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                document = json.loads(line)
                weighted_text = " ".join(filter(None, (
                    document.get("condition_context"),
                    document.get("condition_context"),
                    document.get("chapter_name"),
                    document.get("text"),
                )))
                terms = _tokens(weighted_text)
                term_frequency = Counter(terms)
                self.documents.append((document, term_frequency, len(terms)))
                document_frequency.update(term_frequency)
        self.average_length = sum(length for _, _, length in self.documents) / max(len(self.documents), 1)
        self.idf = {
            term: math.log(1 + (len(self.documents) - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(
        self,
        query: str,
        *,
        chapter_hints: set[int] | None = None,
        limit: int = 6,
    ) -> list[dict]:
        query_terms = Counter(_tokens(query))
        if not query_terms:
            return []
        preferred_fields = {
            field for field, hints in FIELD_HINTS.items()
            if set(query_terms) & hints
        }
        scored = []
        k1, b = 1.5, 0.75
        for document, frequency, length in self.documents:
            score = 0.0
            for term, query_count in query_terms.items():
                tf = frequency.get(term, 0)
                if not tf:
                    continue
                denominator = tf + k1 * (1 - b + b * length / max(self.average_length, 1))
                score += self.idf.get(term, 0) * (tf * (k1 + 1) / denominator) * min(query_count, 2)
            if chapter_hints and document.get("chapter_number") in chapter_hints:
                score += 4.0
            if preferred_fields and document.get("field") in preferred_fields:
                score += 12.0
            condition_terms = set(_tokens(document.get("condition_context")))
            score += 1.5 * len(set(query_terms) & condition_terms)
            if score > 0:
                scored.append((score, document))
        scored.sort(key=lambda row: (-row[0], row[1]["pdf_page"], row[1]["section_id"]))

        selected = []
        page_counts: defaultdict[int, int] = defaultdict(int)
        for score, document in scored:
            page = int(document["pdf_page"])
            if page_counts[page] >= 2:
                continue
            selected.append({
                "section_id": document["section_id"],
                "document": "NHIA Book 3",
                "chapter_number": document.get("chapter_number"),
                "chapter": document.get("chapter_name"),
                "condition": document.get("condition_context"),
                "field": document.get("field"),
                "pdf_page": page,
                "printed_page": document.get("printed_page"),
                "text": str(document.get("text") or "")[:1200],
                "retrieval_score": round(score, 4),
                "ocr_confidence": document.get("mean_ocr_confidence"),
            })
            page_counts[page] += 1
            if len(selected) >= limit:
                break
        return selected


@lru_cache(maxsize=1)
def get_guideline_index() -> GuidelineIndex:
    return GuidelineIndex(GUIDELINE_PATH)


def retrieve_guidance_for_pa(pa: dict, *, per_item_limit: int = 6) -> dict:
    diagnoses = _diagnosis_values(pa)
    chapter_hints = _icd_chapter_hints(diagnoses)
    clinical_context = " ".join(str(pa.get(key) or "") for key in (
        "presenting_complaint", "clinical_notes", "symptoms", "care_category", "checkin_type",
    ))
    items = _pa_items(pa)
    index = get_guideline_index()
    item_evidence = []
    for item in items:
        name = _item_name(item)
        query = " ".join((*diagnoses, clinical_context, name))
        item_evidence.append({
            "claim_item_id": _item_id(item),
            "item_name": name,
            "evidence": index.search(query, chapter_hints=chapter_hints, limit=per_item_limit),
        })
    return {
        "document_id": "nhia-book-3-2025",
        "diagnoses": diagnoses,
        "icd_chapter_hints": sorted(chapter_hints),
        "item_evidence": item_evidence,
    }
