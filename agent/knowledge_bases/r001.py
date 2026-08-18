"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO CORPORATE PLAN R001 BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}
Source workbook: R001.xlsx, sheet "Appendix A - Benefits Table"

PLAN TIERS (ascending): Bronze → Silver → Gold → Platinum → Platinum Plus

──────────────────────────────────────────
CONTRIBUTIONS / PREMIUMS (NGN) — CONTEXT ONLY, NOT A COVERAGE LIMIT
──────────────────────────────────────────
Individual Premium:
  Bronze: 85,805.50 | Silver: 124,795 | Gold: 229,194.90 | Platinum: 392,339.20 | Platinum Plus: 844,550.30

Family of 6:
  Bronze: 386,124.75 | Silver: 561,577.50 | Gold: 1,031,377.05 | Platinum: 1,765,526.40 | Platinum Plus: 3,800,476.35

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Maximum Annual Benefit (master cap — overrides all other limits):
  Bronze: 1,000,000 | Silver: 1,700,000 | Gold: 2,500,000 | Platinum: 3,500,000 | Platinum Plus: 5,000,000

Inpatient Limit:
  Bronze: 600,000 | Silver: 1,000,000 | Gold: 1,500,000 | Platinum: 2,100,000 | Platinum Plus: 3,000,000

Outpatient Limit:
  Bronze: 400,000 | Silver: 700,000 | Gold: 1,000,000 | Platinum: 1,400,000 | Platinum Plus: 2,000,000

Surgical Care Limit (covers all surgery types — minor, intermediate, major, unless excluded):
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

Critical Illness + Death Cover (cancer, kidney failure, heart attack, stroke, or death):
  Bronze: NOT COVERED | Silver: 100,000 | Gold: 200,000 | Platinum: 400,000 | Platinum Plus: 400,000

Mortuary Services (Cleaning, Embalmment, Storage, Autopsy):
  Bronze: NOT COVERED | Silver: 50,000 | Gold: 100,000 | Platinum: 150,000 | Platinum Plus: 150,000

Fertility Investigation:
  Bronze: NOT COVERED
  Silver: 35,000 (Fertility Consultations, Counseling, USS, SFA)
  Gold: 50,000 (Fertility Consultations, Counseling, USS, SFA)
  Platinum: 100,000 (Fertility Consultations, Counseling, USS, SFA, HSG, Hormone Profile)
  Platinum Plus: 200,000 (Fertility Consultations, Counseling, USS, SFA, HSG, Hormone Profile)

Delivery Abroad Reimbursement:
  NOT LISTED in the R001 source workbook. Treat as not covered unless AMAN payload has a specific configured benefit/approval context.

Treatment of Congenital Abnormalities:
  NOT LISTED in the R001 source workbook. Treat as not covered unless AMAN payload has a specific configured benefit/approval context.

──────────────────────────────────────────
COVERED INPATIENT / OUTPATIENT SERVICES
──────────────────────────────────────────
Platinum Express Card: Platinum Plus only; no pre-authorization required.
Telemedicine: Unlimited on all plans.
Free door-step medication delivery and pharmacy pick-up: Covered on all plans where available.

Inpatient:
- Accidents and emergencies: resuscitative or lifesaving initial treatment, investigations, and interventions are covered on all plans.
- Admission ward care, medications and consumables, blood transfusion, feeding where available: Covered on all plans.
- Accommodation: Bronze=General Ward, Silver=Semi-Private Ward, Gold/Platinum/Platinum Plus=Private Ward.
- Inpatient medication and medical/surgical consumables: Covered on all plans.
- Mother accommodation for dependent admission, SCBU/NICU only, excluding feeding:
  Bronze: 24 hours | Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days
- ICU/HDU:
  Bronze: 24 hours | Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days

Consultations:
- General consultations, initial and follow-up: Covered on all plans.
- Specialist consultations, initial and follow-up: Covered on all plans, including cardiology, endocrinology, nephrology, gastroenterology, pulmonology, infectious disease, rheumatology, dermatology, neurology, family medicine, psychiatry, paediatrics, obstetrics/gynaecology, surgery, orthopaedics, neurosurgery, cardiothoracic surgery, urology, paediatric surgery, ENT, ophthalmology, anaesthesia, radiology, radiation oncology, pathology, haematology, chemical pathology, microbiology, immunology, clinical pharmacology, emergency medicine, palliative medicine, genetics, oral/maxillofacial surgery, dentistry, and similar listed specialties.

Tests and investigations:
- X-rays and basic diagnostic tests: Covered on all plans.
- WHO essential in-vitro diagnostic laboratory tests: Covered on all plans.
- Haematology investigations: Covered on all plans. Includes FBC/CBC, PCV, Hb, WBC/RBC/platelets, differential WBC, ESR, PBF, ABO/Rh typing, crossmatching, PT, aPTT, BT, CT, sickling test, genotype/Hb electrophoresis, reticulocyte count, malaria parasite test.
- Chemistry investigations: Covered on all plans. Includes glucose, urea, creatinine, electrolytes, calcium, phosphate, proteins, bilirubin, ALP, ALT/SGPT, AST/SGOT, GGT, cholesterol, triglycerides, HDL, LDL, etc.
- Microbiology investigations: Covered on all plans. Includes blood/urine/sputum/wound/throat/stool/CSF cultures, Gram stain, AFB/TB, AST sensitivity, HVS M/C/S, HBsAg, HCV antibody, HIV, syphilis, malaria, stool ova/parasite, H. pylori, fungal culture, KOH mount, etc.
- Advanced investigations: Covered on all plans only when stated in the schedule. Includes Alpha-1 Antitrypsin, HBA1C, 24-hour creatinine clearance, bleeding time, blood urea nitrogen, chlamydia screening, clotting time, Coombs direct/indirect, creatinine phosphokinase, CSF M/C/S, D-Dimer, G-6PD, hepatitis B/C screening, HBSAg, HIV confirmatory/screening, immunofluorescence assay, osmotic fragility, Pap smear/cytology, PSA, protein electrophoresis, semen M/C/S, SFA, serum immunoglobulins/antibody, etc.
- CT/MRI:
  Bronze: CT only, emergency cases only, once per annum
  Silver: CT/MRI scan only, emergency cases only, once per annum
  Gold: CT/MRI scan only, up to 3 times per annum
  Platinum: Up to outpatient limit
  Platinum Plus: Up to outpatient limit
- ECG: Covered on all plans.
- Echocardiogram: Bronze NOT COVERED; Silver/Gold/Platinum/Platinum Plus covered.
- Molecular diagnostics including Covid-19 testing: Designated center only. Bronze NOT COVERED; Silver once per annum; Gold/Platinum/Platinum Plus up to 2 tests per annum.

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Care:
  Bronze: 2 sessions/year | Silver: 4 sessions/year | Gold: 8 sessions/year | Platinum: 12 sessions/year | Platinum Plus: 20 sessions/year

Physiotherapy:
  Bronze: 2 sessions | Silver: 6 sessions | Gold: 10 sessions | Platinum: 15 sessions | Platinum Plus: 20 sessions

Endoscopic Procedures (therapeutic and diagnostic):
  Bronze: NOT COVERED | Silver/Gold/Platinum/Platinum Plus: COVERED
  Includes colonoscopy, flexible sigmoidoscopy, proctoscopy, anoscopy, capsule endoscopy, enteroscopy, EUS, bronchoscopy, laryngoscopy, nasopharyngoscopy, diagnostic cystoscopy, ureteroscopy, diagnostic nephroscopy, diagnostic hysteroscopy, laparoscopy.

Wellness — Gym (Principal only):
  Bronze: NOT COVERED | Silver: 2x/month | Gold: 4x/month | Platinum: 8x/month | Platinum Plus: Unlimited

Wellness — Spa (Principal only):
  Bronze: NOT COVERED | Silver: NOT COVERED | Gold: 2 sessions/year | Platinum: 3 sessions/year | Platinum Plus: 4 sessions/year

──────────────────────────────────────────
SURGERY CLASSIFICATION (all draw from Surgical Care Limit unless excluded)
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

MAJOR SURGERIES (covered all plans unless excluded):
Exploratory laparotomy, bowel resection and anastomosis, gastrectomy, colectomy,
splenectomy, pancreatic surgery (Whipple), thyroidectomy, mastectomy, major trauma surgery,
hepatectomy, craniotomy, brain tumor excision, spinal cord decompression, aneurysm clipping,
VP shunt insertion, total hip replacement, total knee replacement, spinal fusion surgery,
major pelvic fracture fixation, limb amputation (major), radical prostatectomy,
nephrectomy (partial or total), cystectomy, major reconstructive urologic surgery,
radical hysterectomy, complex myomectomy, obstetric hysterectomy,
surgery for ruptured ectopic pregnancy, pelvic reconstructive surgery

NOTE: Exclusions override the surgery classification. Joint replacements and prosthetic limbs are listed as exclusions in this R001 source.

──────────────────────────────────────────
MATERNITY, NEONATAL & IMMUNIZATIONS
──────────────────────────────────────────
Eligibility rule: Maternity and neonatal services are exclusive to family plan subscribers. If subscriber/family-plan status is unknown, ESCALATE rather than approve or deny solely on plan tier.

Maternity:
- Antenatal care: Covered on all plans.
- Normal delivery: Covered on all plans.
- Induction of labour: Covered on all plans.
- Caesarean section: Covered on all plans, up to surgical limit.
- Postnatal care: Covered for 6 weeks on all plans.
- Pregnancy has a 9-month waiting period and delivery is not covered in the first year of enrollment.

Neonatal:
- Male circumcision and ear piercing: Covered on all plans.
- Phototherapy: Bronze 24 hours | Silver 48 hours | Gold 72 hours | Platinum 5 days | Platinum Plus 7 days.
- Mild/moderate neonatal sepsis treatment: Covered on all plans.
- Neonatal incubator/SCBU benefit can only be drawn from the nursing mother's limit for a live birth.

Immunizations:
- NPI immunizations for ages 0-5: BCG, Measles, Pentavalent, Oral polio, IPV, Vitamin A supplementation, Pentavalent vaccine covered on all plans.
- Additional immunizations ages 0-5:
  Bronze: NOT COVERED
  Silver: Hepatitis B, HiB, Yellow Fever
  Gold/Platinum/Platinum Plus: Hepatitis A, Hepatitis B, Hib, Chicken Pox, MMR, Pneumococcal, Rotavirus, Meningitis, Yellow Fever, Typhoid Fever
- Additional immunizations ages 6+:
  Bronze: NOT COVERED | Silver: Hepatitis B, Yellow Fever | Gold: Hepatitis B, Yellow Fever | Platinum/Platinum Plus: Meningitis, Yellow Fever, Hepatitis B

──────────────────────────────────────────
EMERGENCY, FAMILY PLANNING, WELLNESS & OTHER BENEFITS
──────────────────────────────────────────
Emergency Response Service:
- Phone aid/telemedicine first-aid: Covered on all plans.
- Onsite deployment of first responder with advanced trauma kit: Covered on all plans.
- Hospital-to-hospital transport: Covered on all plans.
- Home-to-hospital and roadside-to-hospital transport: Covered on all plans.

Family Planning Services:
  Bronze: Oral and injectables
  Silver: IUCD (Copper T), injectables
  Gold: IUCD (Copper T), injectables, pills
  Platinum: IUCD (Copper T), injectables, pills, Norplant
  Platinum Plus: IUCD (Copper T), injectables, pills, Norplant

Health Checks:
  Bronze: Limited basic — physical, BP, urinalysis, blood sugar, PCV, serum cholesterol.
  Silver: Limited basic — physical, BP, urinalysis, blood sugar, PCV, PSA for men above 40 years, serum cholesterol.
  Gold: Limited basic — physical, BP, urinalysis, blood sugar, PCV, liver function test, electrolytes/urea/creatinine, Pap smear, PSA, mammography.
  Platinum: Limited basic — physical, BP, urinalysis, blood sugar, PCV, serum cholesterol, liver function test, electrolytes/urea/creatinine, Pap smear, ECG, PSA, mammography.
  Platinum Plus: Physical examination, BMI, urinalysis, PCV, blood pressure, blood sugar, chest X-ray, ECG, serum cholesterol, liver function test, electrolytes/urea/creatinine, annual mammogram for women >40, breast scan every 2 years for women >30, cervical smears every 2 years for women >30, PSA for men above 40.
  Health checks can only be done at designated hospitals/diagnostic centers during institutions' health week and are non-refundable otherwise.

Onsite/online promotional health talks, webinars, health education series: Covered on all plans.
Aman Care App: Covered on all plans.

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. PLATINUM EXPRESS CARD: Platinum Plus has no pre-authorization required.
2. FIRST YEAR SURGICAL EXCLUSION: Non-accidental surgical claims incurred within the first year of cover are excluded.
3. CHRONIC DISEASE WAITING PERIOD: Chronic diseases such as hypertension, diabetes, hyperlipidemia, etc. have a 6-month waiting period.
4. PREGNANCY WAITING PERIOD: Pregnancy has a 9-month waiting period and delivery is not covered in the first year of enrollment.
5. MATERNITY/NEONATAL FAMILY PLAN RULE: Maternity and neonatal services are exclusive to family plan subscribers. If family plan/subscriber type is unknown, ESCALATE.
6. AGE LIMIT: Principal must be 65 or under. Enrollees/dependents above 65 must be enrolled on the Senior Citizens Plan.
7. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
8. GYM/SPA: Principal only. Other terms and conditions apply.
9. ROOM TYPE: Bronze=General Ward, Silver=Semi-Private Ward, Gold/Platinum/Platinum Plus=Private Ward. Executive/VIP rooms not covered.
10. NEONATAL BENEFIT: Drawn from nursing mother's limit for a live birth only.
11. FAMILY PREMIUM: Family premium quoted is for family of six (principal, spouse, and 4 children under 24).
12. PREMIUM PAYMENT: Premium computed is payable once annually. Flexible payment may be arranged by negotiation.
13. INSURANCE/LIMITS: Insurance and limits of services are not transferable.

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (ALL R001 PLANS)
──────────────────────────────────────────
1. Non-accidental surgical claims incurred within the first year of cover
2. Chronic diseases such as hypertension, diabetes, hyperlipidemia, etc. before the 6-month waiting period is cleared
3. Pregnancy before the 9-month waiting period is cleared; delivery is not covered in the first year of enrollment
4. Transplant surgery
5. Speech disorder / speech disorders
6. Thyroid disorders, neurological and neurosurgical disorders
7. Plastic/cosmetic surgeries
8. Advanced and complex investigations not stated in the schedule of covered services
9. Other investigations and treatment problems relating to infertility, including hydrotubation, hysterosalpingogram, IVF, GIFT, artificial insemination
10. Virility enhancing drugs
11. Herbal drugs, non-prescription drugs, food supplements, experimental drugs and experimental treatment
12. Other laboratory investigations not listed in the schedule of covered services
13. Dental care not listed in the schedule of covered services
14. Home care and domiciliary services
15. Joint replacements and prosthetic limbs
16. Long-term psychiatric illness longer than 6 months
17. Comprehensive health screening/well-persons check outside the scope of the covered health checks
18. Pre-school health examinations
19. Treatment for newborn not registered on the plan after 6 weeks of birth
20. Neonatal care not listed under neonatal services
21. Self-inflicted injuries
22. Treatment of obesity
23. All Covid-19 and Hepatitis treatment
24. Covid-19 testing except as stated in the schedule of covered services
25. Room upgrades beyond the specified plan benefit
26. Management of severe burns covering more than 10% body surface area
27. Learning difficulties, behavioral and developmental problems
28. Consultations with unrecognized consultants, hospitals, family doctors, therapists, dental practitioners, or complementary medicines practitioners
29. Any other treatment, service, procedure, or investigation not listed in the schedule of covered medical services
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
        "critical_illness_death": None,
        "mortuary": None,
        "fertility": None,
        "congenital": None,
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
        "critical_illness_death": 100_000,
        "mortuary": 50_000,
        "fertility": 35_000,
        "congenital": None,
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
        "critical_illness_death": 200_000,
        "mortuary": 100_000,
        "fertility": 50_000,
        "congenital": None,
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
        "critical_illness_death": 400_000,
        "mortuary": 150_000,
        "fertility": 100_000,
        "congenital": None,
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
        "critical_illness_death": 400_000,
        "mortuary": 150_000,
        "fertility": 200_000,
        "congenital": None,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
