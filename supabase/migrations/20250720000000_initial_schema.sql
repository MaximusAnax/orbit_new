-- Orbit initial schema (mirrors orbit-backend/app/db/schema.sql)

create extension if not exists vector;
create extension if not exists pgcrypto;

create table person (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  preferred_name text,
  photo_url text,
  pronunciation_note text,
  orbit text check (orbit in ('inner','close','active','extended','outer')),
  linked_phone_contact_id text,
  created_at timestamptz not null default now()
);

create table contact_point (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  type text not null check (type in ('phone','email','instagram','linkedin','x','website','other')),
  value text not null,
  is_primary boolean not null default false
);

create table event (
  id uuid primary key default gen_random_uuid(),
  date timestamptz not null,
  location text,
  raw_transcript text,
  summary text,
  title text,
  source text not null check (source in ('voice','text','manual')),
  status text not null default 'captured' check (status in ('captured','confirmed','synced')),
  audio_storage_path text,
  created_at timestamptz not null default now()
);

create table event_participant (
  event_id uuid not null references event(id) on delete cascade,
  person_id uuid not null references person(id) on delete cascade,
  role text not null check (role in ('attendee','introducer','introduced','mentioned_not_present')),
  per_person_notes text,
  primary key (event_id, person_id)
);

create table organization (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  type text check (type in ('company','school','other')),
  created_at timestamptz not null default now()
);

create table fact (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  category text not null check (category in ('job','location','school','interest','goal','family','other')),
  value text not null,
  organization_id uuid references organization(id),
  status text not null check (status in ('current','past','uncertain')),
  valid_from timestamptz,
  valid_to timestamptz,
  source_event_id uuid references event(id),
  confidence real,
  created_at timestamptz not null default now()
);
create index fact_person_category_idx on fact(person_id, category, status);

create table profile_update_proposal (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references event(id) on delete cascade,
  person_id uuid not null references person(id) on delete cascade,
  proposed_fact_json jsonb not null,
  decision text not null default 'pending' check (decision in ('pending','accepted','edited','rejected','deferred')),
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table relationship_state (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  description text,
  desired_closeness text,
  desired_cadence text,
  direction text,
  source text not null check (source in ('explicit','inferred_suggested')),
  recorded_at timestamptz not null default now(),
  superseded_at timestamptz
);
create index relationship_state_current_idx on relationship_state(person_id) where superseded_at is null;

create table task (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  event_id uuid references event(id),
  description text not null,
  status text not null default 'open' check (status in ('open','done')),
  due_date date,
  created_at timestamptz not null default now()
);

create table ambiguity_flag (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references event(id) on delete cascade,
  raw_fragment text not null,
  candidate_person_ids uuid[] not null,
  resolved_person_id uuid references person(id),
  created_at timestamptz not null default now()
);

create table embedding (
  id uuid primary key default gen_random_uuid(),
  source_type text not null check (source_type in ('fact','event','person_summary')),
  source_id uuid not null,
  vector vector(1536) not null,
  text_content text not null,
  model_version text not null,
  created_at timestamptz not null default now()
);
create index embedding_vector_idx on embedding using ivfflat (vector vector_cosine_ops) with (lists = 100);

create table pipeline_debug_log (
  id uuid primary key default gen_random_uuid(),
  event_id uuid references event(id) on delete cascade,
  step_name text not null,
  raw_input jsonb,
  raw_output jsonb,
  created_at timestamptz not null default now()
);

alter table person enable row level security;
alter table contact_point enable row level security;
alter table event enable row level security;
alter table event_participant enable row level security;
alter table organization enable row level security;
alter table fact enable row level security;
alter table profile_update_proposal enable row level security;
alter table relationship_state enable row level security;
alter table task enable row level security;
alter table ambiguity_flag enable row level security;
alter table embedding enable row level security;

create policy "authenticated_read_person" on person for select to authenticated using (true);
create policy "authenticated_read_event" on event for select to authenticated using (true);
create policy "authenticated_read_proposal" on profile_update_proposal for select to authenticated using (true);
