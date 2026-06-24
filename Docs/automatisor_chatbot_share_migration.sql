-- Chat sharing support for automatisor_chatbot
-- Run in Supabase SQL editor before deploying chat share.

alter table public.automatisor_chatbot
  alter column customer_id drop not null;

alter table public.automatisor_chatbot
  add column if not exists shared_by_customer_id uuid null
    references public.automatisor_customer (customer_id) on delete set null,
  add column if not exists source_session_id uuid null
    references public.automatisor_chatbot (session_id) on delete set null,
  add column if not exists shared_at timestamptz null,
  add column if not exists pending_recipient_email text null;

create index if not exists idx_automatisor_chatbot_pending_recipient
  on public.automatisor_chatbot (pending_recipient_email)
  where customer_id is null and pending_recipient_email is not null;

create index if not exists idx_automatisor_chatbot_shared_source
  on public.automatisor_chatbot (source_session_id)
  where source_session_id is not null;
