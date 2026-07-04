from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime
import uuid


# ── Auth schemas ────────────────────────────────────────────────────────────
class UserRegister(BaseModel):
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserInfo(BaseModel):
    id: uuid.UUID
    email: EmailStr
    plan: str
    analyses_used: int
    plan_limit: int
    days_remaining: int
    ai_provider: Optional[str] = None

class ConnectAI(BaseModel):
    provider: str
    api_key: str


# ── Conversation / Message schemas ──────────────────────────────────────────
class ConversationCreate(BaseModel):
    title: str

class Conversation(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    class Config:
        from_attributes = True

class MessageCreate(BaseModel):
    conversation_id: uuid.UUID
    content: str

class Message(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    content: str
    tool_used: Optional[str] = None
    ticker: Optional[str] = None
    created_at: datetime
    class Config:
        from_attributes = True


# ── Usage schemas ────────────────────────────────────────────────────────────
class UsageInfo(BaseModel):
    plan: str
    analyses_used: int
    limit: int
    days_remaining: int


# ── Notification schemas ─────────────────────────────────────────────────────
class Notification(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    message: str
    read_status: bool = False
    created_at: datetime
    class Config:
        from_attributes = True


# ── Tool output schemas (used by executive_report.py and other tools) ────────
class FilingMetaOut(BaseModel):
    ticker: str
    form_type: str
    filing_date: Optional[str] = None
    accession_number: Optional[str] = None

class SignalHitOut(BaseModel):
    signal: str
    context: Optional[str] = None
    tier: Optional[int] = None

class SectionScoreOut(BaseModel):
    materiality: Any
    raw_score: float = 0.0
    tier1_hits: List[SignalHitOut] = []
    tier2_hits: List[SignalHitOut] = []
    new_signals: List[SignalHitOut] = []
    removed_signals: List[SignalHitOut] = []

class ScoringOut(BaseModel):
    overall_materiality: Any
    risk_factors: SectionScoreOut
    mda: SectionScoreOut
    top_signals: List[SignalHitOut] = []

class SectionDeltaOut(BaseModel):
    delta_success: bool = False
    magnitude: Any = None
    pct_changed: float = 0.0

class DeltaOut(BaseModel):
    risk_factors: SectionDeltaOut = SectionDeltaOut()
    mda: SectionDeltaOut = SectionDeltaOut()

class ExtractionOut(BaseModel):
    risk_factors: Any = None
    mda: Any = None

class FinancialContextOut(BaseModel):
    fetch_success: bool = False
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    total_debt: Optional[float] = None
    current_ratio: Optional[float] = None
    capex: Optional[float] = None

class ExecutiveReportOutput(BaseModel):
    ticker: str
    form_type: str
    pipeline_success: bool = False
    failure_reason: Optional[str] = None
    report: Optional[str] = None
    filing_date: Optional[str] = None
    overall_materiality: Optional[str] = None
    top_signals: List[Any] = []
    elapsed_seconds: float = 0.0