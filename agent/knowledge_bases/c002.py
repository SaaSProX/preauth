"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO CORPORATE PLAN C002 BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}
Source document: C002.pdf, Appendix A - Aman HMO Plans
Source date shown in PDF: September 20, 2025

PLAN TIERS: Basic only

──────────────────────────────────────────
CONTRIBUTIONS / PREMIUMS (NGN) — CONTEXT ONLY, NOT A COVERAGE LIMIT
──────────────────────────────────────────
Individual Premium:
  Basic: 38,000

Family of 3 Premium:
  Basic: 114,000

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Maximum Annual Benefit:
  NOT STATED in the C002 source PDF. Do not invent an annual master cap. Use explicit benefit bucket limits and AMAN consumption data where available.

Inpatient Limit:
  Basic: 180,000

Outpatient Limit:
  Basic: 120,000

Surgical Care Limit (surgeries including day-case procedures, minor, intermediate, major, cesarean section, and endoscopic procedures unless excluded):
  Basic: 150,000

Maternity — Antenatal Care + Normal Delivery + Postnatal Care (6 weeks):
  Basic: 120,000

Dental Care Limit:
  Basic: 15,000
  Dental is limited to relief of pain, fillings, nonsurgical extractions, preventive care, scaling and polishing only.

Optical — Lenses/Frames/Contact Lenses (once every 2 years only):
  Basic: 5,000 (lenses only)

Optical — Eye Testing + Acute/Chronic Eye Disease Treatment:
  Basic: 20,000

Optical — Total Optical Limit (sum of optical sublimits; no separate total row in source PDF):
  Basic: 25,000

Cancer Care:
  Basic: NOT COVERED

Chronic Disease Medication:
  Basic: 80,000

HIV/AIDS Care and Treatment:
  Referral to accredited center. No monetary sublimit is stated in the C002 source PDF.

Kidney Dialysis:
  Basic: NOT COVERED

Neonatal Care — mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU:
  Basic: NOT COVERED

Treatment of Congenital Abnormalities (for children born on the plan):
  Basic: NOT COVERED

Mortuary Services:
  Basic: NOT COVERED

Critical Illness + Death Cover:
  Basic: NOT COVERED

Fertility Investigation:
  Basic: 30,000 (basic consultation and investigation)

Physiotherapy:
  Basic: 20,000

──────────────────────────────────────────
COVERED INPATIENT / OUTPATIENT SERVICES
──────────────────────────────────────────
Telemedicine: Unlimited.

Inpatient:
- Accidents and emergencies: resuscitative or lifesaving initial treatment is covered up to inpatient limit.
- Accommodation: General Ward, 15 days/annum.
- Inpatient medication and medical/surgical consumables: Covered up to inpatient limit.
- Mother accommodation for dependent admission, SCBU/NICU only, excluding feeding: NOT COVERED.
- ICU/HDU: 24 hours.
- Psychiatric hospitalization: NOT COVERED.

Consultations:
- General consultations, initial and follow-up: Covered up to outpatient limit.
- Specialist consultations, initial and follow-up: Covered up to outpatient limit.

Tests and investigations:
- X-rays and basic diagnostic tests: Covered up to outpatient limit.
- WHO essential in-vitro diagnostic laboratory tests: Covered up to outpatient limit.
- Advanced/complex investigations limited to CT scan, MRI scan, and echocardiogram: NOT COVERED.
- Molecular diagnostics including Covid-19 testing at designated center: NOT COVERED.

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Treatment:
  Basic: Up to outpatient limit

Physiotherapy:
  Basic: 20,000

Wellness — Gym/Spa:
  Basic: NOT COVERED

──────────────────────────────────────────
SURGERY CLASSIFICATION (draws from Surgical Care Limit unless excluded)
──────────────────────────────────────────
Surgery category includes surgeries, day-case procedures, minor surgeries, intermediate surgeries, major surgeries, cesarean section, and therapeutic/diagnostic endoscopic procedures.

FIRST YEAR SURGICAL EXCLUSION: Non-accidental surgical claims incurred within the first year of cover are excluded.

NOTE: Exclusions override the surgery classification. Joint replacements and prosthetic limbs are listed as exclusions in this C002 source.

──────────────────────────────────────────
MATERNITY, NEONATAL & IMMUNIZATIONS
──────────────────────────────────────────
C002 lists maternity benefits but does not state the family-plan-or-50-principals eligibility rule. If subscriber/dependent eligibility for maternity or neonatal care is unclear from the payload, ESCALATE rather than approve or deny solely on plan tier.

Maternity:
- Antenatal care + normal delivery + postnatal care for 6 weeks: Covered up to 120,000.
- Caesarean section: Covered under the surgical limit.

Neonatal:
- Neonatal care for mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU: NOT COVERED.
- Male circumcision and ear piercing: Covered up to outpatient limit.
- Congenital abnormalities treatment for children born on the plan: NOT COVERED.
- Neonatal benefit can only be drawn from the nursing mother's limit for a live birth.

Immunizations:
- NPI immunizations for ages 0-5: BCG, Measles, DPT, Oral polio, IPV, Vitamin A supplementation, Pentavalent vaccine.
- Additional immunizations ages 0-5: NOT COVERED.
- Additional immunizations ages 6+: NOT COVERED.

──────────────────────────────────────────
EMERGENCY, FAMILY PLANNING, WELLNESS & OTHER BENEFITS
──────────────────────────────────────────
Ambulance Evacuation Services:
- Hospital-to-hospital transport: Covered.
- Home-to-hospital and roadside-to-hospital transport: 2 times per annum.

Family Planning Services:
  Basic: Oral and injectables

Health Checks:
  Basic: Limited basic - physical examination, BP, BMI, urinalysis, blood sugar, PCV, visual acuity, HBsAg.

HIV/AIDS Care and Treatment:
  Referral to accredited center.

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. BASIC ONLY: C002 has only the Basic plan tier.
2. NO ANNUAL MASTER CAP STATED: The PDF does not state a Maximum Benefits per Enrollee per Annum row. Use explicit bucket limits and AMAN consumption rows.
3. FIRST YEAR SURGICAL EXCLUSION: Non-accidental surgical claims incurred within the first year of cover are excluded.
4. AGE LIMIT: Age limit on C002 plan is 60 years.
5. FAMILY PREMIUM: Family premium quoted is for family of six (principal, spouse, and 4 children under 18), even though the pricing row shown is Family of 3 Premium.
6. PREMIUM PAYMENT: Premium computed is payable once annually based on the population.
7. HEALTH CHECKS/WELLNESS NOTE: Principal only. Other terms and conditions apply.

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (C002 BASIC PLAN)
──────────────────────────────────────────
1. Non-accidental surgical claims incurred within the first year of cover
2. Transplant surgery
3. Plastic/cosmetic surgeries
4. Advanced and complex investigations not stated in the schedule of covered services
5. Other investigations and treatment problems relating to infertility, including hydrotubation, hysterosalpingogram, IVF, GIFT, artificial insemination
6. Virility enhancing drugs
7. Herbal drugs, non-prescription drugs, food supplements, experimental drugs and experimental treatment
8. Other laboratory investigations not listed in the schedule of covered services
9. Dental care not listed in the schedule of covered services
10. Home care and domiciliary services
11. Joint replacements and prosthetic limbs
12. Long-term psychiatric illness longer than 6 months
13. Comprehensive health screening/well-persons check outside the scope of covered health checks
14. Pre-school health examinations
15. Treatment for newborn not registered on the plan after 6 weeks of birth
16. Neonatal care not listed under neonatal services
17. Self-inflicted injuries
18. Treatment of obesity
19. All Covid-19 treatment
20. Covid-19 testing except as stated in the schedule of covered services
21. Speech disorders
22. Room upgrades beyond the specified plan benefit
23. Management of severe burns covering more than 10% body surface area
24. Learning difficulties, behavioral and developmental problems
25. Consultations with unrecognized consultants, hospitals, family doctors, therapists, dental practitioners, or complementary medicines practitioners
26. Any other treatment, service, procedure, or investigation not listed in the schedule of covered medical services
"""


PLAN_LIMITS = {
    "Basic": {
        "annual_cap": None,
        "inpatient": 180_000,
        "outpatient": 120_000,
        "surgical": 150_000,
        "maternity": 120_000,
        "dental": 15_000,
        "optical_total": 25_000,
        "cancer": None,
        "chronic": 80_000,
        "hiv": None,
        "dialysis": None,
        "neonatal": None,
        "congenital": None,
        "mortuary": None,
        "critical_illness_death": None,
        "fertility": 30_000,
        "physiotherapy": 20_000,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
