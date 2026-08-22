# NHIA Book 3 clinical guideline artifacts

This directory contains OCR-derived, page-traceable retrieval data from the
2025 NHIA Standard Treatment Guideline and Referral Protocol (Book 3).

- `pages.jsonl`: complete page-level OCR for all 359 PDF pages.
- `clinical_sections.jsonl`: clinical table content separated into condition,
  history, findings/differentials, investigation, treatment/stabilisation,
  referral red flags, and health education fields.
- `condition_index.json`: normalized condition labels with source pages.
- `manifest.json`: provenance, coverage, extraction method, and counts.

These files are decision-support inputs, not executable clinical rules. A PA
recommendation must cite its source page, distinguish clinical appropriateness
from benefit coverage, and escalate when clinical context or OCR evidence is
ambiguous. The original PDF remains the authoritative source.

Regenerate after OCR shards are produced:

```bash
python scripts/build_nhia_guideline.py \
  --ocr-dir tmp/nhia_ocr \
  --output-dir agent/clinical_guidelines/nhia_book3 \
  --source-file "/path/to/NHIA Book-3.pdf"
```
