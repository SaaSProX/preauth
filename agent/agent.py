import json
import logging
from datetime import date
from anthropic import AsyncAnthropic
from config.settings import settings
from services.db import pg_execute, pg_query_one

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# KNOWLEDGE BASE — extracted from Aman HMO 2026 Retail Prices PDF
# ---------------------------------------------------------------------------
KNOWLEDGE_BASE = """
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
MATERNITY & NEONATAL SERVICES (all plans)
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
5. AGE LIMIT: Principal must be 65 or under. Above 65 → must be on Senior Citizens Plan, not standard plans.
6. OPTICAL LENSES: Once every 2 years only.
7. HEALTH CHECKS: Only at designated centers during institutions' health week. Non-refundable otherwise.
8. GYM/SPA: Principal only.
9. NEWBORN REGISTRATION: Newborns not registered within 6 weeks of birth are excluded.
10. ROOM TYPE: Bronze=General Ward, Silver=Semi-Private, Gold/Platinum/Platinum Plus=Private. Upgrades not covered.
11. NEONATAL BENEFIT: Drawn from nursing mother's limit (live birth only).

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
""".format(today=TODAY)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
async def _call_claude(system_prompt: str, user_message: str) -> dict:
    """Call Claude API and return parsed JSON response."""
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _parse_json_field(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _log_agent(request_id: str, agent_num: int, agent_name: str, result: dict):
    """Save individual agent result to agent_logs table and log to console."""
    passed = result.get("pass", result.get("decision") == "APPROVE")
    status = "pass" if passed else "fail"
    logger.info(f"[Agent {agent_num} - {agent_name}] status={status} | {json.dumps(result)}")
    await pg_execute(
        """
        INSERT INTO agent_logs (request_id, agent_num, agent_name, status, result, logged_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
        """,
        str(request_id),
        agent_num,
        agent_name,
        status,
        json.dumps(result)
    )


# ---------------------------------------------------------------------------
# AGENT 1 — Eligibility
# ---------------------------------------------------------------------------
async def agent_eligibility(pa: dict) -> dict:
    system_prompt = """You are Agent 1 of a pre-authorization pipeline for Aman HMO.
Your ONLY job is to check member eligibility.
Return ONLY valid JSON. No markdown, no explanation outside the JSON."""

    user_message = f"""Check eligibility for this pre-authorization request.
Today's date is {TODAY}.

PA REQUEST:
{json.dumps(pa, indent=2)}

Rules:
1. If plan is exactly "Platinum Plus" → auto-pass, set is_platinum_plus: true
2. eligibility.status must be "active" (case-insensitive)
3. eligibility.enrollment_date must be on or before today ({TODAY})
4. eligibility.expiry_date must be strictly after today ({TODAY})
5. If member age is provided and exceeds 65 → fail (must be on Senior Citizens Plan)
6. Any failure → pass: false with a specific reason

Return ONLY this JSON:
{{
  "pass": true or false,
  "reason": "one sentence explanation",
  "is_platinum_plus": true or false,
  "checks": {{
    "status_active": true or false,
    "enrollment_valid": true or false,
    "not_expired": true or false,
    "age_ok": true or false or null
  }}
}}"""

    return await _call_claude(system_prompt, user_message)


# ---------------------------------------------------------------------------
# AGENT 2 — Plan & Coverage
# ---------------------------------------------------------------------------
async def agent_plan_coverage(pa: dict) -> dict:
    system_prompt = f"""You are Agent 2 of a pre-authorization pipeline for Aman HMO.
Your ONLY job is to check whether requested items are covered under the member's plan tier.
Use the knowledge base below. Return ONLY valid JSON. No markdown.

KNOWLEDGE BASE:
{KNOWLEDGE_BASE}"""

    user_message = f"""Check plan coverage for this pre-authorization request.
Today's date is {TODAY}.

PA REQUEST:
{json.dumps(pa, indent=2)}

Check in this order:
1. EXCLUSION CHECK: Is any item in the exclusions list? → fail immediately if yes
2. WAITING PERIOD CHECK:
   - chronic_disease_waiting_cleared false + chronic disease item → fail
   - maternity_waiting_cleared false + pregnancy/delivery item → fail
   - surgical_waiting_cleared false + non-accidental surgery item → fail
3. PLAN COVERAGE CHECK: Is the item covered on this specific plan tier?
4. BENEFIT CATEGORY: Classify into the correct bucket

Production payload guidance:
- If items have pricing_source="tariff", treat them as recognized Aman tariff items.
- If proposed_impact.status is "allowed" and proposed_impact.violations is empty, do not deny solely because the exact tariff item name is not listed in the knowledge base.
- For basic outpatient consultation, routine laboratory tests, and routine medication on tariff, pass coverage unless there is a clear exclusion, waiting-period issue, or explicit violation.
- If coverage is uncertain, prefer pass with a note or escalate in the final decision rather than deterministic denial.

Return ONLY this JSON:
{{
  "pass": true or false,
  "reason": "one sentence explanation",
  "benefit_category": "exact bucket name (e.g. Intermediate Surgery / Major Surgery / Minor Surgery / Inpatient / Outpatient / Dental / Optical / Cancer Care / Chronic Disease Medication / Physiotherapy / Psychiatric Care / Maternity / Neonatal / Kidney Dialysis / HIV/AIDS Care / CT/MRI Scan / Endoscopy / Immunization / Emergency)",
  "covered_items": ["item descriptions that are covered"],
  "denied_items": ["item description — reason for denial"],
  "exclusion_triggered": true or false,
  "exclusion_detail": "which exclusion rule, or null",
  "waiting_period_issue": true or false,
  "waiting_period_detail": "which waiting period applies, or null",
  "plan_restriction": true or false,
  "plan_restriction_detail": "which plan restriction applies, or null"
}}"""

    return await _call_claude(system_prompt, user_message)


# ---------------------------------------------------------------------------
# AGENT 3 — Utilization & Limits
# ---------------------------------------------------------------------------
async def agent_utilization(pa: dict, benefit_category: str) -> dict:
    system_prompt = f"""You are Agent 3 of a pre-authorization pipeline for Aman HMO.
Your ONLY job is to check utilization limits and remaining balances.
Use the knowledge base below. Return ONLY valid JSON. No markdown.

KNOWLEDGE BASE:
{KNOWLEDGE_BASE}"""

    user_message = f"""Check utilization and limits for this pre-authorization request.
Benefit category from Agent 2: {benefit_category}

PA REQUEST:
{json.dumps(pa, indent=2)}

Rules:
1. Identify the correct limit bucket based on benefit_category and plan tier
2. Use utilization data if present; if missing, use knowledge base limits and assume 0 used
3. For amount-based benefits, check: bucket_used + estimated_cost <= bucket_limit.
   For multi-item requests, estimated_cost means total_requested_cost if present, otherwise sum every item estimated_cost/requested_cost.
4. For frequency-based benefits such as CT/MRI scan counts, check: count_used + 1 <= count_limit.
   Do not compare estimated_cost against a frequency count.
4. Check: maximum_annual_benefit_used + estimated_cost <= maximum_annual_benefit_limit
5. Both checks must pass
6. If bucket_exceeded is false and annual_cap_exceeded is false, pass MUST be true.
7. Note: Surgical limit covers all surgery types (minor, intermediate, major)

Return ONLY this JSON:
{{
  "pass": true or false,
  "reason": "one sentence explanation",
  "bucket": "exact bucket name",
  "bucket_limit": number,
  "bucket_used": number,
  "estimated_cost": number,
  "bucket_remaining_before": number,
  "bucket_remaining_after": number,
  "annual_cap_limit": number,
  "annual_cap_used": number,
  "annual_cap_remaining_before": number,
  "annual_cap_remaining_after": number,
  "bucket_exceeded": true or false,
  "annual_cap_exceeded": true or false,
  "utilization_data_missing": true or false
}}"""

    result = await _call_claude(system_prompt, user_message)
    return _normalize_utilization_result(result)


# ---------------------------------------------------------------------------
# AGENT 4 — Final Decision
# ---------------------------------------------------------------------------
async def agent_final_decision(pa: dict, agent1: dict, agent2: dict, agent3: dict) -> dict:
    system_prompt = """You are Agent 4 of a pre-authorization pipeline for Aman HMO.
Aggregate findings from Agents 1, 2, and 3 and produce the final decision.
Return ONLY valid JSON. No markdown."""

    user_message = f"""Make the final pre-authorization decision.

ORIGINAL PA REQUEST:
{json.dumps(pa, indent=2)}

AGENT 1 — ELIGIBILITY:
{json.dumps(agent1, indent=2)}

AGENT 2 — PLAN & COVERAGE:
{json.dumps(agent2, indent=2)}

AGENT 3 — UTILIZATION:
{json.dumps(agent3, indent=2)}

Decision rules:
- APPROVE: all 3 agents passed
- DENY: any agent failed with a clear deterministic reason
- ESCALATE: incomplete data, conflicting results, edge case, or low confidence
- Platinum Plus (agent1.is_platinum_plus = true) → always APPROVE

Return ONLY this JSON:
{{
  "decision": "APPROVE" or "DENY" or "ESCALATE",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "amount_approved": number or null,
  "denial_reason": "specific reason or null",
  "escalation_reason": "specific reason or null",
  "reasoning": "one clear sentence summarizing the decision",
  "flags": ["notable flags or empty array"],
  "no_preauth_required": true or false,
  "agent_summary": {{
    "agent1_pass": true or false,
    "agent2_pass": true or false,
    "agent3_pass": true or false
  }}
}}"""

    return await _call_claude(system_prompt, user_message)


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------
async def run(patient_id: str, request_id: str):
    logger.info(f"[Agent] ── START ── request_id={request_id} patient_id={patient_id}")

    row = await pg_query_one(
        "SELECT extracted_fields FROM preauth_logs WHERE request_id = $1",
        str(request_id)
    )
    if not row:
        logger.error(f"[Agent] No record found for request_id={request_id}")
        return

    pa = _parse_json_field(row["extracted_fields"])

    try:
        # ── Agent 1: Eligibility ──────────────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET status = 'processing', agent_step = 'eligibility' WHERE request_id = $1",
            str(request_id)
        )
        result_1 = await agent_eligibility(pa)
        await _log_agent(request_id, 1, "Eligibility", result_1)

        if not result_1.get("pass"):
            await _save_decision(request_id, "DENY", {
                "agent1": result_1, "agent2": None, "agent3": None, "agent4": None,
                "decision": "DENY", "confidence": "HIGH", "amount_approved": None,
                "denial_reason": result_1.get("reason"),
                "reasoning": result_1.get("reason"),
                "flags": ["Failed eligibility check"],
                "no_preauth_required": False,
                "agent_summary": {"agent1_pass": False, "agent2_pass": None, "agent3_pass": None}
            })
            return

        if result_1.get("is_platinum_plus"):
            logger.info(f"[Agent] Platinum Plus express — auto-approving request_id={request_id}")
            await _save_decision(request_id, "APPROVE", {
                "agent1": result_1, "agent2": None, "agent3": None, "agent4": None,
                "decision": "APPROVE", "confidence": "HIGH",
                "amount_approved": _get_estimated_cost(pa),
                "denial_reason": None, "escalation_reason": None,
                "reasoning": "Platinum Plus express card — no pre-authorization required.",
                "flags": ["platinum_plus_express_card"],
                "no_preauth_required": True,
                "agent_summary": {"agent1_pass": True, "agent2_pass": None, "agent3_pass": None}
            })
            return

        # ── Agent 2: Plan & Coverage ──────────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET agent_step = 'coverage' WHERE request_id = $1",
            str(request_id)
        )
        result_2 = await agent_plan_coverage(pa)
        await _log_agent(request_id, 2, "Plan & Coverage", result_2)

        if not result_2.get("pass"):
            await _save_decision(request_id, "DENY", {
                "agent1": result_1, "agent2": result_2, "agent3": None, "agent4": None,
                "decision": "DENY", "confidence": "HIGH", "amount_approved": None,
                "denial_reason": result_2.get("reason"),
                "reasoning": result_2.get("reason"),
                "flags": result_2.get("denied_items", []),
                "no_preauth_required": False,
                "agent_summary": {"agent1_pass": True, "agent2_pass": False, "agent3_pass": None}
            })
            return

        # ── Agent 3: Utilization & Limits ─────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET agent_step = 'utilization' WHERE request_id = $1",
            str(request_id)
        )
        benefit_category = result_2.get("benefit_category", "unknown")

        # If consumption / YTD usage is missing from the payload, we cannot
        # honestly verify limits. Escalate for human review instead of
        # silently assuming zero usage.
        util_data = pa.get("utilization") if isinstance(pa.get("utilization"), dict) else {}
        if util_data.get("utilization_data_missing"):
            skip_result = {
                "pass": None,
                "reason": "Cannot verify limits — consumption data (enrollee_limits/policy_limits) is missing from the payload. Escalated for human review.",
                "utilization_data_missing": True,
                "benefit_category": benefit_category,
            }
            await _log_agent(request_id, 3, "Utilization & Limits", skip_result)
            await _save_decision(request_id, "ESCALATE", {
                "agent1": result_1, "agent2": result_2, "agent3": skip_result, "agent4": None,
                "decision": "ESCALATE", "confidence": "MEDIUM", "amount_approved": None,
                "escalation_reason": "Consumption data missing — cannot verify limits",
                "reasoning": "Cannot verify limits because the inbound payload does not include enrollee or policy consumption snapshots. Escalated for human review.",
                "flags": ["Consumption data missing"],
                "no_preauth_required": False,
                "agent_summary": {"agent1_pass": True, "agent2_pass": True, "agent3_pass": None},
            })
            return

        result_3 = await agent_utilization(pa, benefit_category)
        await _log_agent(request_id, 3, "Utilization & Limits", result_3)

        if not result_3.get("pass"):
            await _save_decision(request_id, "DENY", {
                "agent1": result_1, "agent2": result_2, "agent3": result_3, "agent4": None,
                "decision": "DENY", "confidence": "HIGH", "amount_approved": None,
                "denial_reason": result_3.get("reason"),
                "reasoning": result_3.get("reason"),
                "flags": ["Benefit limit exceeded"],
                "no_preauth_required": False,
                "agent_summary": {"agent1_pass": True, "agent2_pass": True, "agent3_pass": False}
            })
            return

        # ── Agent 4: Final Decision ───────────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET agent_step = 'decision' WHERE request_id = $1",
            str(request_id)
        )
        result_4 = await agent_final_decision(pa, result_1, result_2, result_3)
        await _log_agent(request_id, 4, "Final Decision", result_4)

        await _save_decision(request_id, result_4.get("decision", "ESCALATE"), {
            "agent1": result_1, "agent2": result_2, "agent3": result_3, "agent4": result_4,
            **result_4
        })

    except Exception as e:
        logger.exception(f"[Agent] ERROR request_id={request_id}: {e}")
        await pg_execute(
            "UPDATE preauth_logs SET status = 'error', error_message = $2 WHERE request_id = $1",
            str(request_id), str(e)
        )


async def _save_decision(request_id: str, decision: str, result: dict):
    status = decision.lower()
    await pg_execute(
        """
        UPDATE preauth_logs
        SET status       = $2,
            agent_step   = 'completed',
            decision     = $3,
            agent_result = $4::jsonb,
            processed_at = NOW()
        WHERE request_id = $1
        """,
        str(request_id), status, decision, json.dumps(result)
    )
    logger.info(f"[Agent] ── END ── request_id={request_id} decision={decision}")

    # Send the decision back to Aman (advisory callback — integration direction (ii)).
    # Imported lazily to avoid any startup-time coupling.
    try:
        from services.aman_callback import send_decision_to_aman
        await send_decision_to_aman(str(request_id))
    except Exception:
        logger.exception("[Agent] Aman callback failed (logged, not raised)")


def _get_estimated_cost(pa: dict) -> float | None:
    if not isinstance(pa, dict):
        return None

    total_requested_cost = pa.get("total_requested_cost")
    if isinstance(total_requested_cost, (int, float)):
        return total_requested_cost

    items = _parse_json_field(pa.get("items") or [])
    if isinstance(items, list) and items:
        total = 0
        has_cost = False
        for item in items:
            if not isinstance(item, dict):
                continue
            cost = item.get("estimated_cost") or item.get("requested_cost") or item.get("amount")
            try:
                total += float(cost)
                has_cost = True
            except (TypeError, ValueError):
                continue
        if has_cost:
            return total
    return None


def _normalize_utilization_result(result: dict) -> dict:
    normalized = dict(result)
    bucket_exceeded = normalized.get("bucket_exceeded") is True
    annual_cap_exceeded = normalized.get("annual_cap_exceeded") is True

    if bucket_exceeded or annual_cap_exceeded:
        normalized["pass"] = False
        if not normalized.get("reason"):
            normalized["reason"] = "Requested service exceeds the identified benefit or annual cap limit."
        return normalized

    if normalized.get("pass") is False:
        normalized["pass"] = True
        normalized["reason"] = "Utilization is within the identified benefit bucket and annual cap limits."
        normalized["normalization_note"] = (
            "Corrected contradictory utilization output where no exceeded limit was reported."
        )

    return normalized
