"""Pydantic schemas for auth module."""

from datetime import date
from pydantic import BaseModel


# ─────────────────────────────────────────────
# Auth Schemas
# ─────────────────────────────────────────────

class RegisterPayload(BaseModel):
    invite_token: str
    email: str
    name: str
    password: str


class LoginPayload(BaseModel):
    email: str
    password: str


class TeamInvitePayload(BaseModel):
    email: str


class CreateOrgPayload(BaseModel):
    org_name: str
    admin_email: str


# ─────────────────────────────────────────────
# API Key Schemas
# ─────────────────────────────────────────────

class GenerateKeyPayload(BaseModel):
    name: str | None = None


# ─────────────────────────────────────────────
# Gmail Integration Schemas
# ─────────────────────────────────────────────

class GmailDisconnectPayload(BaseModel):
    connection_id: int | None = None
    email: str | None = None


class GmailWatchStartPayload(BaseModel):
    connection_id: int | None = None
    org_id: int | None = None


# ─────────────────────────────────────────────
# Preauth Schemas
# ─────────────────────────────────────────────

class AuditEventPayload(BaseModel):
    event_type: str
    target_kind: str | None = None
    target_id: str | None = None
    metadata: dict | None = None


class RetryPreauthPayload(BaseModel):
    request_id: str
    org_id: int | None = None


class RetryPendingPreauthPayload(BaseModel):
    org_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None
    limit: int = 20


class SendPreauthDecisionPayload(BaseModel):
    request_id: str
    org_id: int | None = None


class AddPACommentPayload(BaseModel):
    request_id: str
    comment_text: str
    org_id: int | None = None


# ─────────────────────────────────────────────
# QA / Mismatch Review Schemas
# ─────────────────────────────────────────────

class CreateMismatchReviewPayload(BaseModel):
    request_id: str
    checkin_id: str | None = None
    mismatch_type: str
    cause_category: str
    agent_decision: str | None = None
    agent_amount: float | None = None
    aman_decision: str | None = None
    aman_amount: float | None = None
    notes: str | None = None
    follow_up_action: str | None = None


class UpdateMismatchReviewPayload(BaseModel):
    cause_category: str | None = None
    notes: str | None = None
    follow_up_action: str | None = None
    fix_status: str | None = None


# ─────────────────────────────────────────────
# Onboarding / Org Management Schemas
# ─────────────────────────────────────────────

class UpdateOrgPayload(BaseModel):
    name: str | None = None
    is_active: bool | None = None
