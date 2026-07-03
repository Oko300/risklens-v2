CREATE TABLE IF NOT EXISTS public.user_ai_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE UNIQUE,
  provider text NOT NULL,
  api_key text NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

ALTER TABLE public.user_ai_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own AI keys" ON public.user_ai_keys
  FOR ALL USING (auth.uid() = user_id);