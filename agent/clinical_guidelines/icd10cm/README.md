# ICD-10-CM diagnosis descriptions

This directory pins the official CDC FY2026 ICD-10-CM code descriptions used
to resolve diagnosis codes before NHIA Book 3 retrieval. Runtime resolution is
fully local; production PA processing does not call an external ICD service.

- Source: https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2026/icd10cm-Code%20Descriptions-2026.zip
- Upstream file: `icd10cm-codes-2026.txt`
- Upstream SHA-256: `a7e2a77e4627ed55c8afe2b8a7ae22efcbab6b6b162d96cf145213772c1246ba`
- Local file: `icd10cm-codes-2026.txt.gz`

AMAN payloads contain ICD-10-like codes. ICD-10-CM is used here only for exact
code-to-description resolution and retrieval. An unknown code is not guessed:
the clinical review must return insufficient information and escalate the line.
