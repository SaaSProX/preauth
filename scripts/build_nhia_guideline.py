"""Build traceable NHIA Book 3 retrieval artifacts from Vision OCR shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


CHAPTERS = {
    39: (1, "Emergency Disease Conditions"),
    62: (2, "Anaesthesia"),
    65: (3, "Benign Breast Conditions"),
    67: (4, "Cancer Disease"),
    70: (5, "Cardiovascular Disease"),
    91: (6, "Central Nervous System Disorders"),
    101: (7, "Ear, Nose and Throat Diseases"),
    115: (8, "Eye Diseases and Conditions"),
    125: (9, "Gastro-intestinal Disease Conditions"),
    149: (10, "Haematological Disease Conditions"),
    155: (11, "Head Injuries"),
    158: (12, "HIV/AIDS"),
    162: (13, "Immunisable Infections"),
    170: (14, "Malaria"),
    174: (15, "Mental Health Conditions"),
    186: (16, "Metabolic and Endocrine Disease Conditions"),
    189: (17, "Musculoskeletal Conditions"),
    192: (18, "Nutrition and Nutritional Disorders"),
    205: (19, "Obstetrics, Gynecology and Family Planning"),
    236: (20, "Poisoning"),
    250: (21, "Renal and Urological Conditions"),
    256: (22, "Respiratory System Conditions"),
    279: (23, "Sexually Transmitted Infections"),
    287: (24, "Skin Conditions"),
    310: (25, "Surgery-related Conditions"),
    312: (26, "Trauma and Injuries"),
    319: (27, "Tuberculosis and Leprosy"),
}

COLUMNS = (
    ("condition", 0.000, 0.145),
    ("history", 0.145, 0.267),
    ("clinical_findings_and_differentials", 0.267, 0.415),
    ("investigation", 0.415, 0.552),
    ("treatment_and_stabilisation", 0.552, 0.666),
    ("referral_red_flags", 0.666, 0.778),
    ("health_education", 0.778, 1.001),
)

HEADER_TEXT = {
    "CONDITION", "HISTORY", "PHYSICAL FINDINGS, CLINICAL",
    "DIAGNOSIS (DIFFERENTIALS)", "(CHECK VITAL SIGNS)", "INVESTIGATION",
    "TREATMENT AND", "STABILISATION", "REFERRAL (RED", "REFERRAL: (RED",
    "FLAGS)", "HEALTH", "EDUCATION",
}


def read_jsonl_shards(folder: Path, pattern: str) -> dict[int, dict]:
    pages: dict[int, dict] = {}
    for path in sorted(folder.glob(pattern)):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                pages[int(row["page"])] = row
    return pages


def chapter_for(page: int) -> tuple[int | None, str | None]:
    eligible = [start for start in CHAPTERS if start <= page]
    if not eligible:
        return None, None
    return CHAPTERS[max(eligible)]


def clean_lines(lines: list[dict]) -> list[dict]:
    result = []
    for line in sorted(lines, key=lambda item: (-item["y"], item["x"])):
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if not text or text in HEADER_TEXT:
            continue
        result.append({**line, "text": text})
    return result


def condition_labels(lines: list[dict]) -> list[str]:
    candidates = []
    for line in clean_lines(lines):
        text = line["text"]
        letters = [char for char in text if char.isalpha()]
        uppercase_ratio = sum(char.isupper() for char in letters) / len(letters) if letters else 0
        if (
            text.upper() not in {"INTRODUCTION", "CONT'D"}
            and len(text) <= 100
            and uppercase_ratio >= 0.75
        ):
            candidates.append(line)
    groups: list[list[dict]] = []
    for line in candidates:
        if not groups or abs(groups[-1][-1]["y"] - line["y"]) > 0.04:
            groups.append([line])
        else:
            groups[-1].append(line)
    labels = []
    for group in groups:
        label = " ".join(item["text"] for item in group).strip(" :-")
        if len(label) >= 3 and not label.isdigit():
            labels.append(label)
    return labels


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-file", type=Path)
    args = parser.parse_args()

    raw_pages = read_jsonl_shards(args.ocr_dir, "pages_[0-9]*.jsonl")
    table_pages = read_jsonl_shards(args.ocr_dir, "table_[0-9]*.jsonl")
    expected = set(range(1, 360))
    if set(raw_pages) != expected:
        raise SystemExit(f"raw OCR coverage mismatch: missing={sorted(expected - set(raw_pages))}")
    expected_tables = set(range(39, 326))
    if set(table_pages) != expected_tables:
        raise SystemExit(f"table OCR coverage mismatch: missing={sorted(expected_tables - set(table_pages))}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    page_rows: list[dict] = []
    section_rows: list[dict] = []
    condition_map: dict[str, dict] = {}

    for page in range(1, 360):
        raw = raw_pages[page]
        confidences = [float(line.get("confidence") or 0) for line in raw.get("lines", [])]
        chapter_number, chapter_name = chapter_for(page) if 39 <= page <= 325 else (None, None)
        page_rows.append({
            "document_id": "nhia-book-3-2025",
            "pdf_page": page,
            "printed_page": page - 2 if page >= 3 else None,
            "chapter_number": chapter_number,
            "chapter_name": chapter_name,
            "text": raw.get("text", ""),
            "mean_ocr_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0,
        })

        if page not in table_pages:
            continue
        rotated = table_pages[page]
        by_field: dict[str, list[dict]] = {}
        for field, left, right in COLUMNS:
            by_field[field] = clean_lines([
                line for line in rotated.get("lines", [])
                if left <= float(line.get("x") or 0) < right
            ])
        labels = condition_labels(by_field["condition"])
        context = "; ".join(labels) if labels else None
        for label in labels:
            key = re.sub(r"[^A-Z0-9]+", " ", label.upper()).strip()
            entry = condition_map.setdefault(key, {
                "condition": label,
                "chapter_number": chapter_number,
                "chapter_name": chapter_name,
                "pdf_pages": [],
                "printed_pages": [],
            })
            entry["pdf_pages"].append(page)
            entry["printed_pages"].append(page - 2)

        for field, _, _ in COLUMNS:
            lines = by_field[field]
            text = "\n".join(line["text"] for line in lines)
            if not text:
                continue
            confidences = [float(line.get("confidence") or 0) for line in lines]
            section_rows.append({
                "section_id": f"nhia-book-3-p{page:03d}-{field}",
                "document_id": "nhia-book-3-2025",
                "pdf_page": page,
                "printed_page": page - 2,
                "chapter_number": chapter_number,
                "chapter_name": chapter_name,
                "condition_context": context,
                "field": field,
                "text": text,
                "mean_ocr_confidence": round(sum(confidences) / len(confidences), 4),
            })

    write_jsonl(args.output_dir / "pages.jsonl", page_rows)
    write_jsonl(args.output_dir / "clinical_sections.jsonl", section_rows)
    conditions = sorted(condition_map.values(), key=lambda item: (item["pdf_pages"][0], item["condition"]))
    (args.output_dir / "condition_index.json").write_text(
        json.dumps(conditions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    source_sha256 = None
    if args.source_file:
        digest = hashlib.sha256()
        with args.source_file.open("rb") as source_handle:
            for block in iter(lambda: source_handle.read(1024 * 1024), b""):
                digest.update(block)
        source_sha256 = digest.hexdigest()

    manifest = {
        "document_id": "nhia-book-3-2025",
        "title": "NHIA Standard Treatment Guideline and Referral Protocol - Book 3",
        "publisher": "National Health Insurance Authority (Nigeria)",
        "edition_year": 2025,
        "source_file": "NHIA Book-3.pdf",
        "source_url": "https://www.nhia.gov.ng/download/nhia-book-3",
        "source_sha256": source_sha256,
        "pdf_pages": 359,
        "clinical_table_pdf_pages": [39, 325],
        "ocr_engine": "Apple Vision accurate recognition",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": {
            "pages": len(page_rows),
            "clinical_sections": len(section_rows),
            "condition_index_entries": len(conditions),
        },
        "safety": "OCR-derived decision support. Verify cited source page; do not treat as autonomous clinical authority.",
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
