"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO PLAN BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}

PLAN TIERS (ascending): Bronze → Silver → Gold → Platinum → Platinum Plus

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Maximum Annual Benefit (master cap — overrides all other limits):
  Bronze: 1,000,000 | Silver: 1,700,000 | Gold: 2,500,000 | Platinum: 3,500,000 | Platinum Plus: 5,000,000

Inpatient Limit:
  Bronze: 600,000 | Silver: 1,000,000 | Gold: 1,500,000 | Platinum: 2,100,000 | Platinum Plus: 3,000,000

Outpatient Limit:
  Bronze: 400,000 | Silver: 700,000 | Gold: 1,000,000 | Platinum: 1,400,000 | Platinum Plus: 2,000,000

Surgical Care Limit (covers all surgery types — minor, intermediate, major):
  Bronze: 200,000 | Silver: 350,000 | Gold: 600,000 | Platinum: 1,000,000 | Platinum Plus: 1,500,000

Dental Care Limit:
  Bronze: 15,000 | Silver: 30,000 | Gold: 70,000 | Platinum: 100,000 | Platinum Plus: 200,000

Optical — Lenses/Frames/Contact Lenses (once every 2 years only):
  Bronze: 5,000 (lenses only) | Silver: 10,000 | Gold: 15,000 | Platinum: 30,000 | Platinum Plus: 50,000

Optical — Eye Testing + Acute/Chronic Eye Disease Treatment (surgery inclusive):
  Bronze: 25,000 | Silver: 50,000 | Gold: 75,000 | Platinum: 100,000 | Platinum Plus: 300,000

Optical — Total Optical Limit:
  Bronze: 30,000 | Silver: 60,000 | Gold: 90,000 | Platinum: 130,000 | Platinum Plus: 350,000

Cancer Care (Consultation, Investigation, Counselling, Chemotherapy, Radiotherapy, Surgery):
  Bronze: 100,000 | Silver: 150,000 | Gold: 250,000 | Platinum: 400,000 | Platinum Plus: 700,000

Chronic Disease Medication:
  Bronze: 80,000 | Silver: 150,000 | Gold: 250,000 | Platinum: 350,000 | Platinum Plus: 500,000

HIV/AIDS Care Treatment:
  Bronze: 100,000 | Silver: 150,000 | Gold: 350,000 | Platinum: 500,000 | Platinum Plus: 500,000

Kidney Dialysis:
  Bronze: NOT COVERED | Silver: 70,000 | Gold: 90,000 | Platinum: 120,000 | Platinum Plus: 500,000

Neonatal Care — Incubator/SCBU (global limit, drawn from nursing mother's limit):
  Bronze: 50,000 | Silver: 100,000 | Gold: 250,000 | Platinum: 500,000 | Platinum Plus: 700,000

Mortuary Services (Cleaning, Embalmment, Storage, Autopsy):
  Bronze: NOT COVERED | Silver: 50,000 | Gold: 100,000 | Platinum: 150,000 | Platinum Plus: 150,000

Critical Illness + Death Cover (cancer, kidney failure, heart attack, stroke, or death):
  Bronze: NOT COVERED | Silver: 100,000 | Gold: 200,000 | Platinum: 400,000 | Platinum Plus: 400,000

Fertility Investigation (family plan subscribers only):
  Bronze: NOT COVERED
  Silver: 35,000 (Consultations, Counseling, USS, SFA)
  Gold: 50,000 (Consultations, Counseling, USS, SFA)
  Platinum: 100,000 (Consultations, Counseling, USS, SFA, HSG, Hormone Profile)
  Platinum Plus: 200,000 (Consultations, Counseling, USS, SFA, HSG, Hormone Profile)

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Care sessions per year:
  Bronze: 2 | Silver: 4 | Gold: 8 | Platinum: 12 | Platinum Plus: 20

Physiotherapy sessions per year:
  Bronze: 2 | Silver: 6 | Gold: 10 | Platinum: 15 | Platinum Plus: 20

CT Scan / MRI Scan:
  Bronze: CT only, emergency cases only, once per annum
  Silver: CT or MRI, emergency cases only, once per annum
  Gold: CT or MRI, up to 3 times per annum
  Platinum: Up to outpatient limit
  Platinum Plus: Up to outpatient limit

Echocardiogram:
  Bronze: NOT COVERED | Silver, Gold, Platinum, Platinum Plus: COVERED

Molecular Diagnostics (including Covid-19 testing, designated centers only):
  Bronze: NOT COVERED | Silver: once per annum | Gold: up to 2/year | Platinum: up to 2/year | Platinum Plus: up to 2/year

Endoscopic Procedures (Colonoscopy, Sigmoidoscopy, Bronchoscopy, Laryngoscopy, Hysteroscopy, Laparoscopy, etc.):
  Bronze: NOT COVERED | Silver, Gold, Platinum, Platinum Plus: COVERED

ICU / High Dependency Unit (HDU):
  Bronze: 24 hours | Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days

Mother Accommodation for Dependent Admission (SCBU/NICU only, excluding feeding):
  Bronze: 24 hours | Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days

Phototherapy:
  Bronze: 24 hours | Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days

Wellness — Gym (Principal only):
  Bronze: NOT COVERED | Silver: 2x/month | Gold: 4x/month | Platinum: 8x/month | Platinum Plus: Unlimited

Wellness — Spa (Principal only):
  Bronze: NOT COVERED | Silver: NOT COVERED | Gold: 2 sessions/year | Platinum: 3 sessions/year | Platinum Plus: 4 sessions/year

──────────────────────────────────────────
SURGERY CLASSIFICATION (all draw from Surgical Care Limit)
──────────────────────────────────────────
MINOR SURGERIES (covered all plans):
Wound suturing, incision and drainage of abscess, removal of foreign bodies, circumcision,
excision of lumps, punch biopsy, skin biopsy, ear syringing, episiotomy repair,
Bartholin cyst incision and drainage, closed reduction of minor dislocations, POP application

INTERMEDIATE SURGERIES (covered all plans):
Appendectomy, hernia repair (inguinal/umbilical), hydrocelectomy, hemorrhoidectomy,
fistulectomy/fistulotomy, excision of large lipoma, incisional biopsy, varicose vein surgery (simple),
pilonidal sinus excision, tonsillectomy, adenoidectomy, septoplasty, turbinectomy, nasal polypectomy,
TURP, orchidopexy, varicocelectomy, cystoscopy, myomectomy (simple), D&C, MVA,
tubal ligation, repair of 3rd/4th degree perineal tear, ORIF (simple fractures),
arthroscopy (diagnostic/simple), tendon repair, removal of deep implants,
wide local excision of skin lesions, keloid excision with flap closure, skin grafting

MAJOR SURGERIES (covered all plans):
Exploratory laparotomy, bowel resection and anastomosis, gastrectomy, colectomy,
splenectomy, pancreatic surgery (Whipple), thyroidectomy, mastectomy, major trauma surgery,
hepatectomy, craniotomy, brain tumor excision, spinal cord decompression, aneurysm clipping,
VP shunt insertion, total hip replacement, total knee replacement, spinal fusion surgery,
major pelvic fracture fixation, limb amputation (major), radical prostatectomy,
nephrectomy (partial or total), cystectomy, major reconstructive urologic surgery,
radical hysterectomy, complex myomectomy, obstetric hysterectomy,
surgery for ruptured ectopic pregnancy, pelvic reconstructive surgery

──────────────────────────────────────────
MATERNITY & NEONATAL SERVICES (family plan subscribers only)
──────────────────────────────────────────
Antenatal Care: Covered | Normal Delivery: Covered | Induction of Labour: Covered
Caesarean Section: Covered (up to surgical limit) | Postnatal Care (6 weeks): Covered
Neonatal basic services (male circumcision, ear piercing): Covered
Treatment of mild/moderate neonatal sepsis: Covered

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. PLATINUM PLUS EXPRESS CARD: No pre-authorization required. Auto-approve all requests.
2. FIRST YEAR SURGICAL EXCLUSION: Non-accidental surgical claims within the first year of cover are excluded.
3. CHRONIC DISEASE WAITING PERIOD: Hypertension, Diabetes, Hyperlipidemia, and similar chronic diseases have a 6-month waiting period from enrollment date.
4. PREGNANCY WAITING PERIOD: Pregnancy has a 9-month waiting period. Delivery is NOT covered in the first year of enrollment.
5. MATERNITY/NEONATAL FAMILY PLAN RULE: Maternity and neonatal benefits are exclusive to family plan subscribers. If family plan/subscriber type is unknown, ESCALATE rather than deny or approve solely on plan tier.
6. AGE LIMIT: Principal must be 65 or under. Above 65 → must be on Senior Citizens Plan, not standard plans.
7. OPTICAL LENSES: Once every 2 years only.
8. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
9. GYM/SPA: Principal only.
10. NEWBORN REGISTRATION: Newborns not registered within 6 weeks of birth are excluded.
11. ROOM TYPE: Bronze=General Ward, Silver=Semi-Private, Gold/Platinum/Platinum Plus=Private. Upgrades not covered.
12. NEONATAL BENEFIT: Drawn from nursing mother's limit (live birth only).

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (ALL PLANS)
──────────────────────────────────────────
1. Transplant surgery (all types)
2. Speech disorders
3. Thyroid disorders — neurological and neurosurgical
4. Plastic and cosmetic surgeries (all types)
5. Infertility treatment: IVF, GIFT, artificial insemination, hydrotubation, hysterosalpingogram as treatment
6. Virility enhancing drugs
7. Herbal drugs, non-prescription drugs, food supplements, experimental drugs and treatments
8. Joint replacements and prosthetic limbs
9. Long-term psychiatric illness (duration longer than 6 months)
10. Self-inflicted injuries
11. Treatment of obesity
12. All Covid-19 treatment and Hepatitis treatment (except molecular diagnostics at designated centers)
13. Severe burns covering more than 10% of body surface area
14. Learning difficulties, behavioral and developmental problems
15. Pre-school health examinations
16. Newborns not registered within 6 weeks of birth
17. Neonatal care not in the covered neonatal services schedule
18. Room upgrades beyond plan specification
19. Home care and domiciliary services
20. Consultations with unrecognized practitioners (unrecognized consultants, hospitals, family doctors, therapists, complementary medicine)
21. Comprehensive health screening outside the health check scope
22. Advanced/complex investigations not in the schedule
23. Dental care not in the schedule
24. Laboratory investigations not in the schedule
25. Any service, treatment, procedure, or investigation not listed in the covered schedule
"""


PLAN_LIMITS = {
    "Bronze": {
        "annual_cap": 1_000_000,
        "inpatient": 600_000,
        "outpatient": 400_000,
        "surgical": 200_000,
        "dental": 15_000,
        "optical_total": 30_000,
        "cancer": 100_000,
        "chronic": 80_000,
        "hiv": 100_000,
        "dialysis": None,
        "neonatal": 50_000,
    },
    "Silver": {
        "annual_cap": 1_700_000,
        "inpatient": 1_000_000,
        "outpatient": 700_000,
        "surgical": 350_000,
        "dental": 30_000,
        "optical_total": 60_000,
        "cancer": 150_000,
        "chronic": 150_000,
        "hiv": 150_000,
        "dialysis": 70_000,
        "neonatal": 100_000,
    },
    "Gold": {
        "annual_cap": 2_500_000,
        "inpatient": 1_500_000,
        "outpatient": 1_000_000,
        "surgical": 600_000,
        "dental": 70_000,
        "optical_total": 90_000,
        "cancer": 250_000,
        "chronic": 250_000,
        "hiv": 350_000,
        "dialysis": 90_000,
        "neonatal": 250_000,
    },
    "Platinum": {
        "annual_cap": 3_500_000,
        "inpatient": 2_100_000,
        "outpatient": 1_400_000,
        "surgical": 1_000_000,
        "dental": 100_000,
        "optical_total": 130_000,
        "cancer": 400_000,
        "chronic": 350_000,
        "hiv": 500_000,
        "dialysis": 120_000,
        "neonatal": 500_000,
    },
    "Platinum Plus": {
        "annual_cap": 5_000_000,
        "inpatient": 3_000_000,
        "outpatient": 2_000_000,
        "surgical": 1_500_000,
        "dental": 200_000,
        "optical_total": 350_000,
        "cancer": 700_000,
        "chronic": 500_000,
        "hiv": 500_000,
        "dialysis": 500_000,
        "neonatal": 700_000,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
