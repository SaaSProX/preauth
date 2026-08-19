"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO CORPORATE PLAN C004 BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}
Source workbook: C004.xlsx, sheet "Appendix A - Benefits Table"

PLAN TIERS (ascending): Silver → Gold → Platinum → Platinum Plus
C004 has no Bronze tier in the source workbook.

──────────────────────────────────────────
CONTRIBUTIONS / PREMIUMS (NGN) — CONTEXT ONLY, NOT A COVERAGE LIMIT
──────────────────────────────────────────
Individual Premium:
  Silver: 113,450 | Gold: 208,359 | Platinum: 356,672 | Platinum Plus: 767,773

Family Premium:
  Silver: 430,261 | Gold: 751,453.13 | Platinum: 1,349,743.50 | Platinum Plus: 3,071,090

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Maximum Annual Benefit (master cap — overrides all other limits):
  Silver: 2,500,000 | Gold: 5,000,000 | Platinum: 8,000,000 | Platinum Plus: 10,000,000

Inpatient Limit:
  Silver: 1,000,000 | Gold: 2,500,000 | Platinum: 3,500,000 | Platinum Plus: 4,500,000

Outpatient Limit:
  Silver: 400,000 | Gold: 1,100,000 | Platinum: 1,500,000 | Platinum Plus: 2,000,000

Surgical Care Limit (covers surgeries including day-case procedures, minor, intermediate, major, cesarean section, and endoscopic procedures unless excluded):
  Silver: 500,000 | Gold: 1,200,000 | Platinum: 2,000,000 | Platinum Plus: 3,000,000

Maternity — Antenatal Care + Normal Delivery + Postnatal Care (6 weeks) global limit:
  Silver: 150,000 | Gold: 250,000 | Platinum: 500,000 | Platinum Plus: 700,000

Dental Care Limit:
  Silver: 50,000 | Gold: 100,000 | Platinum: 200,000 | Platinum Plus: 300,000

Optical — Lenses/Frames/Contact Lenses (once every 2 years only):
  Silver: 25,000 | Gold: 45,000 | Platinum: 75,000 | Platinum Plus: 150,000

Optical — Eye Testing + Acute/Chronic Eye Disease Treatment (surgery inclusive):
  Silver: 50,000 | Gold: 75,000 | Platinum: 100,000 | Platinum Plus: 300,000

Optical — Total Optical Limit:
  Silver: 75,000 | Gold: 120,000 | Platinum: 175,000 | Platinum Plus: 450,000

Cancer Care:
  Silver: 100,000 | Gold: 200,000 | Platinum: 500,000 | Platinum Plus: 700,000

Chronic Disease Medication:
  Silver: 200,000 | Gold: 300,000 | Platinum: 500,000 | Platinum Plus: 750,000

HIV/AIDS Care Treatment:
  Silver: 150,000 | Gold: 350,000 | Platinum: 500,000 | Platinum Plus: 500,000

Kidney Dialysis:
  Silver: 70,000 | Gold: 90,000 | Platinum: 120,000 | Platinum Plus: 500,000

Neonatal Care — mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU global limit:
  Silver: 100,000 | Gold: 250,000 | Platinum: 500,000 | Platinum Plus: 700,000

Treatment of Congenital Abnormalities (for children born on the plan):
  Silver: NOT COVERED | Gold: NOT COVERED | Platinum: 250,000 | Platinum Plus: 400,000

Mortuary Services (Cleaning, Embalmment, Storage, Autopsy):
  Silver: 50,000 | Gold: 100,000 | Platinum: 150,000 | Platinum Plus: 150,000

Critical Illness + Death Cover (cancer, kidney failure, heart attack, stroke, or death):
  Silver: 100,000 | Gold: 200,000 | Platinum: 400,000 | Platinum Plus: 400,000

Fertility Investigation:
  Silver: 35,000 (Fertility Consultations, Counseling, USS, SFA)
  Gold: 50,000 (Fertility Consultations, Counseling, USS, SFA)
  Platinum: 100,000 (Fertility Consultations, Counseling, USS, SFA, HSG, Hormone Profile)
  Platinum Plus: 200,000 (Fertility Consultations, Counseling, USS, SFA, HSG, Hormone Profile)

Delivery Abroad Reimbursement:
  Silver: NOT COVERED
  Gold: Normal Delivery $150 / CS $200
  Platinum: Normal Delivery $200 / CS $300
  Platinum Plus: Normal Delivery $300 / CS $400

──────────────────────────────────────────
COVERED INPATIENT / OUTPATIENT SERVICES
──────────────────────────────────────────
Telemedicine: Unlimited on all C004 plans.
Free door-step medication delivery and pharmacy pick-up: Covered on all C004 plans where available.

Inpatient:
- Accidents and emergencies: resuscitative or lifesaving initial treatment, investigations, and interventions are covered on all C004 plans.
- Admission ward care, medications and consumables, blood transfusion, feeding where available: Covered on all C004 plans.
- Accommodation: Silver=Semi-Private Ward; Gold/Platinum/Platinum Plus=Private Ward. Executive/VIP rooms are not covered.
- Inpatient medication and medical/surgical consumables: Covered on all C004 plans.
- Mother accommodation for dependent admission, SCBU/NICU only, excluding feeding:
  Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days
- ICU/HDU:
  Silver: 48 hours | Gold: 72 hours | Platinum: 5 days | Platinum Plus: 7 days

Consultations:
- General consultations, initial and follow-up: Covered on all C004 plans.
- Specialist consultations, initial and follow-up: Covered on all C004 plans, including cardiology, endocrinology, nephrology, gastroenterology, pulmonology, infectious disease, rheumatology, dermatology, neurology, family medicine, psychiatry, paediatrics, obstetrics/gynaecology, surgery, orthopaedics, neurosurgery, cardiothoracic surgery, urology, paediatric surgery, ENT, ophthalmology, anaesthesia, radiology, radiation oncology, pathology, haematology, chemical pathology, microbiology, immunology, clinical pharmacology, emergency medicine, palliative medicine, genetics, oral/maxillofacial surgery, dentistry, and similar listed specialties.

Tests and investigations:
- X-rays and basic diagnostic tests: Covered on all C004 plans.
- WHO essential in-vitro diagnostic laboratory tests: Covered on all C004 plans.
- Haematology investigations: Covered on all C004 plans. Includes FBC/CBC, PCV, Hb, WBC/RBC/platelets, differential WBC, ESR, PBF, ABO/Rh typing, crossmatching, PT, aPTT, BT, CT, sickling test, genotype/Hb electrophoresis, reticulocyte count, malaria parasite test.
- Chemistry investigations: Covered on all C004 plans. Includes glucose, urea, creatinine, electrolytes, calcium, phosphate, proteins, bilirubin, ALP, ALT/SGPT, AST/SGOT, GGT, cholesterol, triglycerides, HDL, LDL, etc.
- Microbiology investigations: Covered on all C004 plans. Includes blood/urine/sputum/wound/throat/stool/CSF cultures, Gram stain, AFB/TB, AST sensitivity, HVS M/C/S, HBsAg, HCV antibody, HIV, syphilis, malaria, stool ova/parasite, H. pylori, fungal culture, KOH mount, etc.
- Advanced investigations: Covered on all C004 plans only when stated in the schedule. Includes Alpha-1 Antitrypsin, HBA1C, 24-hour creatinine clearance, bleeding time, blood urea nitrogen, chlamydia screening, clotting time, Coombs direct/indirect, creatinine phosphokinase, CSF M/C/S, D-Dimer, G-6PD, hepatitis B/C screening, HBSAg, HIV confirmatory/screening, immunofluorescence assay, osmotic fragility, Pap smear/cytology, PSA, protein electrophoresis, semen M/C/S, SFA, serum immunoglobulins/antibody, etc.
- Advanced/complex investigations are limited to CT scan, MRI scan, and echocardiogram:
  Silver: CT/MRI scan only, emergency cases only, once per annum
  Gold: CT/MRI scan only, up to 4 times per annum
  Platinum: Up to outpatient limit
  Platinum Plus: Up to outpatient limit
- Molecular diagnostics including Covid-19 testing: Designated center only.
  Silver: once per annum | Gold/Platinum/Platinum Plus: up to 2 tests per annum

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Care:
  Silver: 4 sessions/year | Gold: 8 sessions/year | Platinum: 12 sessions/year | Platinum Plus: 20 sessions/year

Physiotherapy:
  Silver: 12 sessions | Gold: 18 sessions | Platinum: 24 sessions | Platinum Plus: 30 sessions

Wellness — Gym (Principal only):
  Silver: 2x/month | Gold: 4x/month | Platinum: 8x/month | Platinum Plus: Unlimited

Wellness — Spa (Principal only):
  Silver: NOT COVERED | Gold: 2 sessions/year | Platinum: 3 sessions/year | Platinum Plus: 4 sessions/year

──────────────────────────────────────────
SURGERY CLASSIFICATION (all draw from Surgical Care Limit unless excluded)
──────────────────────────────────────────
Surgery category includes surgeries, day-case procedures, minor surgeries, intermediate surgeries, major surgeries, cesarean section, and therapeutic/diagnostic endoscopic procedures.

NOTE: Exclusions override the surgery classification. Joint replacements and prosthetic limbs are listed as exclusions in this C004 source.

──────────────────────────────────────────
MATERNITY, NEONATAL & IMMUNIZATIONS
──────────────────────────────────────────
C004 source lists maternity and neonatal benefits with global limits, but does not state the C001/C022 family-plan-or-50-principals eligibility rule. If subscriber/dependent eligibility for maternity or neonatal care is unclear from the payload, ESCALATE rather than approve or deny solely on plan tier.

Maternity:
- Antenatal care + normal delivery + postnatal care for 6 weeks: Covered up to the maternity global limit.
- Delivery abroad reimbursement: Silver NOT COVERED; Gold/Platinum/Platinum Plus covered at the stated USD reimbursement limits.
- Cesarean section: Covered under the surgical limit.

Neonatal:
- Mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU: Covered up to the neonatal global limit.
- Male circumcision and ear piercing: Covered up to outpatient limit on all C004 plans.
- Congenital abnormalities treatment for children born on the plan:
  Silver: NOT COVERED | Gold: NOT COVERED | Platinum: 250,000 | Platinum Plus: 400,000
- Neonatal benefit can only be drawn from the nursing mother's limit for a live birth.

Immunizations:
- NPI immunizations for ages 0-5: BCG, Measles, DPT, Oral polio, IPV, Vitamin A supplementation, Pentavalent vaccine covered on all C004 plans.
- Additional immunizations ages 0-5:
  Silver: Hepatitis B, HiB, Yellow Fever
  Gold/Platinum/Platinum Plus: Hepatitis A, Hepatitis B, Hib, Chicken Pox, MMR, Pneumococcal, Rotavirus, Meningitis, Yellow Fever, Typhoid Fever
- Additional immunizations ages 6+:
  Silver: Hepatitis B, Yellow Fever
  Gold: Hepatitis B, Yellow Fever
  Platinum/Platinum Plus: Meningitis, Yellow Fever, Hepatitis B

──────────────────────────────────────────
EMERGENCY, FAMILY PLANNING, WELLNESS & OTHER BENEFITS
──────────────────────────────────────────
Emergency Response Service:
- Phone aid/telemedicine first-aid: Covered on all C004 plans.
- Onsite deployment of first responder with advanced trauma kit: Covered on all C004 plans.
- Hospital-to-hospital transport: Covered on all C004 plans.
- Home-to-hospital and roadside-to-hospital transport: Covered on all C004 plans.

Family Planning Services:
  Silver: IUCD (Copper T), injectables
  Gold: IUCD (Copper T), injectables, pills
  Platinum: IUCD (Copper T), injectables, pills, Norplant
  Platinum Plus: IUCD (Copper T), injectables, pills, Norplant

Health Checks:
  Silver: Limited basic — physical, BP, urinalysis, genotype, blood sugar, blood group, PCV, PSA, serum cholesterol.
  Gold: Limited basic — physical, BP, urinalysis, genotype, blood sugar, blood group, PCV, liver function test, electrolytes/urea/creatinine, Pap smear, PSA, mammography.
  Platinum: Limited basic — physical, BP, urinalysis, genotype, blood sugar, blood group, PCV, serum cholesterol, liver function test, electrolytes/urea/creatinine, Pap smear, ECG, PSA, mammography.
  Platinum Plus: Physical examination, BMI, urinalysis, PCV, blood pressure, blood sugar, chest X-ray, ECG, serum cholesterol, liver function test, electrolytes/urea/creatinine, annual mammogram for women >40, breast scan every 2 years for women >30, cervical smears every 2 years for women >30, PSA for men above 40.
  Health checks can only be done at designated hospitals/diagnostic centers during institutions' health week and are non-refundable otherwise.

Onsite/online promotional health talks, webinars, health education series: Covered on all C004 plans.

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. PREMIUM PAYMENT: Premium computed is payable once annually based on the population.
2. FAMILY PREMIUM: Family premium quoted is for family of six (principal, spouse, and 4 children under 24).
3. AGE LIMIT: Age limit on C004 plans is 65 years.
4. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
5. GYM/SPA: Principal only. Other terms and conditions apply.
6. ROOM TYPE: Silver=Semi-Private Ward; Gold/Platinum/Platinum Plus=Private Ward. Executive/VIP rooms not covered.
7. NEONATAL BENEFIT: Drawn from nursing mother's limit for a live birth only.
8. INSURANCE/LIMITS: Insurance and limits of services are not transferable.

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (ALL C004 PLANS)
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
    "Silver": {
        "annual_cap": 2_500_000,
        "inpatient": 1_000_000,
        "outpatient": 400_000,
        "surgical": 500_000,
        "maternity": 150_000,
        "dental": 50_000,
        "optical_total": 75_000,
        "cancer": 100_000,
        "chronic": 200_000,
        "hiv": 150_000,
        "dialysis": 70_000,
        "neonatal": 100_000,
        "congenital": None,
        "mortuary": 50_000,
        "critical_illness_death": 100_000,
        "fertility": 35_000,
    },
    "Gold": {
        "annual_cap": 5_000_000,
        "inpatient": 2_500_000,
        "outpatient": 1_100_000,
        "surgical": 1_200_000,
        "maternity": 250_000,
        "dental": 100_000,
        "optical_total": 120_000,
        "cancer": 200_000,
        "chronic": 300_000,
        "hiv": 350_000,
        "dialysis": 90_000,
        "neonatal": 250_000,
        "congenital": None,
        "mortuary": 100_000,
        "critical_illness_death": 200_000,
        "fertility": 50_000,
    },
    "Platinum": {
        "annual_cap": 8_000_000,
        "inpatient": 3_500_000,
        "outpatient": 1_500_000,
        "surgical": 2_000_000,
        "maternity": 500_000,
        "dental": 200_000,
        "optical_total": 175_000,
        "cancer": 500_000,
        "chronic": 500_000,
        "hiv": 500_000,
        "dialysis": 120_000,
        "neonatal": 500_000,
        "congenital": 250_000,
        "mortuary": 150_000,
        "critical_illness_death": 400_000,
        "fertility": 100_000,
    },
    "Platinum Plus": {
        "annual_cap": 10_000_000,
        "inpatient": 4_500_000,
        "outpatient": 2_000_000,
        "surgical": 3_000_000,
        "maternity": 700_000,
        "dental": 300_000,
        "optical_total": 450_000,
        "cancer": 700_000,
        "chronic": 750_000,
        "hiv": 500_000,
        "dialysis": 500_000,
        "neonatal": 700_000,
        "congenital": 400_000,
        "mortuary": 150_000,
        "critical_illness_death": 400_000,
        "fertility": 200_000,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
