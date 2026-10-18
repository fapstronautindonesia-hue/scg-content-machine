BEGIN;

-- ============================================================
-- SCG CONTENT MACHINE V2.0.0
-- Additive / idempotent migration.
-- Existing V1 jobs, assets and analyses are preserved.
-- ============================================================

CREATE TABLE IF NOT EXISTS job_requests (
    job_id uuid PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    channel varchar(30) NOT NULL DEFAULT 'INSTAGRAM',
    instruction text NOT NULL DEFAULT '',
    intent jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS channel varchar(30) NOT NULL DEFAULT 'INSTAGRAM';
ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS instruction text NOT NULL DEFAULT '';
ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS intent jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    instruction text NOT NULL,
    requested_by text,
    status varchar(30) NOT NULL DEFAULT 'PENDING',
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz
);
ALTER TABLE revisions ADD COLUMN IF NOT EXISTS instruction text;
ALTER TABLE revisions ADD COLUMN IF NOT EXISTS requested_by text;
ALTER TABLE revisions ADD COLUMN IF NOT EXISTS status varchar(30) NOT NULL DEFAULT 'PENDING';
ALTER TABLE revisions ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE revisions ADD COLUMN IF NOT EXISTS applied_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_revisions_job_id ON revisions(job_id);

CREATE TABLE IF NOT EXISTS content_plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    revision_id uuid NULL,
    model varchar(100),
    plan jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(30) NOT NULL DEFAULT 'ACTIVE',
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS revision_id uuid NULL;
ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS model varchar(100);
ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS plan jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS status varchar(30) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_content_plans_job_id ON content_plans(job_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'content_plans_revision_id_fkey'
    ) THEN
        ALTER TABLE content_plans
        ADD CONSTRAINT content_plans_revision_id_fkey
        FOREIGN KEY (revision_id) REFERENCES revisions(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS renders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    content_plan_id uuid NOT NULL REFERENCES content_plans(id) ON DELETE CASCADE,
    output_type varchar(40) NOT NULL,
    version integer NOT NULL DEFAULT 1,
    storage_provider varchar(30),
    storage_key text,
    status varchar(30) NOT NULL DEFAULT 'PREVIEW_READY',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE renders ADD COLUMN IF NOT EXISTS content_plan_id uuid;
ALTER TABLE renders ADD COLUMN IF NOT EXISTS output_type varchar(40);
ALTER TABLE renders ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE renders ADD COLUMN IF NOT EXISTS storage_provider varchar(30);
ALTER TABLE renders ADD COLUMN IF NOT EXISTS storage_key text;
ALTER TABLE renders ADD COLUMN IF NOT EXISTS status varchar(30) NOT NULL DEFAULT 'PREVIEW_READY';
ALTER TABLE renders ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE renders ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_renders_job_id ON renders(job_id);
CREATE INDEX IF NOT EXISTS idx_renders_plan_id ON renders(content_plan_id);

CREATE TABLE IF NOT EXISTS captions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    content_plan_id uuid NOT NULL REFERENCES content_plans(id) ON DELETE CASCADE,
    channel varchar(30) NOT NULL,
    body text NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'DRAFT',
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE captions ADD COLUMN IF NOT EXISTS content_plan_id uuid;
ALTER TABLE captions ADD COLUMN IF NOT EXISTS channel varchar(30);
ALTER TABLE captions ADD COLUMN IF NOT EXISTS body text;
ALTER TABLE captions ADD COLUMN IF NOT EXISTS status varchar(30) NOT NULL DEFAULT 'DRAFT';
ALTER TABLE captions ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_captions_job_id ON captions(job_id);

CREATE TABLE IF NOT EXISTS usage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    operation varchar(50) NOT NULL,
    model varchar(100),
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    total_tokens bigint NOT NULL DEFAULT 0,
    estimated_cost numeric(18,6),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE usage ADD COLUMN IF NOT EXISTS operation varchar(50);
ALTER TABLE usage ADD COLUMN IF NOT EXISTS model varchar(100);
ALTER TABLE usage ADD COLUMN IF NOT EXISTS input_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS output_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS total_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS estimated_cost numeric(18,6);
ALTER TABLE usage ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE usage ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_usage_job_id ON usage(job_id);
CREATE INDEX IF NOT EXISTS idx_usage_created_at ON usage(created_at);

CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type varchar(60) NOT NULL,
    actor text NOT NULL DEFAULT 'system',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE events ADD COLUMN IF NOT EXISTS event_type varchar(60);
ALTER TABLE events ADD COLUMN IF NOT EXISTS actor text NOT NULL DEFAULT 'system';
ALTER TABLE events ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);

COMMIT;
