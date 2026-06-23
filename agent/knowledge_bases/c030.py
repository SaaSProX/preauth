"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO CORPORATE PLAN C030 BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}
Source workbook: C030.xlsx, sheet "Sheet1"

PLAN NAME: Kaduna Electric Customize Plan
PLAN TIERS: Bronze Customize only
Normalize Bronze Customize to Bronze for limit lookup.

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Total Limit / Maximum Annual Benefit (master cap — overrides all other limits):
  Bronze: 1,500,000

Inpatient and outpatient benefits are covered but the source workbook does not state separate inpatient/outpatient monetary sublimits.
For deterministic utilization, use the Total Limit as the fallback inpatient and outpatient bucket limit unless AMAN consumption data supplies a more specific limit row.

Surgical Care Limit (covers all surgery types — minor, intermediate, major, unless excluded):
  Bronze: 200,000

Dental Care Limit:
  Bronze: 25,000

Optical — Lenses/Frames/Contact Lenses (once every 2 years only):
  Bronze: 10,000 (lenses only)

Optical — Eye Testing + Acute/Chronic Eye Disease Treatment (surgery inclusive):
  Bronze: 25,000

Optical — Total Optical Limit:
  Bronze: 35,000

Cancer Care (Consultation, Investigation, Counselling, Chemotherapy, Radiotherapy, Surgery):
  Bronze: 100,000

Chronic Disease Medication:
  Bronze: 80,000

HIV/AIDS Care Treatment:
  Bronze: 100,000

Kidney Dialysis:
  Bronze: NOT COVERED

Critical Illness + Death Cover:
  Bronze: NOT COVERED

Mortuary Services:
  Bronze: NOT COVERED

Infertility Investigation:
  Bronze: NOT COVERED

Treatment of Congenital Abnormalities (for children born on the plan):
  Bronze: NOT COVERED

──────────────────────────────────────────
COVERED INPATIENT / OUTPATIENT SERVICES
──────────────────────────────────────────
Telemedicine: Unlimited.
Free medication pick-up and door-step delivery: Covered where available.

Inpatient:
- Accidents and emergencies: resuscitative or lifesaving initial treatment, investigations, and interventions are covered.
- Accommodation: General Ward.
- Admission ward care, medications and consumables, blood transfusion, feeding where available: Covered.
- Inpatient medication and medical/surgical consumables: Covered.
- Mother accommodation for dependent admission, SCBU/NICU only, excluding feeding: NOT COVERED.
- ICU/HDU: 24 hours.

Consultations:
- General consultations, initial and follow-up: Covered.
- Specialist consultations, initial and follow-up: Covered, including cardiology, endocrinology, nephrology, gastroenterology, pulmonology, infectious disease, rheumatology, dermatology, neurology, family medicine, psychiatry, paediatrics, obstetrics/gynaecology, surgery, orthopaedics, neurosurgery, cardiothoracic surgery, urology, paediatric surgery, ENT, ophthalmology, anaesthesia, radiology, radiation oncology, pathology, haematology, chemical pathology, microbiology, immunology, clinical pharmacology, emergency medicine, palliative medicine, genetics, oral/maxillofacial surgery, dentistry, and similar listed specialties.

Medications:
- Outpatient prescription medicines: Covered.
- Chronic disease medication: Covered up to 80,000.

Tests and investigations:
- X-rays and basic diagnostic tests: Covered.
- WHO essential in-vitro diagnostic laboratory tests: Covered.
- Haematology investigations: Covered. Includes FBC/CBC, PCV, Hb, WBC/RBC/platelets, differential WBC, ESR, PBF, ABO/Rh typing, crossmatching, PT, aPTT, BT, CT, sickling test, genotype/Hb electrophoresis, reticulocyte count, malaria parasite test.
- Chemistry investigations: Covered. Includes glucose, urea, creatinine, electrolytes, calcium, phosphate, proteins, bilirubin, ALP, ALT/SGPT, AST/SGOT, GGT, cholesterol, triglycerides, HDL, LDL, etc.
- Microbiology investigations: Covered. Includes blood/urine/sputum/wound/throat/stool/CSF cultures, Gram stain, AFB/TB, AST sensitivity, HVS M/C/S, HBsAg, HCV antibody, HIV, syphilis, malaria, stool ova/parasite, H. pylori, fungal culture, KOH mount, etc.
- Advanced investigations listed in the schedule include Alpha-1 Antitrypsin, HBA1C, 24-hour creatinine clearance, bleeding time, blood urea nitrogen, chlamydia screening, clotting time, Coombs direct/indirect, creatinine phosphokinase, CSF M/C/S, D-Dimer, G-6PD, hepatitis B/C screening, HBSAg, HIV confirmatory/screening, immunofluorescence assay, osmotic fragility, Pap smear/cytology, PSA, protein electrophoresis, semen M/C/S, SFA, serum immunoglobulins/antibody, etc.
- CT/MRI:
  Bronze: CT/MRI scan only, emergency cases only, once per annum
- ECG: Covered.
- Echocardiogram: Covered.
- Molecular diagnostics including Covid-19 testing: NOT COVERED.

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Care:
  Bronze: 1 session

Physiotherapy:
  Bronze: Covered. Source workbook does not state a session count or monetary sublimit; use coverage prompt context and escalate if utilization cannot be determined from AMAN consumption data.

Neonatal Care:
  Bronze: 24 hours for mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU.

Wellness — Gym:
  Bronze: NOT COVERED

Wellness — Spa:
  Bronze: NOT COVERED

──────────────────────────────────────────
SURGERY CLASSIFICATION (draws from Surgical Care Limit unless excluded)
──────────────────────────────────────────
MINOR SURGERIES (covered):
Wound suturing, incision and drainage of abscess, removal of foreign bodies, circumcision,
excision of lumps, punch biopsy, skin biopsy, ear syringing, episiotomy repair,
Bartholin cyst incision and drainage, closed reduction of minor dislocations, POP application

INTERMEDIATE SURGERIES (covered):
Appendectomy, hernia repair (inguinal/umbilical), hydrocelectomy, hemorrhoidectomy,
fistulectomy/fistulotomy, excision of large lipoma, incisional biopsy, varicose vein surgery (simple),
pilonidal sinus excision, tonsillectomy, adenoidectomy, septoplasty, turbinectomy, nasal polypectomy,
TURP, orchidopexy, varicocelectomy, cystoscopy, myomectomy (simple), D&C, MVA,
tubal ligation, repair of 3rd/4th degree perineal tear, ORIF (simple fractures),
arthroscopy (diagnostic/simple), tendon repair, removal of deep implants,
wide local excision of skin lesions, keloid excision with flap closure, skin grafting

MAJOR SURGERIES (covered unless excluded):
Exploratory laparotomy, bowel resection and anastomosis, gastrectomy, colectomy,
splenectomy, pancreatic surgery (Whipple), thyroidectomy, mastectomy, major trauma surgery,
hepatectomy, craniotomy, brain tumor excision, spinal cord decompression, aneurysm clipping,
VP shunt insertion, total hip replacement, total knee replacement, spinal fusion surgery,
major pelvic fracture fixation, limb amputation (major), radical prostatectomy,
nephrectomy (partial or total), cystectomy, major reconstructive urologic surgery,
radical hysterectomy, complex myomectomy, obstetric hysterectomy,
surgery for ruptured ectopic pregnancy, pelvic reconstructive surgery

NOTE: Exclusions override the surgery classification. Joint replacements and prosthetic limbs are listed as exclusions in this C030 source.

──────────────────────────────────────────
MATERNITY, NEONATAL & IMMUNIZATIONS
──────────────────────────────────────────
C030 lists maternity and neonatal benefits but does not state the C001/C022 family-plan-or-50-principals eligibility rule. If subscriber/dependent eligibility for maternity or neonatal care is unclear from the payload, ESCALATE rather than approve or deny solely on plan tier.

Maternity:
- Antenatal care: Covered.
- Normal delivery: Covered.
- Induction of labour: Covered.
- Delivery abroad reimbursement: NOT COVERED.
- Caesarean section: Covered up to surgical limit.
- Postnatal care: Covered for 6 weeks.

Neonatal:
- Male circumcision and ear piercing: Covered.
- Phototherapy: 24 hours.
- Mild/moderate neonatal sepsis treatment: Covered.
- Incubator care and SCBU: 24 hours.
- Congenital abnormalities treatment for children born on the plan: NOT COVERED.
- Neonatal benefit can only be drawn from the nursing mother's limit for a live birth.

Immunizations:
- NPI immunizations for ages 0-5: BCG, Measles, Hepatitis B, DPT, Oral polio, IPV, Vitamin A supplementation, Pentavalent vaccine.
- Additional immunizations ages 0-5: Chicken Pox.
- Additional immunizations ages 6+: NOT COVERED.

──────────────────────────────────────────
EMERGENCY, FAMILY PLANNING, WELLNESS & OTHER BENEFITS
──────────────────────────────────────────
Emergency Response Service:
- Phone aid/telemedicine first-aid: Covered.
- Onsite deployment of first responder with advanced trauma kit: Covered.
- Hospital-to-hospital transport: Covered.
- Home-to-hospital and roadside-to-hospital transport: Covered.

Family Planning Services:
  Bronze: Oral and injectables

Health Checks:
  Bronze: Limited basic — physical, BP, urinalysis, blood sugar, PCV, serum cholesterol.
  Health checks can only be done at designated hospitals/diagnostic centers during institutions' health week and are non-refundable otherwise.

Onsite/online promotional health talks, webinars, health education series: Covered.
Aman Care App: Covered.

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. PREMIUM PAYMENT: Premium computed is payable once annually. Flexible payment may be arranged by negotiation.
2. FAMILY PREMIUM: Family premium quoted is for family of six (principal, spouse, and 4 children under 24).
3. AGE LIMIT: Principal must be 65 or under. Enrollees/dependents above 65 must be enrolled on the Senior Citizens Plan.
4. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
5. ROOM TYPE: General Ward. Executive/VIP rooms are not covered.
6. NEONATAL BENEFIT: Drawn from nursing mother's limit for a live birth only.
7. INSURANCE/LIMITS: Insurance and limits of services are not transferable.

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (C030 PLAN)
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
        "annual_cap": 1_500_000,
        "inpatient": 1_500_000,
        "outpatient": 1_500_000,
        "surgical": 200_000,
        "dental": 25_000,
        "optical_total": 35_000,
        "cancer": 100_000,
        "chronic": 80_000,
        "hiv": 100_000,
        "dialysis": None,
        "neonatal": 1_500_000,
        "congenital": None,
        "mortuary": None,
        "critical_illness_death": None,
        "fertility": None,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
