-- ============================================================
-- RiskLens v2 — Full Database Schema
-- Run this ONCE in the Supabase SQL Editor (Dashboard → SQL Editor)
-- ============================================================


-- ── SHARED TRIGGER FUNCTION ──────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;


-- ── 1. USERS ─────────────────────────────────────────────────
-- Extends Supabase Auth (auth.users). Never store passwords here.

CREATE TABLE IF NOT EXISTS public.users (
    id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email           TEXT UNIQUE NOT NULL,
    full_name       TEXT NOT NULL DEFAULT '',

    -- User's own AI provider (they bring their own key)
    ai_provider     TEXT CHECK (ai_provider IN ('claude', 'grok', 'gemini')),
    ai_api_key_enc  TEXT,        -- AES-256 encrypted — NEVER stored plain
    ai_model        TEXT,        -- e.g. 'claude-sonnet-4-6', 'gemini-2.0-flash'

    -- Timezone for per-user daily free-tier reset
    timezone        TEXT NOT NULL DEFAULT 'UTC',

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER users_updated_at
    BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── 2. SUBSCRIPTIONS ─────────────────────────────────────────
-- One row per user. Auto-created as 'free' on registration.

CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL UNIQUE
                                    REFERENCES public.users(id) ON DELETE CASCADE,
    plan                        TEXT NOT NULL DEFAULT 'free'
                                    CHECK (plan IN ('free', 'pro', 'business')),
    status                      TEXT NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'cancelled', 'expired', 'past_due')),

    -- Paystack identifiers (null until user upgrades)
    paystack_customer_id        TEXT,
    paystack_subscription_id    TEXT,
    paystack_subscription_code  TEXT,
    paystack_plan_code          TEXT,

    -- Billing window (null on free plan)
    current_period_start        TIMESTAMPTZ,
    current_period_end          TIMESTAMPTZ,

    -- Business plan only
    team_seats                  INTEGER NOT NULL DEFAULT 1,
    team_owner_id               UUID REFERENCES public.users(id) ON DELETE SET NULL,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER subscriptions_updated_at
    BEFORE UPDATE ON public.subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── 3. USAGE_LOGS ────────────────────────────────────────────
-- One row per tool run. Usage date is stored in user's timezone
-- so the daily free limit resets at midnight in their local time.

CREATE TABLE IF NOT EXISTS public.usage_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    tool_name   TEXT NOT NULL,
    ticker      TEXT,
    -- Date computed server-side as: (NOW() AT TIME ZONE user.timezone)::DATE
    usage_date  DATE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_user_date ON public.usage_logs (user_id, usage_date);
CREATE INDEX IF NOT EXISTS idx_usage_tool      ON public.usage_logs (tool_name);


-- ── 4. ANALYSES ──────────────────────────────────────────────
-- Full record of every tool run: raw structured output + AI explanation.

CREATE TABLE IF NOT EXISTS public.analyses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

    ticker              TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    tool_params         JSONB NOT NULL DEFAULT '{}',

    -- Raw structured output from the tool (can be large JSONB)
    tool_output         JSONB,

    -- AI natural language interpretation (populated in Phase 2)
    ai_interpretation   TEXT,
    ai_provider         TEXT,
    ai_model            TEXT,

    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    failure_reason      TEXT,
    elapsed_seconds     FLOAT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyses_user   ON public.analyses (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON public.analyses (ticker);
CREATE INDEX IF NOT EXISTS idx_analyses_tool   ON public.analyses (tool_name);
CREATE INDEX IF NOT EXISTS idx_analyses_status ON public.analyses (status);

CREATE TRIGGER analyses_updated_at
    BEFORE UPDATE ON public.analyses
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── 5. CONVERSATIONS ─────────────────────────────────────────
-- A chat thread. Can be linked to an analysis for context.

CREATE TABLE IF NOT EXISTS public.conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    analysis_id     UUID REFERENCES public.analyses(id) ON DELETE SET NULL,
    title           TEXT,
    ticker          TEXT,
    message_count   INTEGER NOT NULL DEFAULT 0,
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user     ON public.conversations (user_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_analysis ON public.conversations (analysis_id);

CREATE TRIGGER conversations_updated_at
    BEFORE UPDATE ON public.conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── 6. MESSAGES ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.messages (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id         UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    role                    TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content                 TEXT NOT NULL,
    triggered_analysis_id   UUID REFERENCES public.analyses(id) ON DELETE SET NULL,
    tool_name               TEXT,
    tool_params             JSONB DEFAULT '{}',
    token_count             INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON public.messages (conversation_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_messages_user ON public.messages (user_id);


-- ── 7. AUTO-UPDATE conversation counters on new message ──────

CREATE OR REPLACE FUNCTION sync_conversation_on_message()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.conversations
    SET message_count   = message_count + 1,
        last_message_at = NEW.created_at,
        updated_at      = NOW()
    WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER messages_sync_conversation
    AFTER INSERT ON public.messages
    FOR EACH ROW EXECUTE FUNCTION sync_conversation_on_message();


-- ── 8. AUTO-CREATE subscription on signup ────────────────────

CREATE OR REPLACE FUNCTION create_free_subscription()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.subscriptions (user_id, plan, status)
    VALUES (NEW.id, 'free', 'active')
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER users_create_subscription
    AFTER INSERT ON public.users
    FOR EACH ROW EXECUTE FUNCTION create_free_subscription();


-- ── 9. AUTO-CREATE public.users from auth.users ──────────────
-- When Supabase Auth creates a user, mirror the row here.
-- full_name comes from optional signup metadata.

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.users (id, email, full_name)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', '')
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();


-- ── 10. ROW LEVEL SECURITY ───────────────────────────────────

ALTER TABLE public.users          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscriptions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_logs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analyses       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages       ENABLE ROW LEVEL SECURITY;

-- Users
CREATE POLICY "users_select_own"
    ON public.users FOR SELECT USING (auth.uid() = id);
CREATE POLICY "users_update_own"
    ON public.users FOR UPDATE USING (auth.uid() = id);

-- Subscriptions
CREATE POLICY "subscriptions_select_own"
    ON public.subscriptions FOR SELECT USING (auth.uid() = user_id);

-- Usage logs
CREATE POLICY "usage_select_own"
    ON public.usage_logs FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "usage_insert_own"
    ON public.usage_logs FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Analyses
CREATE POLICY "analyses_select_own"
    ON public.analyses FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "analyses_insert_own"
    ON public.analyses FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "analyses_update_own"
    ON public.analyses FOR UPDATE USING (auth.uid() = user_id);

-- Conversations
CREATE POLICY "conversations_all_own"
    ON public.conversations FOR ALL USING (auth.uid() = user_id);

-- Messages
CREATE POLICY "messages_all_own"
    ON public.messages FOR ALL USING (auth.uid() = user_id);
