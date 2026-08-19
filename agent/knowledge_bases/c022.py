"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO CORPORATE PLAN C022 BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}
Source workbook: C022.xlsx, sheet "Appendix A - Benefits Table"

PLAN TIERS (ascending): Customized Bronze → Customized Silver
Normalize Customized Bronze to Bronze and Customized Silver to Silver for limit lookup.

──────────────────────────────────────────
CONTRIBUTIONS / PREMIUMS (NGN) — CONTEXT ONLY, NOT A COVERAGE LIMIT
──────────────────────────────────────────
Individual Premium:
  Customized Bronze: 58,005 | Customized Silver: 90,450

Family of 2:
  Customized Bronze: 110,209.50 | Customized Silver: 171,855
Family of 3:
  Customized Bronze: 156,613.50 | Customized Silver: 244,215
Family of 4:
  Customized Bronze: 197,217 | Customized Silver: 307,530
Family of 5:
  Customized Bronze: 232,020 | Customized Silver: 339,187.50
Family of 6:
  Customized Bronze: 261,022.50 | Customized Silver: 407,025
Each additional child dependent below 24:
  Customized Bronze: 43,503.75 | Customized Silver: 67,837.50
Each additional adult dependent above 24:
  Customized Bronze: 58,005 | Customized Silver: 90,450

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Maximum Annual Benefit (master cap — overrides all other limits):
  Bronze: 1,000,000 | Silver: 1,900,000

Inpatient Limit:
  Bronze: 600,000 | Silver: 1,200,000

Outpatient Limit:
  Bronze: 400,000 | Silver: 700,000

Surgical Care Limit (covers all surgery types — minor, intermediate, major, unless excluded):
  Bronze: 250,000 | Silver: 400,000

Dental Care Limit:
  Bronze: 25,000 | Silver: 50,000

Optical — Lenses/Frames/Contact Lenses (once every 2 years only):
  Bronze: 10,000 (lenses only) | Silver: 25,000

Optical — Eye Testing + Acute/Chronic Eye Disease Treatment (surgery inclusive):
  Bronze: 20,000 | Silver: 40,000

Optical — Total Optical Limit:
  Bronze: 30,000 | Silver: 65,000

Cancer Care (Consultation, Investigation, Counselling, Chemotherapy, Radiotherapy, Surgery):
  Bronze: 100,000 | Silver: 250,000

Chronic Disease Medication:
  Bronze: 150,000 | Silver: 250,000

HIV/AIDS Care Treatment:
  Bronze: 100,000 | Silver: 150,000

Kidney Dialysis:
  Bronze: NOT COVERED | Silver: 70,000

Neonatal Care — Incubator/SCBU (global limit, drawn from nursing mother's limit):
  Bronze: 50,000 | Silver: 100,000

Treatment of Congenital Abnormalities (for children born on the plan):
  Bronze: NOT COVERED | Silver: 50,000

Mortuary Services (Cleaning, Embalmment, Storage, Autopsy):
  Bronze: NOT COVERED | Silver: 50,000

Critical Illness + Death Cover (cancer, kidney failure, heart attack, stroke, or death):
  Bronze: NOT COVERED | Silver: 100,000

Fertility Investigation:
  Bronze: NOT COVERED
  Silver: 35,000 (Fertility Consultations, Counseling, USS, SFA)

Delivery Abroad Reimbursement:
  Bronze: NOT COVERED | Silver: NOT COVERED

──────────────────────────────────────────
COVERED INPATIENT / OUTPATIENT SERVICES
──────────────────────────────────────────
Platinum Express Card: NOT APPLICABLE. No Platinum or Platinum Plus tier exists under C022.
Telemedicine: Unlimited on both plans.
Free door-step medication delivery and pharmacy pick-up: Covered on both plans where available.

Inpatient:
- Accidents and emergencies: resuscitative or lifesaving initial treatment, investigations, and interventions are covered on both plans.
- Admission ward care, medications and consumables, blood transfusion, feeding where available: Covered on both plans.
- Accommodation: Bronze=General Ward, Silver=Semi-Private Ward. Executive/VIP rooms are not covered.
- Inpatient medication and medical/surgical consumables: Covered on both plans.
- Mother accommodation for dependent admission, SCBU/NICU only, excluding feeding:
  Bronze: 24 hours | Silver: 48 hours
- ICU/HDU:
  Bronze: 24 hours | Silver: 48 hours

Consultations:
- General consultations, initial and follow-up: Covered on both plans.
- Specialist consultations, initial and follow-up: Covered on both plans, including cardiology, endocrinology, nephrology, gastroenterology, pulmonology, infectious disease, rheumatology, dermatology, neurology, family medicine, psychiatry, paediatrics, obstetrics/gynaecology, surgery, orthopaedics, neurosurgery, cardiothoracic surgery, urology, paediatric surgery, ENT, ophthalmology, anaesthesia, radiology, radiation oncology, pathology, haematology, chemical pathology, microbiology, immunology, clinical pharmacology, emergency medicine, palliative medicine, genetics, oral/maxillofacial surgery, dentistry, and similar listed specialties.

Tests and investigations:
- X-rays and basic diagnostic tests: Covered on both plans.
- WHO essential in-vitro diagnostic laboratory tests: Covered on both plans.
- Haematology investigations: Covered on both plans. Includes FBC/CBC, PCV, Hb, WBC/RBC/platelets, differential WBC, ESR, PBF, ABO/Rh typing, crossmatching, PT, aPTT, BT, CT, sickling test, genotype/Hb electrophoresis, reticulocyte count, malaria parasite test.
- Chemistry investigations: Covered on both plans. Includes glucose, urea, creatinine, electrolytes, calcium, phosphate, proteins, bilirubin, ALP, ALT/SGPT, AST/SGOT, GGT, cholesterol, triglycerides, HDL, LDL, etc.
- Microbiology investigations: Covered on both plans. Includes blood/urine/sputum/wound/throat/stool/CSF cultures, Gram stain, AFB/TB, AST sensitivity, HVS M/C/S, HBsAg, HCV antibody, HIV, syphilis, malaria, stool ova/parasite, H. pylori, fungal culture, KOH mount, etc.
- Advanced investigations: Covered on both plans only when stated in the schedule. Includes Alpha-1 Antitrypsin, HBA1C, 24-hour creatinine clearance, bleeding time, blood urea nitrogen, chlamydia screening, clotting time, Coombs direct/indirect, creatinine phosphokinase, CSF M/C/S, D-Dimer, G-6PD, hepatitis B/C screening, HBSAg, HIV confirmatory/screening, immunofluorescence assay, osmotic fragility, Pap smear/cytology, PSA, protein electrophoresis, semen M/C/S, SFA, serum immunoglobulins/antibody, etc.
- CT/MRI:
  Bronze: CT only, emergency cases only, once per annum
  Silver: CT or MRI scan only, emergency cases only, once per annum
- ECG: Covered on both plans.
- Echocardiogram: Bronze NOT COVERED; Silver covered.
- Molecular diagnostics including Covid-19 testing: Designated center only. Bronze NOT COVERED; Silver once per annum.

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Care:
  Bronze: 2 sessions/year | Silver: 4 sessions/year

Physiotherapy:
  Bronze: 30,000 NGN limit | Silver: 12 sessions

Endoscopic Procedures (therapeutic and diagnostic):
  Bronze: NOT COVERED | Silver: COVERED
  Includes colonoscopy, flexible sigmoidoscopy, proctoscopy, anoscopy, capsule endoscopy, enteroscopy, EUS, bronchoscopy, laryngoscopy, nasopharyngoscopy, diagnostic cystoscopy, ureteroscopy, diagnostic nephroscopy, diagnostic hysteroscopy, laparoscopy.

Wellness — Gym (Principal only):
  Bronze: NOT COVERED | Silver: 2x/month

Wellness — Spa (Principal only):
  Bronze: NOT COVERED | Silver: NOT COVERED

──────────────────────────────────────────
SURGERY CLASSIFICATION (all draw from Surgical Care Limit unless excluded)
──────────────────────────────────────────
MINOR SURGERIES (covered both plans):
Wound suturing, incision and drainage of abscess, removal of foreign bodies, circumcision,
excision of lumps, punch biopsy, skin biopsy, ear syringing, episiotomy repair,
Bartholin cyst incision and drainage, closed reduction of minor dislocations, POP application

INTERMEDIATE SURGERIES (covered both plans):
Appendectomy, hernia repair (inguinal/umbilical), hydrocelectomy, hemorrhoidectomy,
fistulectomy/fistulotomy, excision of large lipoma, incisional biopsy, varicose vein surgery (simple),
pilonidal sinus excision, tonsillectomy, adenoidectomy, septoplasty, turbinectomy, nasal polypectomy,
TURP, orchidopexy, varicocelectomy, cystoscopy, myomectomy (simple), D&C, MVA,
tubal ligation, repair of 3rd/4th degree perineal tear, ORIF (simple fractures),
arthroscopy (diagnostic/simple), tendon repair, removal of deep implants,
wide local excision of skin lesions, keloid excision with flap closure, skin grafting

MAJOR SURGERIES (covered both plans unless excluded):
Exploratory laparotomy, bowel resection and anastomosis, gastrectomy, colectomy,
splenectomy, pancreatic surgery (Whipple), thyroidectomy, mastectomy, major trauma surgery,
hepatectomy, craniotomy, brain tumor excision, spinal cord decompression, aneurysm clipping,
VP shunt insertion, total hip replacement, total knee replacement, spinal fusion surgery,
major pelvic fracture fixation, limb amputation (major), radical prostatectomy,
nephrectomy (partial or total), cystectomy, major reconstructive urologic surgery,
radical hysterectomy, complex myomectomy, obstetric hysterectomy,
surgery for ruptured ectopic pregnancy, pelvic reconstructive surgery

NOTE: Exclusions override the surgery classification. Joint replacements and prosthetic limbs are listed as exclusions in this C022 source.

──────────────────────────────────────────
MATERNITY, NEONATAL & IMMUNIZATIONS
──────────────────────────────────────────
Eligibility rule: Maternity and neonatal services are exclusive to family plan subscribers OR individual subscribers in companies with more than 50 principals. If family/corporate-principal status is unknown, ESCALATE rather than approve or deny solely on plan tier.

Maternity:
- Antenatal care: Covered on both plans.
- Normal delivery: Covered on both plans.
- Induction of labour: Covered on both plans.
- Delivery abroad reimbursement: NOT COVERED on both plans.
- Caesarean section: Covered on both plans, up to surgical limit.
- Postnatal care: Covered for 6 weeks on both plans.

Neonatal:
- Male circumcision and ear piercing: Covered on both plans.
- Phototherapy: Bronze 24 hours | Silver 48 hours.
- Mild/moderate neonatal sepsis treatment: Covered on both plans.
- Congenital abnormalities treatment for children born on the plan:
  Bronze: NOT COVERED | Silver: 50,000
- Neonatal incubator/SCBU benefit can only be drawn from the nursing mother's limit for a live birth.

Immunizations:
- NPI immunizations for ages 0-5: BCG, Measles, Pentavalent, Oral polio, IPV, Vitamin A supplementation, Pentavalent vaccine covered on both plans.
- Additional immunizations ages 0-5:
  Bronze: NOT COVERED | Silver: Hepatitis B, HiB, Yellow Fever
- Additional immunizations ages 6+:
  Bronze: NOT COVERED | Silver: Hepatitis B, Yellow Fever

──────────────────────────────────────────
EMERGENCY, FAMILY PLANNING, WELLNESS & OTHER BENEFITS
──────────────────────────────────────────
Emergency Response Service:
- Phone aid/telemedicine first-aid: Covered on both plans.
- Onsite deployment of first responder with advanced trauma kit: Covered on both plans.
- Hospital-to-hospital transport: Covered on both plans.
- Home-to-hospital and roadside-to-hospital transport: Covered on both plans.

Family Planning Services:
  Bronze: Oral and injectables
  Silver: IUCD (Copper T), injectables

Health Checks:
  Bronze: Limited basic — physical, BP, urinalysis, blood sugar, PCV, serum cholesterol.
  Silver: Limited basic — physical, BP, urinalysis, blood sugar, PCV, PSA for men above 40 years, serum cholesterol.
  Health checks can only be done at designated hospitals/diagnostic centers during institutions' health week and are non-refundable otherwise.

Onsite/online promotional health talks, webinars, health education series: Covered on both plans.
Aman Care App: Covered on both plans.

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. PLAN SIZE: C022 is designed for a minimum of 50 principals.
2. MATERNITY/NEONATAL ELIGIBILITY: Covered only for family plan subscribers or individual subscribers in companies with more than 50 principals. If subscriber/corporate-principal status is unknown, ESCALATE.
3. AGE LIMIT: Principal must be 65 or under. Enrollees/dependents above 65 must be enrolled on the Senior Citizens Plan, not C022 tiers.
4. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
5. GYM/SPA: Principal only. Other terms and conditions apply.
6. ROOM TYPE: Bronze=General Ward, Silver=Semi-Private Ward. Executive/VIP rooms are not covered.
7. NEONATAL BENEFIT: Drawn from nursing mother's limit for a live birth only.
8. FAMILY PREMIUM: Family premium quoted is for family of six (principal, spouse, and 4 children under 24).
9. PREMIUM PAYMENT: Premium computed is payable once annually. Flexible payment may be arranged by negotiation.
10. INSURANCE/LIMITS: Insurance and limits of services are not transferable.

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (BOTH PLANS)
──────────────────────────────────────────
1. Transplant surgery
2. Speech disorder / speech disorders
3. Thyroid disorders, neurological and neurosurgical disorders
4. Plastic/cosmetic surgeries
5. Advanced and complex investigations not stated in the schedule of covered services
6. Other investigations and treatment problems relating to infertility, including hydrotubation, hysterosalpingogram, IVF, GIFT, artificial insemination
7. Virility enhancing drugs
8. Herbal drugs, non-prescription drugs, food supplements, experimental drugs and experimental treatment
9. Other laboratory investigations not listed in the schedule of covered services
10. Dental care not listed in the schedule of covered services
11. Home care and domiciliary services
12. Joint replacements and prosthetic limbs
13. Long-term psychiatric illness longer than 6 months
14. Comprehensive health screening/well-persons check outside the scope of the covered health checks
15. Pre-school health examinations
16. Treatment for newborn not registered on the plan after 6 weeks of birth
17. Neonatal care not listed under neonatal services
18. Self-inflicted injuries
19. Treatment of obesity
20. All Covid-19 and Hepatitis treatment
21. Covid-19 testing except as stated in the schedule of covered services
22. Room upgrades beyond the specified plan benefit
23. Management of severe burns covering more than 10% body surface area
24. Learning difficulties, behavioral and developmental problems
25. Consultations with unrecognized consultants, hospitals, family doctors, therapists, dental practitioners, or complementary medicines practitioners
26. Any other treatment, service, procedure, or investigation not listed in the schedule of covered medical services
"""


PLAN_LIMITS = {
    "Bronze": {
        "annual_cap": 1_000_000,
        "inpatient": 600_000,
        "outpatient": 400_000,
        "surgical": 250_000,
        "dental": 25_000,
        "optical_total": 30_000,
        "cancer": 100_000,
        "chronic": 150_000,
        "hiv": 100_000,
        "dialysis": None,
        "neonatal": 50_000,
        "congenital": None,
        "mortuary": None,
        "critical_illness_death": None,
        "fertility": None,
    },
    "Silver": {
        "annual_cap": 1_900_000,
        "inpatient": 1_200_000,
        "outpatient": 700_000,
        "surgical": 400_000,
        "dental": 50_000,
        "optical_total": 65_000,
        "cancer": 250_000,
        "chronic": 250_000,
        "hiv": 150_000,
        "dialysis": 70_000,
        "neonatal": 100_000,
        "congenital": 50_000,
        "mortuary": 50_000,
        "critical_illness_death": 100_000,
        "fertility": 35_000,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
