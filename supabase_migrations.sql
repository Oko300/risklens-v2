-- Table 1: user_plans
CREATE TABLE public.user_plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES auth.users UNIQUE,
    plan text DEFAULT 'free_trial',
    analyses_used integer DEFAULT 0,
    period_start timestamptz DEFAULT now(),
    period_end timestamptz DEFAULT (now() + interval '30 days'),
    created_at timestamptz DEFAULT now()
);

ALTER TABLE public.user_plans ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own user plans." ON public.user_plans
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own user plans." ON public.user_plans
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own user plans." ON public.user_plans
  FOR UPDATE USING (auth.uid() = user_id);

-- Table 2: conversations
CREATE TABLE public.conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES auth.users,
    title text,
    created_at timestamptz DEFAULT now()
);

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own conversations." ON public.conversations
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own conversations." ON public.conversations
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own conversations." ON public.conversations
  FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own conversations." ON public.conversations
  FOR DELETE USING (auth.uid() = user_id);

-- Table 3: messages
CREATE TABLE public.messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid REFERENCES public.conversations(id) ON DELETE CASCADE,
    user_id uuid REFERENCES auth.users,
    role text,
    content text,
    tool_used text,
    ticker text,
    created_at timestamptz DEFAULT now()
);

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own messages." ON public.messages
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own messages." ON public.messages
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own messages." ON public.messages
  FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own messages." ON public.messages
  FOR DELETE USING (auth.uid() = user_id);