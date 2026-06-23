"""Plan-specific Aman HMO knowledge-base data.

These modules intentionally contain static plan facts only. Runtime routing and
agent orchestration stay in agent.agent so new corporate plans can be added
without growing the agent pipeline file.
"""

KNOWLEDGE_BASE_TEMPLATE = """
AMAN HMO CORPORATE PLAN C000 BENEFITS — 2026 KNOWLEDGE BASE
Today's date: {today}
Source document: C000.pdf, Appendix A - Aman HMO Plans

PLAN TIERS (ascending): Bronze → Silver → Gold → Platinum
C000 has no Platinum Plus tier in the source document.

──────────────────────────────────────────
CONTRIBUTIONS / PREMIUMS (NGN) — CONTEXT ONLY, NOT A COVERAGE LIMIT
──────────────────────────────────────────
Individual Premium:
  Bronze: 67,830 | Silver: 107,833 | Gold: 196,594 | Platinum: 385,794

Family Premium:
  Bronze: 250,970 | Silver: 398,980 | Gold: 727,395 | Platinum: 1,427,438

──────────────────────────────────────────
FINANCIAL LIMITS PER PLAN (NGN)
──────────────────────────────────────────
Maximum Annual Benefit:
  NOT STATED in the C000 source PDF. Do not invent an annual master cap. Use the explicit benefit bucket limits below and AMAN consumption data where available.

Inpatient Limit:
  Bronze: 500,000 | Silver: 1,000,000 | Gold: 2,500,000 | Platinum: 3,500,000

Outpatient Limit:
  Bronze: 200,000 | Silver: 400,000 | Gold: 1,100,000 | Platinum: 1,500,000

Surgical Care Limit (surgeries including day-case procedures, minor, intermediate, major, cesarean section, and endoscopic procedures unless excluded):
  Bronze: 100,000 | Silver: 150,000 | Gold: 500,000 | Platinum: 1,000,000

Maternity — Antenatal Care + Normal Delivery + Postnatal Care (6 weeks) global limit:
  Bronze: 100,000 | Silver: 150,000 | Gold: 250,000 | Platinum: 500,000

Dental Care Limit:
  Bronze: 10,000 | Silver: 15,000 | Gold: 30,000 | Platinum: 80,000
  Bronze dental is limited to relief of pain, fillings, nonsurgical extractions, preventive care, scaling and polishing only.

Optical — Lenses/Frames/Contact Lenses (once every 2 years only):
  Bronze: 10,000 (lenses only) | Silver: 15,000 | Gold: 20,000 | Platinum: 35,000

Optical — Eye Testing + Acute/Chronic Eye Disease Treatment (surgery inclusive):
  Bronze: 25,000 | Silver: 50,000 | Gold: 75,000 | Platinum: 100,000

Optical — Total Optical Limit (sum of optical sublimits; no separate total row in source PDF):
  Bronze: 35,000 | Silver: 65,000 | Gold: 95,000 | Platinum: 135,000

Cancer Care:
  Bronze: NOT COVERED | Silver: 100,000 | Gold: 200,000 | Platinum: 500,000

Chronic Disease Medication:
  Bronze: 60,000 | Silver: 120,000 | Gold: 200,000 | Platinum: 300,000

HIV/AIDS Care Treatment:
  Bronze: 100,000 | Silver: 150,000 | Gold: 350,000 | Platinum: 500,000

Kidney Dialysis:
  Bronze: NOT COVERED | Silver: 70,000 | Gold: 90,000 | Platinum: 120,000

Neonatal Care — mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU global limit:
  Bronze: NOT COVERED | Silver: 50,000 | Gold: 150,000 | Platinum: 500,000

Treatment of Congenital Abnormalities (for children born on the plan):
  Bronze: NOT COVERED | Silver: NOT COVERED | Gold: NOT COVERED | Platinum: 250,000

Mortuary Services (Cleaning, Embalmment, Storage, Autopsy):
  Bronze: NOT COVERED | Silver: 50,000 | Gold: 100,000 | Platinum: 150,000

Critical Illness + Death Cover (cancer, kidney failure, heart attack, stroke, or death):
  Bronze: NOT COVERED | Silver: 100,000 | Gold: 200,000 | Platinum: 400,000

Fertility Investigation:
  Bronze: 20,000 (Basic consultation and investigation)
  Silver: 35,000 (Fertility Consultations, Counseling, USS, SFA)
  Gold: 50,000 (Fertility Consultations, Counseling, USS, SFA)
  Platinum: 100,000 (Fertility Consultations, Counseling, USS, SFA, HSG, Hormone Profile)

Physiotherapy:
  Bronze: 30,000 | Silver: 40,000 | Gold: 60,000 | Platinum: 100,000

──────────────────────────────────────────
COVERED INPATIENT / OUTPATIENT SERVICES
──────────────────────────────────────────
Telemedicine: Unlimited on all C000 plans.

Inpatient:
- Accidents and emergencies: resuscitative or lifesaving initial treatment is covered up to inpatient limit on all C000 plans.
- Accommodation:
  Bronze: General Ward, 10 days/annum
  Silver: General Ward, 20 days/annum
  Gold: Private Ward, 20 days/annum
  Platinum: Private Ward, 30 days/annum
- Inpatient medication and medical/surgical consumables: Covered up to inpatient limit on all C000 plans.
- Mother accommodation for dependent admission, SCBU/NICU only, excluding feeding:
  Bronze: NOT COVERED | Silver: NOT COVERED | Gold: General Ward 48 hours | Platinum: Semi-Private Ward 48 hours
- ICU/HDU:
  Bronze: 24 hours | Silver: 48 hours | Gold: 72 hours | Platinum: 5 days
- Psychiatric hospitalization:
  Bronze: NOT COVERED | Silver: NOT COVERED | Gold: NOT COVERED | Platinum: Up to accommodation limit

Consultations:
- General consultations, initial and follow-up: Covered up to outpatient limit on all C000 plans.
- Specialist consultations, initial and follow-up: Covered up to outpatient limit on all C000 plans.

Tests and investigations:
- X-rays and basic diagnostic tests: Covered up to outpatient limit on all C000 plans.
- WHO essential in-vitro diagnostic laboratory tests: Covered up to outpatient limit on all C000 plans.
- Advanced/complex investigations are limited to CT scan, MRI scan, and echocardiogram:
  Bronze: NOT COVERED
  Silver: CT/MRI scan only, emergency cases only, once per annum
  Gold: CT/MRI scan only, up to 4 times per annum
  Platinum: Up to outpatient limit
- Molecular diagnostics including Covid-19 testing: Designated center only.
  Bronze: NOT COVERED | Silver: once per annum | Gold: up to 2 tests per annum | Platinum: up to 2 tests per annum

──────────────────────────────────────────
SESSION / FREQUENCY LIMITS
──────────────────────────────────────────
Psychiatric Treatment:
  Bronze: NOT COVERED | Silver: NOT COVERED | Gold: Outpatient only for 6 months | Platinum: Inpatient/Outpatient

Physiotherapy:
  Bronze: 30,000 | Silver: 40,000 | Gold: 60,000 | Platinum: 100,000

Wellness — Gym (Principal only):
  Bronze: NOT COVERED | Silver: 2x/month | Gold: 4x/month | Platinum: 5x/month

──────────────────────────────────────────
SURGERY CLASSIFICATION (draws from Surgical Care Limit unless excluded)
──────────────────────────────────────────
Surgery category includes surgeries, day-case procedures, minor surgeries, intermediate surgeries, major surgeries, cesarean section, and therapeutic/diagnostic endoscopic procedures.

NOTE: Exclusions override the surgery classification. Joint replacements and prosthetic limbs are listed as exclusions in this C000 source.

──────────────────────────────────────────
MATERNITY, NEONATAL & IMMUNIZATIONS
──────────────────────────────────────────
C000 lists maternity and neonatal benefits with global limits but does not state the family-plan-or-50-principals eligibility rule. If subscriber/dependent eligibility for maternity or neonatal care is unclear from the payload, ESCALATE rather than approve or deny solely on plan tier.

Maternity:
- Antenatal care + normal delivery + postnatal care for 6 weeks: Covered up to maternity global limit.
- Caesarean section: Covered under the surgical limit.

Neonatal:
- Mild/moderate neonatal sepsis, phototherapy, incubator care, and SCBU: Covered up to neonatal global limit for Silver/Gold/Platinum; not covered on Bronze.
- Male circumcision and ear piercing: Covered up to outpatient limit on all C000 plans.
- Congenital abnormalities treatment for children born on the plan: Platinum only, up to 250,000.
- Neonatal benefit can only be drawn from the nursing mother's limit for a live birth.

Immunizations:
- NPI immunizations for ages 0-5: BCG, Measles, DPT, Oral polio, IPV, Vitamin A supplementation, Pentavalent vaccine covered on all C000 plans.
- Additional immunizations ages 0-5:
  Bronze: NOT COVERED | Silver: Hepatitis B, HiB, Yellow Fever | Gold/Platinum: Hepatitis A, Hepatitis B, Hib, Chicken Pox, MMR, Pneumococcal, Rotavirus, Meningitis, Yellow Fever, Typhoid Fever
- Additional immunizations ages 6+:
  Bronze: NOT COVERED | Silver: Hepatitis B, Yellow Fever | Gold: Hepatitis B, Yellow Fever | Platinum: Meningitis, Yellow Fever, Hepatitis B

──────────────────────────────────────────
EMERGENCY, FAMILY PLANNING, WELLNESS & OTHER BENEFITS
──────────────────────────────────────────
Ambulance Evacuation Services:
- Hospital-to-hospital transport: Covered on all C000 plans.
- Home-to-hospital and roadside-to-hospital transport:
  Bronze: 2 times per annum | Silver/Gold/Platinum: Covered

Family Planning Services:
  Bronze: Oral and injectables
  Silver: IUCD (Copper T), injectables
  Gold: IUCD (Copper T), injectables, pills
  Platinum: IUCD (Copper T), injectables, pills, Norplant

Health Checks:
  Bronze: NOT COVERED
  Silver: Limited basic — physical, BP, urinalysis, genotype, blood sugar, blood group, PCV, PSA.
  Gold: Limited basic — physical, BP, urinalysis, genotype, blood sugar, blood group, PCV, thyroid function test, Pap smear, PSA, mammography.
  Platinum: Limited basic — physical, BP, urinalysis, genotype, blood sugar, blood group, PCV, serum cholesterol, thyroid function test, Pap smear, PSA, mammography.
  Health checks can only be done at designated hospitals/diagnostic centers during institutions' health week and are non-refundable otherwise.

──────────────────────────────────────────
SPECIAL RULES
──────────────────────────────────────────
1. C000 HAS NO PLATINUM PLUS: Do not apply Platinum Plus express/no-preauthorization behavior.
2. NO ANNUAL MASTER CAP STATED: The PDF does not state a Maximum Benefits per Enrollee per Annum row. Use explicit bucket limits and AMAN consumption rows.
3. AGE LIMIT: Age limit on C000 plans is 60 years. Enrollees/dependents above the plan age limit should be placed on Senior Citizens Plan.
4. FAMILY PREMIUM: Family premium quoted is for family of six (principal, spouse, and 4 children under 18).
5. PREMIUM PAYMENT: Premium computed is payable once annually based on the population.
6. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
7. GYM: Principal only. Other terms and conditions apply.
8. ROOM TYPE: Executive/VIP rooms not covered.
9. NEONATAL BENEFIT: Drawn from nursing mother's limit for a live birth only.

──────────────────────────────────────────
EXCLUSIONS — ALWAYS DENY (ALL C000 PLANS)
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
        "annual_cap": None,
        "inpatient": 500_000,
        "outpatient": 200_000,
        "surgical": 100_000,
        "maternity": 100_000,
        "dental": 10_000,
        "optical_total": 35_000,
        "cancer": None,
        "chronic": 60_000,
        "hiv": 100_000,
        "dialysis": None,
        "neonatal": None,
        "congenital": None,
        "mortuary": None,
        "critical_illness_death": None,
        "fertility": 20_000,
        "physiotherapy": 30_000,
    },
    "Silver": {
        "annual_cap": None,
        "inpatient": 1_000_000,
        "outpatient": 400_000,
        "surgical": 150_000,
        "maternity": 150_000,
        "dental": 15_000,
        "optical_total": 65_000,
        "cancer": 100_000,
        "chronic": 120_000,
        "hiv": 150_000,
        "dialysis": 70_000,
        "neonatal": 50_000,
        "congenital": None,
        "mortuary": 50_000,
        "critical_illness_death": 100_000,
        "fertility": 35_000,
        "physiotherapy": 40_000,
    },
    "Gold": {
        "annual_cap": None,
        "inpatient": 2_500_000,
        "outpatient": 1_100_000,
        "surgical": 500_000,
        "maternity": 250_000,
        "dental": 30_000,
        "optical_total": 95_000,
        "cancer": 200_000,
        "chronic": 200_000,
        "hiv": 350_000,
        "dialysis": 90_000,
        "neonatal": 150_000,
        "congenital": None,
        "mortuary": 100_000,
        "critical_illness_death": 200_000,
        "fertility": 50_000,
        "physiotherapy": 60_000,
    },
    "Platinum": {
        "annual_cap": None,
        "inpatient": 3_500_000,
        "outpatient": 1_500_000,
        "surgical": 1_000_000,
        "maternity": 500_000,
        "dental": 80_000,
        "optical_total": 135_000,
        "cancer": 500_000,
        "chronic": 300_000,
        "hiv": 500_000,
        "dialysis": 120_000,
        "neonatal": 500_000,
        "congenital": 250_000,
        "mortuary": 150_000,
        "critical_illness_death": 400_000,
        "fertility": 100_000,
        "physiotherapy": 100_000,
    },
}


def build_knowledge_base(today: str) -> str:
    return KNOWLEDGE_BASE_TEMPLATE.format(today=today)
