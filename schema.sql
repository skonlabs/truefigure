-- =============================================================================
-- TrueFigure SDK - PostgreSQL schema v2.4 (column-coverage audit: complete wire-name map; superseded_by direction + scheduler stamp documented)
-- (v2.3: G6 - deployment_service_users links incumbent-system service accounts
--  operated by the measured vendor's product to its deployment, so customer-
--  origin events acted by those accounts corroborate the vendor's autonomous-
--  action claims. 31 tables.)
-- (v2.2: rejected_events.import_job_id -> composite tenant FK (was the last
--  representable cross-tenant reference); api_key revoked-state pairing check;
--  gauge non-negativity; seat_classification_thresholds one-version-per-
--  effective-date; import-job partial indexes; alerts.acked_by documented as
--  deliberately plain FK)
-- (v2.1: qa_label_definitions -> qa_label_definitions; identifier_namespaces ->
--  identifier_namespaces with enum identifier_field_type; rejected_events
--  separation rationale documented in its table comment)
-- v2.0 review round: organizations table (the company/tenant commercial entity;
-- workspaces belong to organizations); grader_status values lowercased (bug);
-- user_status +blocked, deployment_status +paused; users.role -> job_role;
-- import_jobs.expected_events NOT NULL DEFAULT 0 (0 = not declared); period
-- columns grammar-constrained by CHECK; column ordering convention (ids, then
-- enums, then refs/data, audit last); table renames for clarity:
--   imports -> import_jobs            event_rejections -> rejected_events
--   change_events -> change_log       change_event_deployments -> change_log_deployments
--   meter_rollups -> meter_usage_daily  ingest_stats_daily -> ingestion_stats_daily
--   classification_threshold_versions -> seat_classification_thresholds
--   deployment_license_users -> deployment_license_identifiers
--
-- Conventions (all normative):
--   * snake_case; plural table names; surrogate PK "id BIGINT GENERATED ALWAYS
--     AS IDENTITY"; wire-visible ids as *_ref TEXT UNIQUE
--   * column order: id, FK ids, enum columns, refs/business columns, audit last
--   * enum-column match rule: every enum-typed column is named identically to
--     its enum type; shared vocabularies use the shared name everywhere
--     (CI check: information_schema query at the bottom of this file)
--   * enum casing: ALL enum values lowercase snake_case (wire = storage = client)
--   * multi-tenant integrity: composite FKs onto (id, workspace_id|deployment_id)
--     pairs make cross-tenant references unrepresentable; audit FKs are plain
--     by design (platform system user writes everywhere); read isolation is
--     app-enforced workspace scoping (RLS optional hardening)
--   * every table: created_dt/created_by/updated_dt/updated_by (BIGINT FK users)
--   * immutable stores (events, parameter_sets, figures, mapping_contracts,
--     reports) are append-only; pipeline-mutable columns explicitly whitelisted
--   * COMPLETE wire-name map (anything not listed is same-named or internal):
--       roster kind        -> users.user_type
--       roster role        -> users.job_role
--       imports kind       -> import_jobs.import_type
--       deployments type   -> deployments.deployment_type
--       deployments mode   -> deployments.deployment_mode
--       change-events type -> change_log.change_type
--       change-events supersedes (ref of the corrected entry)
--                          -> sets change_log.superseded_by_id ON THE OLD ROW
--       license period_unit-> deployment_licenses.period_unit_type
--       webhooks events[]  -> webhooks.webhook_event_type
--       lifecycle size_band-> work_items.size_type
--       envelope origin    -> events.origin_type
--       api key mode       -> api_keys.api_key_mode
--   * (superseded line: role->job_role,
--     size_band->size_type, origin->origin_type, mode->api_key_mode/deployment_mode
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- enumerated types ------------------------------------------------
CREATE TYPE org_status         AS ENUM ('pending','active','suspended','closed');
CREATE TYPE plan_type          AS ENUM ('free','team','organization','enterprise','vendor');
CREATE TYPE environment_type   AS ENUM ('production','sandbox');
CREATE TYPE workspace_status   AS ENUM ('active','suspended','closed');
CREATE TYPE api_key_mode       AS ENUM ('production','test');
CREATE TYPE api_key_scope      AS ENUM ('workspace','deployment');
CREATE TYPE api_key_status     AS ENUM ('active','revoked');
CREATE TYPE record_status      AS ENUM ('active','retired');
CREATE TYPE deployment_type    AS ENUM ('saas_tool','in_house','agent','decision_ai');
CREATE TYPE deployment_mode    AS ENUM ('planned','measured');
CREATE TYPE deployment_status  AS ENUM ('active','paused','archived');
CREATE TYPE period_unit_type   AS ENUM ('month','year');
CREATE TYPE user_type          AS ENUM ('user','shared','service','bot');
CREATE TYPE user_status        AS ENUM ('active','inactive','blocked');
CREATE TYPE identifier_field_type AS ENUM ('user_ref','work_item_id','assignee_ref');
CREATE TYPE grader_status      AS ENUM ('uncalibrated','active','deactivated_drift');
CREATE TYPE webhook_status     AS ENUM ('unverified','verified','suspended','deleted');
CREATE TYPE webhook_event_type AS ENUM ('figure.updated','refusal.lifted','alert.raised','report.issued');
CREATE TYPE delivery_status    AS ENUM ('retrying','delivered','dropped');
CREATE TYPE event_type         AS ENUM ('activity','lifecycle','cost_meter','quality_signal','revenue_signal');
CREATE TYPE change_type        AS ENUM ('system_cutover','process_change','seasonal','staffing','regulatory','tool_change','other');
CREATE TYPE sided_type         AS ENUM ('symmetric','one_sided');
CREATE TYPE import_type        AS ENUM ('backfill','publication','migration','other');
CREATE TYPE import_status      AS ENUM ('queued','processing','completed','completed_with_rejects','failed');
CREATE TYPE origin_type        AS ENUM ('customer_system','vendor_product');
CREATE TYPE pipeline_status    AS ENUM ('accepted','resolved','joined','excluded');
CREATE TYPE exclusion_reason   AS ENUM ('shared_account','service_account','bot_account','unresolved_identity');
CREATE TYPE meter_type         AS ENUM ('tokens_in','tokens_out','api_calls','compute_seconds','seats_active','storage_gb');
CREATE TYPE size_type          AS ENUM ('xs','s','m','l','xl');
CREATE TYPE figure_status      AS ENUM ('computed','refused','awaiting_parameters');
CREATE TYPE grade_type         AS ENUM ('estimate','measured','verified');
CREATE TYPE value_class_type   AS ENUM ('realized','capacity');
CREATE TYPE alert_code_type    AS ENUM ('join_rate_drop','resolution_rate_drop','event_silence','counter_monotonic_suspect','clock_skew_suspect','license_undeclared');
CREATE TYPE alert_status       AS ENUM ('open','acked','resolved');
CREATE TYPE attribution_mode   AS ENUM ('single','joint','separable');

CREATE OR REPLACE FUNCTION fn_set_updated_dt() RETURNS trigger AS $fn$
BEGIN
  NEW.updated_dt := now();
  RETURN NEW;
END $fn$ LANGUAGE plpgsql;

-- =============================================================================
-- 1. PRINCIPALS, ORGANIZATIONS & TENANCY
-- =============================================================================

CREATE TABLE users (
  id           BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id BIGINT,
  user_type    user_type   NOT NULL DEFAULT 'user',
  user_status  user_status NOT NULL DEFAULT 'active',
  user_ref     TEXT NOT NULL,
  display_name TEXT,
  job_role     TEXT,
  team         TEXT,
  effective_from DATE,
  effective_to   DATE,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_users PRIMARY KEY (id),
  CONSTRAINT uq_users_id_workspace UNIQUE (id, workspace_id),
  CONSTRAINT ck_users_effective CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from),
  CONSTRAINT fk_users_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_users_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE UNIQUE INDEX uq_users_workspace_user_ref ON users (workspace_id, user_ref) WHERE workspace_id IS NOT NULL;
CREATE UNIQUE INDEX uq_users_platform_user_ref  ON users (user_ref) WHERE workspace_id IS NULL;

INSERT INTO users (workspace_id, user_type, user_ref, display_name, created_by, updated_by)
  OVERRIDING SYSTEM VALUE VALUES (NULL, 'service', 'system', 'Platform System', 1, 1);

CREATE TABLE organizations (
  id             BIGINT GENERATED ALWAYS AS IDENTITY,
  org_status     org_status NOT NULL DEFAULT 'pending',
  plan_type      plan_type  NOT NULL DEFAULT 'free',
  org_ref        TEXT NOT NULL,
  legal_name     TEXT NOT NULL,
  display_name   TEXT,
  primary_domain TEXT,
  country_code   CHAR(2),
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_organizations PRIMARY KEY (id),
  CONSTRAINT uq_organizations_org_ref UNIQUE (org_ref),
  CONSTRAINT fk_organizations_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_organizations_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE workspaces (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY,
  organization_id       BIGINT NOT NULL,
  environment_type      environment_type NOT NULL DEFAULT 'production',
  workspace_status      workspace_status NOT NULL DEFAULT 'active',
  workspace_ref         TEXT NOT NULL,
  name                  TEXT NOT NULL,
  backfill_horizon_days INTEGER NOT NULL DEFAULT 400,
  rate_limit_rpm        INTEGER NOT NULL DEFAULT 600,
  rate_limit_epm        INTEGER NOT NULL DEFAULT 60000,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_workspaces PRIMARY KEY (id),
  CONSTRAINT uq_workspaces_workspace_ref UNIQUE (workspace_ref),
  CONSTRAINT fk_workspaces_organization FOREIGN KEY (organization_id) REFERENCES organizations (id),
  CONSTRAINT ck_workspaces_backfill CHECK (backfill_horizon_days > 0),
  CONSTRAINT ck_workspaces_rate_limits CHECK (rate_limit_rpm > 0 AND rate_limit_epm > 0),
  CONSTRAINT fk_workspaces_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_workspaces_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_workspaces_organization_id ON workspaces (organization_id);

ALTER TABLE users ADD CONSTRAINT fk_users_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id);

CREATE TABLE api_keys (
  id             BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id   BIGINT NOT NULL,
  owner_user_id  BIGINT NOT NULL,
  api_key_mode   api_key_mode   NOT NULL,
  api_key_scope  api_key_scope  NOT NULL DEFAULT 'workspace',
  api_key_status api_key_status NOT NULL DEFAULT 'active',
  api_key_ref    TEXT NOT NULL,
  secret_hash    TEXT NOT NULL,
  roles          TEXT[] NOT NULL DEFAULT '{}',
  revoked_dt     TIMESTAMPTZ,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_api_keys PRIMARY KEY (id),
  CONSTRAINT uq_api_keys_api_key_ref UNIQUE (api_key_ref),
  CONSTRAINT uq_api_keys_id_workspace UNIQUE (id, workspace_id),
  CONSTRAINT ck_api_keys_revoked_pairing CHECK ((api_key_status = 'revoked') = (revoked_dt IS NOT NULL)),
  CONSTRAINT fk_api_keys_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_api_keys_owner_user FOREIGN KEY (owner_user_id) REFERENCES users (id),
  CONSTRAINT fk_api_keys_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_api_keys_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_api_keys_workspace_id ON api_keys (workspace_id);

-- =============================================================================
-- 2. CONFIG PLANE
-- =============================================================================

CREATE TABLE deployments (
  id                 BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id       BIGINT NOT NULL,
  deployment_type    deployment_type NOT NULL,
  deployment_mode    deployment_mode NOT NULL DEFAULT 'measured',
  deployment_status  deployment_status NOT NULL DEFAULT 'active',
  attribution_mode   attribution_mode,
  deployment_ref     TEXT NOT NULL,
  name               TEXT NOT NULL,
  external_ref       TEXT,
  planned_rollout_at TIMESTAMPTZ,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_deployments PRIMARY KEY (id),
  CONSTRAINT uq_deployments_deployment_ref UNIQUE (deployment_ref),
  CONSTRAINT uq_deployments_id_workspace UNIQUE (id, workspace_id),
  CONSTRAINT uq_deployments_workspace_external_ref UNIQUE (workspace_id, external_ref),
  CONSTRAINT fk_deployments_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_deployments_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_deployments_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_deployments_workspace_id ON deployments (workspace_id);

CREATE TABLE api_key_deployments (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  BIGINT NOT NULL,
  api_key_id    BIGINT NOT NULL,
  deployment_id BIGINT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_api_key_deployments PRIMARY KEY (id),
  CONSTRAINT uq_api_key_deployments UNIQUE (api_key_id, deployment_id),
  CONSTRAINT fk_api_key_deployments_api_key FOREIGN KEY (api_key_id, workspace_id) REFERENCES api_keys (id, workspace_id) ON DELETE CASCADE,
  CONSTRAINT fk_api_key_deployments_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_api_key_deployments_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_api_key_deployments_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE deployment_licenses (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY,
  deployment_id       BIGINT NOT NULL,
  period_unit_type    period_unit_type NOT NULL DEFAULT 'year',
  seats_paid          INTEGER NOT NULL,
  price_per_seat      NUMERIC(14,6),
  currency            CHAR(3) NOT NULL DEFAULT 'USD',
  valid_from          DATE NOT NULL,
  valid_to            DATE,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_deployment_licenses PRIMARY KEY (id),
  CONSTRAINT fk_deployment_licenses_deployment FOREIGN KEY (deployment_id) REFERENCES deployments (id),
  CONSTRAINT ck_deployment_licenses_seats CHECK (seats_paid >= 0),
  CONSTRAINT ck_deployment_licenses_validity CHECK (valid_to IS NULL OR valid_to >= valid_from),
  CONSTRAINT fk_deployment_licenses_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_deployment_licenses_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE UNIQUE INDEX uq_deployment_licenses_current ON deployment_licenses (deployment_id) WHERE valid_to IS NULL;

CREATE TABLE deployment_license_identifiers (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY,
  deployment_license_id BIGINT NOT NULL,
  licensed_identifier   TEXT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_deployment_license_identifiers PRIMARY KEY (id),
  CONSTRAINT uq_deployment_license_identifiers UNIQUE (deployment_license_id, licensed_identifier),
  CONSTRAINT fk_deployment_license_identifiers_license FOREIGN KEY (deployment_license_id) REFERENCES deployment_licenses (id) ON DELETE CASCADE,
  CONSTRAINT fk_deployment_license_identifiers_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_deployment_license_identifiers_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE deployment_service_users (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  BIGINT NOT NULL,
  deployment_id BIGINT NOT NULL,
  user_id       BIGINT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_deployment_service_users PRIMARY KEY (id),
  CONSTRAINT uq_deployment_service_users UNIQUE (deployment_id, user_id),
  CONSTRAINT fk_deployment_service_users_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_deployment_service_users_user FOREIGN KEY (user_id, workspace_id) REFERENCES users (id, workspace_id),
  CONSTRAINT fk_deployment_service_users_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_deployment_service_users_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE user_identifiers (
  id               BIGINT GENERATED ALWAYS AS IDENTITY,
  user_id          BIGINT NOT NULL,
  workspace_id     BIGINT NOT NULL,
  record_status    record_status NOT NULL DEFAULT 'active',
  namespace        TEXT NOT NULL,
  identifier_value TEXT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_user_identifiers PRIMARY KEY (id),
  CONSTRAINT fk_user_identifiers_user FOREIGN KEY (user_id, workspace_id) REFERENCES users (id, workspace_id) ON DELETE CASCADE,
  CONSTRAINT fk_user_identifiers_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_user_identifiers_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_user_identifiers_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE UNIQUE INDEX uq_user_identifiers_active ON user_identifiers (workspace_id, namespace, identifier_value) WHERE record_status = 'active';
CREATE INDEX ix_user_identifiers_user_id ON user_identifiers (user_id);

CREATE TABLE identifier_namespaces (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  BIGINT NOT NULL,
  deployment_id BIGINT,
  identifier_field_type identifier_field_type NOT NULL,
  record_status record_status NOT NULL DEFAULT 'active',
  source_ref    TEXT NOT NULL DEFAULT '',
  namespace     TEXT NOT NULL,
  format_regex  TEXT,
  description   TEXT,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_id_namespaces PRIMARY KEY (id),
  CONSTRAINT fk_id_namespaces_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_id_namespaces_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_id_namespaces_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_id_namespaces_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE UNIQUE INDEX uq_id_namespaces_active_deployment ON identifier_namespaces (workspace_id, deployment_id, identifier_field_type, source_ref) WHERE record_status = 'active' AND deployment_id IS NOT NULL;
CREATE UNIQUE INDEX uq_id_namespaces_active_workspace_default ON identifier_namespaces (workspace_id, identifier_field_type, source_ref) WHERE record_status = 'active' AND deployment_id IS NULL;

CREATE TABLE parameter_sets (
  id             BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id   BIGINT NOT NULL,
  version        INTEGER NOT NULL,
  effective_from DATE NOT NULL,
  currency       CHAR(3) NOT NULL DEFAULT 'USD',
  payload        JSONB NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_parameter_sets PRIMARY KEY (id),
  CONSTRAINT uq_parameter_sets_workspace_version UNIQUE (workspace_id, version),
  CONSTRAINT uq_parameter_sets_workspace_effective_from UNIQUE (workspace_id, effective_from),
  CONSTRAINT ck_parameter_sets_version CHECK (version > 0),
  CONSTRAINT fk_parameter_sets_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_parameter_sets_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_parameter_sets_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE qa_label_definitions (
  id                          BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id                BIGINT NOT NULL,
  record_status               record_status NOT NULL DEFAULT 'active',
  grader_status               grader_status NOT NULL DEFAULT 'uncalibrated',
  label_schema_ref            TEXT NOT NULL,
  name                        TEXT NOT NULL,
  label_values                TEXT[] NOT NULL,
  applies_to_category         TEXT,
  min_samples_for_calibration INTEGER NOT NULL DEFAULT 200,
  samples_received            INTEGER NOT NULL DEFAULT 0,
  agreement_rate              NUMERIC(5,4),
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_qa_label_schemas PRIMARY KEY (id),
  CONSTRAINT uq_qa_label_schemas_ref UNIQUE (label_schema_ref),
  CONSTRAINT fk_qa_label_schemas_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT ck_qa_label_schemas_values CHECK (array_length(label_values, 1) BETWEEN 2 AND 20),
  CONSTRAINT ck_qa_label_schemas_agreement CHECK (agreement_rate IS NULL OR (agreement_rate >= 0 AND agreement_rate <= 1)),
  CONSTRAINT fk_qa_label_schemas_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_qa_label_schemas_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE webhooks (
  id                 BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id       BIGINT NOT NULL,
  webhook_status     webhook_status NOT NULL DEFAULT 'unverified',
  webhook_event_type webhook_event_type[] NOT NULL,
  webhook_ref        TEXT NOT NULL,
  url                TEXT NOT NULL,
  secret_hash        TEXT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_webhooks PRIMARY KEY (id),
  CONSTRAINT uq_webhooks_ref UNIQUE (webhook_ref),
  CONSTRAINT uq_webhooks_workspace_url_events UNIQUE (workspace_id, url, webhook_event_type),
  CONSTRAINT ck_webhooks_event_types CHECK (array_length(webhook_event_type, 1) >= 1),
  CONSTRAINT fk_webhooks_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_webhooks_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_webhooks_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE webhook_deliveries (
  id                 BIGINT GENERATED ALWAYS AS IDENTITY,
  webhook_id         BIGINT NOT NULL,
  webhook_event_type webhook_event_type NOT NULL,
  delivery_status    delivery_status NOT NULL DEFAULT 'retrying',
  delivery_ref       TEXT NOT NULL DEFAULT ('dl_' || replace(gen_random_uuid()::text, '-', '')),
  payload            JSONB NOT NULL,
  attempt_count      INTEGER NOT NULL DEFAULT 0,
  last_http_status   SMALLINT,
  next_retry_at      TIMESTAMPTZ,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_webhook_deliveries PRIMARY KEY (id),
  CONSTRAINT uq_webhook_deliveries_ref UNIQUE (delivery_ref),
  CONSTRAINT fk_webhook_deliveries_webhook FOREIGN KEY (webhook_id) REFERENCES webhooks (id) ON DELETE CASCADE,
  CONSTRAINT fk_webhook_deliveries_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_webhook_deliveries_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id, created_dt DESC);
CREATE INDEX ix_webhook_deliveries_retry ON webhook_deliveries (next_retry_at) WHERE delivery_status = 'retrying';

CREATE TABLE mapping_contracts (
  id           BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id BIGINT NOT NULL,
  event_type   event_type NOT NULL,
  source_ref   TEXT NOT NULL,
  version      INTEGER NOT NULL,
  field_map    JSONB NOT NULL,
  notes        TEXT,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_mapping_contracts PRIMARY KEY (id),
  CONSTRAINT uq_mapping_contracts UNIQUE (workspace_id, source_ref, version),
  CONSTRAINT ck_mapping_contracts_version CHECK (version > 0),
  CONSTRAINT fk_mapping_contracts_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_mapping_contracts_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_mapping_contracts_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE change_log (
  id               BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id     BIGINT NOT NULL,
  superseded_by_id BIGINT,
  change_type      change_type NOT NULL,
  sided_type       sided_type NOT NULL,
  change_event_ref TEXT NOT NULL,
  occurred_at      TIMESTAMPTZ NOT NULL,
  description      TEXT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_change_log PRIMARY KEY (id),
  CONSTRAINT uq_change_log_ref UNIQUE (change_event_ref),
  CONSTRAINT uq_change_log_id_workspace UNIQUE (id, workspace_id),
  CONSTRAINT fk_change_log_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_change_log_superseded_by FOREIGN KEY (superseded_by_id, workspace_id) REFERENCES change_log (id, workspace_id),
  CONSTRAINT fk_change_log_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_change_log_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE change_log_deployments (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  BIGINT NOT NULL,
  change_log_id BIGINT NOT NULL,
  deployment_id BIGINT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_change_log_deployments PRIMARY KEY (id),
  CONSTRAINT uq_change_log_deployments UNIQUE (change_log_id, deployment_id),
  CONSTRAINT fk_change_log_deployments_change FOREIGN KEY (change_log_id, workspace_id) REFERENCES change_log (id, workspace_id) ON DELETE CASCADE,
  CONSTRAINT fk_change_log_deployments_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_change_log_deployments_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_change_log_deployments_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

-- =============================================================================
-- 3. WRITE PLANE
-- =============================================================================

CREATE TABLE import_jobs (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id          BIGINT NOT NULL,
  import_type           import_type NOT NULL,
  import_status         import_status NOT NULL DEFAULT 'queued',
  import_ref            TEXT NOT NULL,
  source_ref            TEXT,
  expected_events       BIGINT NOT NULL DEFAULT 0,
  received_count        BIGINT NOT NULL DEFAULT 0,
  accepted_count        BIGINT NOT NULL DEFAULT 0,
  duplicate_count       BIGINT NOT NULL DEFAULT 0,
  rejected_count        BIGINT NOT NULL DEFAULT 0,
  upload_path           TEXT,
  upload_url_expires_at TIMESTAMPTZ,
  error_report_path     TEXT,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_import_jobs PRIMARY KEY (id),
  CONSTRAINT uq_import_jobs_ref UNIQUE (import_ref),
  CONSTRAINT uq_import_jobs_id_workspace UNIQUE (id, workspace_id),
  CONSTRAINT ck_import_jobs_counts CHECK (expected_events >= 0 AND received_count >= 0 AND accepted_count >= 0 AND duplicate_count >= 0 AND rejected_count >= 0),
  CONSTRAINT fk_import_jobs_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_import_jobs_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_import_jobs_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE work_items (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  BIGINT NOT NULL,
  size_type     size_type,
  work_item_ref TEXT NOT NULL,
  category      TEXT,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_work_items PRIMARY KEY (id),
  CONSTRAINT uq_work_items UNIQUE (workspace_id, work_item_ref),
  CONSTRAINT uq_work_items_id_workspace UNIQUE (id, workspace_id),
  CONSTRAINT fk_work_items_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_work_items_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_work_items_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE events (
  id               BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id     BIGINT NOT NULL,
  deployment_id    BIGINT NOT NULL,
  import_job_id    BIGINT,
  resolved_user_id BIGINT,
  work_item_id     BIGINT,
  event_type       event_type NOT NULL,
  origin_type      origin_type NOT NULL,
  pipeline_status  pipeline_status NOT NULL DEFAULT 'accepted',
  exclusion_reason exclusion_reason,
  event_key        CHAR(32) NOT NULL,
  schema_version   TEXT NOT NULL,
  occurred_at      TIMESTAMPTZ NOT NULL,
  received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_ref       TEXT,
  payload          JSONB NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_events PRIMARY KEY (id, occurred_at),
  CONSTRAINT uq_events_dedup UNIQUE (workspace_id, event_key, occurred_at),
  CONSTRAINT ck_events_exclusion CHECK (exclusion_reason IS NULL OR pipeline_status = 'excluded'),
  CONSTRAINT fk_events_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_events_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_events_import_job FOREIGN KEY (import_job_id, workspace_id) REFERENCES import_jobs (id, workspace_id),
  CONSTRAINT fk_events_resolved_user FOREIGN KEY (resolved_user_id, workspace_id) REFERENCES users (id, workspace_id),
  CONSTRAINT fk_events_work_item FOREIGN KEY (work_item_id, workspace_id) REFERENCES work_items (id, workspace_id),
  CONSTRAINT fk_events_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_events_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE events_default PARTITION OF events DEFAULT;

CREATE INDEX ix_events_deployment_occurred ON events (deployment_id, occurred_at);
CREATE INDEX ix_events_work_item ON events (work_item_id) WHERE work_item_id IS NOT NULL;
CREATE INDEX ix_events_resolved_user ON events (resolved_user_id) WHERE resolved_user_id IS NOT NULL;
CREATE INDEX ix_events_unresolved ON events (workspace_id, occurred_at) WHERE pipeline_status = 'accepted';
CREATE INDEX ix_events_source_ref ON events (deployment_id, source_ref, occurred_at) WHERE source_ref IS NOT NULL;
CREATE INDEX ix_events_import_job ON events (import_job_id) WHERE import_job_id IS NOT NULL;

CREATE TABLE rejected_events (
  id             BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id   BIGINT NOT NULL,
  import_job_id  BIGINT,
  event_type     event_type,
  deployment_ref TEXT,
  event_key      CHAR(32),
  error_code     TEXT NOT NULL,
  field_path     TEXT,
  source_ref     TEXT,
  request_id     TEXT NOT NULL,
  received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_rejected_events PRIMARY KEY (id),
  CONSTRAINT fk_rejected_events_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_rejected_events_import_job FOREIGN KEY (import_job_id, workspace_id) REFERENCES import_jobs (id, workspace_id),
  CONSTRAINT fk_rejected_events_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_rejected_events_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_rejected_events_event_key ON rejected_events (workspace_id, event_key) WHERE event_key IS NOT NULL;
CREATE INDEX ix_rejected_events_received ON rejected_events (workspace_id, received_at);
CREATE INDEX ix_rejected_events_request_id ON rejected_events (request_id);
CREATE INDEX ix_rejected_events_import_job ON rejected_events (import_job_id) WHERE import_job_id IS NOT NULL;

-- =============================================================================
-- 4. LIVE-SERVING AGGREGATES
-- =============================================================================

CREATE TABLE meter_usage_daily (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  deployment_id BIGINT NOT NULL,
  meter_type    meter_type NOT NULL,
  meter_ref     TEXT NOT NULL DEFAULT '',
  bucket_date   DATE NOT NULL,
  quantity_sum  NUMERIC(20,6) NOT NULL DEFAULT 0,
  gauge_last    NUMERIC(20,6),
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_meter_usage_daily PRIMARY KEY (id),
  CONSTRAINT uq_meter_usage_daily UNIQUE (deployment_id, meter_type, meter_ref, bucket_date),
  CONSTRAINT ck_meter_usage_daily_quantity CHECK (quantity_sum >= 0),
  CONSTRAINT ck_meter_usage_daily_gauge CHECK (gauge_last IS NULL OR gauge_last >= 0),
  CONSTRAINT fk_meter_usage_daily_deployment FOREIGN KEY (deployment_id) REFERENCES deployments (id),
  CONSTRAINT fk_meter_usage_daily_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_meter_usage_daily_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE user_activity_daily (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id  BIGINT NOT NULL,
  deployment_id BIGINT NOT NULL,
  user_id       BIGINT NOT NULL,
  activity_date DATE NOT NULL,
  event_count   INTEGER NOT NULL DEFAULT 0,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_user_activity_daily PRIMARY KEY (id),
  CONSTRAINT uq_user_activity_daily UNIQUE (deployment_id, user_id, activity_date),
  CONSTRAINT ck_user_activity_daily_count CHECK (event_count >= 0),
  CONSTRAINT fk_user_activity_daily_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_user_activity_daily_user FOREIGN KEY (user_id, workspace_id) REFERENCES users (id, workspace_id),
  CONSTRAINT fk_user_activity_daily_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_user_activity_daily_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE ingestion_stats_daily (
  id                     BIGINT GENERATED ALWAYS AS IDENTITY,
  deployment_id          BIGINT NOT NULL,
  source_ref             TEXT NOT NULL DEFAULT '',
  stat_date              DATE NOT NULL,
  accepted_count         BIGINT NOT NULL DEFAULT 0,
  duplicate_count        BIGINT NOT NULL DEFAULT 0,
  rejected_count         BIGINT NOT NULL DEFAULT 0,
  identity_bearing_count BIGINT NOT NULL DEFAULT 0,
  resolved_count         BIGINT NOT NULL DEFAULT 0,
  activity_count         BIGINT NOT NULL DEFAULT 0,
  joined_count           BIGINT NOT NULL DEFAULT 0,
  last_event_at          TIMESTAMPTZ,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_ingestion_stats_daily PRIMARY KEY (id),
  CONSTRAINT uq_ingestion_stats_daily UNIQUE (deployment_id, source_ref, stat_date),
  CONSTRAINT fk_ingestion_stats_daily_deployment FOREIGN KEY (deployment_id) REFERENCES deployments (id),
  CONSTRAINT fk_ingestion_stats_daily_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_ingestion_stats_daily_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE seat_classification_thresholds (
  id             BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id   BIGINT NOT NULL,
  version        TEXT NOT NULL,
  rules          JSONB NOT NULL,
  effective_from DATE NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_seat_classification_thresholds PRIMARY KEY (id),
  CONSTRAINT uq_seat_classification_thresholds UNIQUE (workspace_id, version),
  CONSTRAINT uq_seat_classification_thresholds_effective UNIQUE (workspace_id, effective_from),
  CONSTRAINT fk_seat_classification_thresholds_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_seat_classification_thresholds_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_seat_classification_thresholds_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

-- =============================================================================
-- 5. READ PLANE
-- =============================================================================

CREATE TABLE figures (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id          BIGINT NOT NULL,
  deployment_id         BIGINT NOT NULL,
  figure_status         figure_status NOT NULL,
  grade_type            grade_type,
  value_class_type      value_class_type,
  figure_ref            TEXT NOT NULL,
  version               INTEGER NOT NULL DEFAULT 1,
  period                TEXT NOT NULL,
  claim                 TEXT,
  value                 NUMERIC(20,6),
  ci_low                NUMERIC(20,6),
  ci_high               NUMERIC(20,6),
  method_id             TEXT,
  method_version        TEXT,
  parameter_set_version INTEGER,
  change_treatment      TEXT,
  refusal_reason        TEXT,
  margin_exceeds_effect BOOLEAN,
  expected_verdict_date DATE,
  missing_parameter     TEXT,
  computed_at           TIMESTAMPTZ NOT NULL,
  next_compute_at       TIMESTAMPTZ,
  lineage               JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_figures PRIMARY KEY (id),
  CONSTRAINT uq_figures UNIQUE (deployment_id, figure_ref, version),
  CONSTRAINT uq_figures_id_deployment UNIQUE (id, deployment_id),
  CONSTRAINT ck_figures_version CHECK (version > 0),
  CONSTRAINT ck_figures_period CHECK (period ~ '^[0-9]{4}-(q[1-4]|0[1-9]|1[0-2])$'),
  CONSTRAINT ck_figures_computed_shape CHECK (figure_status <> 'computed' OR (claim IS NOT NULL AND value IS NOT NULL AND grade_type IS NOT NULL)),
  CONSTRAINT ck_figures_ci CHECK (ci_low IS NULL OR ci_high IS NULL OR ci_low <= ci_high),
  CONSTRAINT fk_figures_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_figures_parameter_set FOREIGN KEY (workspace_id, parameter_set_version) REFERENCES parameter_sets (workspace_id, version),
  CONSTRAINT fk_figures_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_figures_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_figures_deployment_period ON figures (deployment_id, period);

CREATE TABLE reports (
  id                 BIGINT GENERATED ALWAYS AS IDENTITY,
  deployment_id      BIGINT NOT NULL,
  report_ref         TEXT NOT NULL,
  version            INTEGER NOT NULL DEFAULT 1,
  period             TEXT NOT NULL,
  issued_at          TIMESTAMPTZ NOT NULL,
  grade_profile      JSONB NOT NULL,
  artifact_path      TEXT NOT NULL,
  supersedes_version INTEGER,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_reports PRIMARY KEY (id),
  CONSTRAINT uq_reports UNIQUE (deployment_id, report_ref, version),
  CONSTRAINT uq_reports_id_deployment UNIQUE (id, deployment_id),
  CONSTRAINT ck_reports_version CHECK (version > 0),
  CONSTRAINT ck_reports_period CHECK (period ~ '^[0-9]{4}-(q[1-4]|0[1-9]|1[0-2])$'),
  CONSTRAINT fk_reports_deployment FOREIGN KEY (deployment_id) REFERENCES deployments (id),
  CONSTRAINT fk_reports_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_reports_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE report_figures (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  deployment_id BIGINT NOT NULL,
  report_id     BIGINT NOT NULL,
  figure_id     BIGINT NOT NULL,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_report_figures PRIMARY KEY (id),
  CONSTRAINT uq_report_figures UNIQUE (report_id, figure_id),
  CONSTRAINT fk_report_figures_report FOREIGN KEY (report_id, deployment_id) REFERENCES reports (id, deployment_id) ON DELETE CASCADE,
  CONSTRAINT fk_report_figures_figure FOREIGN KEY (figure_id, deployment_id) REFERENCES figures (id, deployment_id),
  CONSTRAINT fk_report_figures_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_report_figures_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);

CREATE TABLE alerts (
  id              BIGINT GENERATED ALWAYS AS IDENTITY,
  workspace_id    BIGINT NOT NULL,
  deployment_id   BIGINT,
  acked_by        BIGINT,
  alert_code_type alert_code_type NOT NULL,
  alert_status    alert_status NOT NULL DEFAULT 'open',
  alert_ref       TEXT NOT NULL,
  detail          TEXT NOT NULL,
  first_seen      TIMESTAMPTZ NOT NULL,
  last_seen       TIMESTAMPTZ NOT NULL,
  acked_at        TIMESTAMPTZ,
  created_dt TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT NOT NULL,
  updated_dt TIMESTAMPTZ NOT NULL DEFAULT now(), updated_by BIGINT NOT NULL,
  CONSTRAINT pk_alerts PRIMARY KEY (id),
  CONSTRAINT uq_alerts_ref UNIQUE (alert_ref),
  CONSTRAINT ck_alerts_ack CHECK ((acked_by IS NULL) = (acked_at IS NULL)),
  CONSTRAINT fk_alerts_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
  CONSTRAINT fk_alerts_deployment FOREIGN KEY (deployment_id, workspace_id) REFERENCES deployments (id, workspace_id),
  CONSTRAINT fk_alerts_acked_by FOREIGN KEY (acked_by) REFERENCES users (id),
  CONSTRAINT fk_alerts_created_by FOREIGN KEY (created_by) REFERENCES users (id),
  CONSTRAINT fk_alerts_updated_by FOREIGN KEY (updated_by) REFERENCES users (id)
);
CREATE INDEX ix_alerts_open ON alerts (workspace_id, alert_status) WHERE alert_status = 'open';

DO $trg$
DECLARE t TEXT;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename NOT IN ('events_default')
  LOOP
    EXECUTE format('CREATE TRIGGER trg_%I_updated_dt BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION fn_set_updated_dt()', t, t);
  END LOOP;
END $trg$;

-- ---------- table descriptions (detailed usage) -----------------------------
COMMENT ON TABLE organizations IS 'The customer COMPANY - the commercial and legal entity TrueFigure contracts with. Holds identity (legal_name, primary_domain, country_code) and the TrueFigure commercial relationship: plan_type (free|team|organization|enterprise|vendor) governs entitlements. One organization owns 1..N workspaces (typically production + sandbox, or per-division). NOTE the licensing distinction: this table is about the company''s relationship WITH TRUEFIGURE; deployment_licenses is the company''s declaration of what it pays its AI VENDOR for the measured tool - two different contracts, never conflated.';
COMMENT ON TABLE workspaces IS 'Operational tenancy unit under an organization - the scope every API key binds to and every *_ref, dedup key, roster, and parameter set lives in. environment_type separates production from sandbox workspaces of the same company. rate_limit_rpm/epm are the limiter tier served by /v1/whoami; backfill_horizon_days bounds TF-EVT-009 (contract-extended for long-seasonality verticals). Written at onboarding/contract changes; read on every authenticated request.';
COMMENT ON TABLE users IS 'All principals known to the platform. Two populations share this table, distinguished by user_type: (a) workspace members who operate the console/API (own api_keys, ack alerts, author parameters), and (b) the measured workforce synced from HR/IdP via POST /v1/roster:batch (wire user_ref; wire role -> job_role). job_role/team key into labor_rates resolution ((job_role,team) -> job_role -> team default). user_type in (shared,service,bot) makes matching events resolve-then-exclude (BR-011). user_status: inactive stops new resolution from effective_to forward; blocked is an admin hard-stop - no resolution AND excluded from every artifact regardless of dates. Row id=1 is the seeded platform system user (workspace_id NULL).';
COMMENT ON TABLE api_keys IS 'Authentication credentials; secret stored only as secret_hash. api_key_mode (production|test) governs echo-vs-persist and is embedded in the key format (tf_live_/tf_test_); api_key_scope=deployment restricts to grants in api_key_deployments (TF-AUTH-002 outside them); roles[] carries privileged grants (finance_params, org_admin). owner_user_id attributes key-driven writes for the audit columns. Revocation is status change, never deletion.';
COMMENT ON TABLE api_key_deployments IS 'Grant rows for deployment-scoped keys. workspace_id + composite FKs onto both parents make a cross-tenant grant unrepresentable. Read by the authorizer on every scoped request.';
COMMENT ON TABLE deployments IS 'One measured AI system (POST /v1/deployments) - the unit every event carries and every figure/report attaches to. deployment_mode=planned accumulates pre-rollout baseline from lifecycle events; the pipeline flips to measured at first accepted activity event or the planned date. deployment_type immutable once events exist (TF-CFG-006, app-enforced). deployment_status: paused stops ingestion temporarily (resume intended, figures freeze); archived is terminal-intent, history and figures preserved. attribution_mode written by the multi-tool analysis job.';
COMMENT ON TABLE deployment_licenses IS 'The customer''s declaration of the MEASURED VENDOR TOOL''s seat contract (PUT /v1/deployments/{id}/license) - what THEY pay the AI vendor, the denominator for utilization and priced waste. NOT TrueFigure''s own licensing (that is organizations.plan_type). Historized by [valid_from, valid_to) windows; exactly one current row (valid_to IS NULL) per deployment via partial unique. A new PUT closes the current row and inserts the next.';
COMMENT ON TABLE deployment_license_identifiers IS 'Optional explicit list of licensed identifier VALUES for one license declaration, enabling exact never-activated lists instead of count derivation. Resolved through user_identifiers like event refs.';
COMMENT ON TABLE deployment_service_users IS 'G6 (overlay topology): links roster service accounts (users.user_type=service, app-validated) that the MEASURED vendor''s product operates inside incumbent systems to the deployment they belong to. When the customer-origin feed witnesses actions performed by such an account, the engine can attribute them to the deployment - independent, customer-side corroboration of the vendor''s autonomous-action (containment) claims. Declared via deployments service_user_refs; composite FKs keep account and deployment in one tenant.';
COMMENT ON TABLE user_identifiers IS 'The identifier values a user is known by, keyed by namespace label (okta_uid, staff_number, email). THE resolution target: event user_ref/assignee_ref values and licensed_identifier values match identifier_value under the applicable id-namespace declaration. Partial unique = one active owner per (workspace, namespace, value) (TF-CFG-004). record_status=retired ends future matching without rewriting past resolutions.';
COMMENT ON TABLE identifier_namespaces IS 'Declarations from POST /v1/id-namespaces stating which identifier system an event field uses - per deployment or workspace default, and per source feed (source_ref: two feeds into one deployment may use different id systems for the same field). format_regex fails violating values at ingestion (TF-EVT-004). One active declaration per (scope, field, source); supersede retires forward-only.';
COMMENT ON TABLE parameter_sets IS 'Customer financial parameters (POST /v1/parameters) - the ONLY channel money enters the system (BR-016). Immutable whole-document versions (payload JSONB, never partially updated); half-open effective windows, overlap impossible by construction (unique effective_from) which also makes retry idempotent. version is stamped onto every dollarized figure and FK-proven from figures. Requires finance_params role.';
COMMENT ON TABLE qa_label_definitions IS 'Wire resource: /v1/qa-labels/schemas (label_schema_ref kept as the wire ref name). Registered closed vocabularies that turn subjective quality review into measurable data: a qa_label quality signal must reference a schema here and use one of its label_values (free-text labels are unmeasurable and rejected). Calibration state (samples_received, agreement_rate vs min_samples_for_calibration) drives grader_status, the BR-022 gate on scaled AI-assisted grading - below-threshold agreement is reported honestly, not scaled. Immutable once active; changed vocabularies are new schemas with fresh calibration.';
COMMENT ON TABLE webhooks IS 'Push subscriptions (POST /v1/webhooks). Create idempotent by (workspace, url, event set); webhook_status moves unverified -> verified via the 30s challenge, 7 days of total failure suspends (re-verify to resume). secret_hash signs deliveries (v1=hex(hmac_sha256(secret, ts.body))).';
COMMENT ON TABLE webhook_deliveries IS 'Per-delivery attempt log behind GET /v1/webhooks/{id}/deliveries: reference-style payload (never figure bodies), attempt_count against the 1m/5m/30m/2h/6h/24h schedule, next_retry_at drives the retry worker. Consumers dedupe by delivery_ref; redelivery idempotent; app-managed retention (7-day refetch window).';
COMMENT ON TABLE mapping_contracts IS 'Versioned source-format-to-event-field contracts (POST /v1/mapping-contracts), immutable per (workspace, source_ref, version). Translators send X-TrueFigure-Source; ingestion associates events to the version active at ingestion; figures stamp mapping versions in lineage - a silently changed export format becomes visible and attributable. field_map targets validated against the event schema (the no-content guarantee applies to mappings).';
COMMENT ON TABLE change_log IS 'The declared change log (POST /v1/change-events; UC-MEA-03): real-world changes measurement must account for - cutovers, seasonal boundaries, process/staffing/regulatory/tool changes - with sided_type stating whether both compared populations were hit equally. Append-only; corrections supersede by reference. The engine applies the method-defined treatment to computations spanning occurred_at and stamps it on figures.';
COMMENT ON TABLE change_log_deployments IS 'Deployment scope rows for a change-log entry; no rows = workspace-wide. Composite FKs forbid scoping onto another tenant''s deployment.';
COMMENT ON TABLE import_jobs IS 'Bulk NDJSON ingestion jobs (POST /v1/imports) for backfills, publication bursts, and migration loads - streamed through the IDENTICAL validators and dedup as sync ingestion in a separate lane so imports never starve live traffic. expected_events: 0 = not declared (progress % rendered only when > 0). error_report_path holds the per-line NDJSON reject report (backed by rejected_events); upload_path/upload_url_expires_at govern the single-use signed upload.';
COMMENT ON TABLE work_items IS 'Workspace-scoped work-item entities that item-bearing events join to (the joined pipeline state). Created lazily by the join pipeline on first sight of a work_item_ref. category is deliberately TEXT: it is the CUSTOMER''S OWN work taxonomy (claim types, ticket categories), an open per-company vocabulary that cannot be a global enum; size (wire size_band -> column size_type) is the closed cross-customer scale.';
COMMENT ON TABLE events IS 'THE append-only witness store - every measured fact lands here. Monthly range partitions on occurred_at (events_default catches gaps; ops provision ahead). Dedup by (workspace_id, event_key), the digest of the canonical dedup key (spec 3.8): workspace-scoped identity for lifecycle/quality/revenue, deployment-scoped for activity/cost_meter. Composite FKs make cross-tenant references unrepresentable. Pipeline-mutable columns ONLY: pipeline_status (accepted -> resolved -> joined | excluded), resolved_user_id, work_item_id, exclusion_reason; everything else immutable forever.';
COMMENT ON TABLE events_default IS 'Default partition catching rows outside provisioned monthly partitions; monitored by ops, repartitioned in maintenance windows.';
COMMENT ON TABLE rejected_events IS 'Rejected events (accepted ones are never here): serves the rejected state of GET /v1/events/{event_key}/status, backs import error reports, and correlates client logs to server records via request_id. deployment_ref is as-sent text, deliberately not an FK (TF-EVT-002: the deployment may not exist). App-managed retention, default 90 days. SEPARATE from events BY DESIGN: rejects may lack a valid deployment (no FK possible), a computable event_key, or a parseable occurred_at (the NOT NULL partition key); they may repeat keys (retries) which the dedup unique forbids; and their purge cycle must never run DELETEs against the append-only witness store - a status column would relax every one of those guarantees for ALL rows and put garbage inside every measurement query.';
COMMENT ON TABLE meter_usage_daily IS 'Daily consumption aggregates powering /v1/live/cost with zero request-time computation: counter meters sum deltas into quantity_sum; gauge meters keep gauge_last per day (averaged per period at read). Upserted per accepted cost_meter event; priced at read against the effective parameter version - quantities and prices never mix at rest.';
COMMENT ON TABLE user_activity_daily IS 'Per-user, per-deployment daily activity counts powering /v1/live/usage seat classification (never_activated/near_zero/moderate/heavy under the effective thresholds version) and health denominators. workspace_id + composite FKs tie user and deployment to one tenant.';
COMMENT ON TABLE ingestion_stats_daily IS 'Per-deployment, per-source daily ingestion counters powering /v1/live/health without event scans: accept rate, identity resolution rate (resolved/identity_bearing), join rate (joined/activity), last_event_at feeding event_silence detection. Empty source_ref aggregates untagged traffic; the breakdown pinpoints WHICH feed of a multi-feed integration degraded.';
COMMENT ON TABLE seat_classification_thresholds IS 'Versioned seat-classification rule documents (rules JSONB). The version in force prints on every usage surface; historical surfaces keep the version they rendered under - threshold changes create new versions, never rewrite old renders.';
COMMENT ON TABLE figures IS 'Engine-written finalized computations served by /v1/figures - immutable per (deployment, figure_ref, version); corrections append versions, priors stay retrievable. Full stamp bundle: claim (open additive registry - deliberately TEXT), grade_type, value_class_type (realized|capacity), method id+version (governed Standard, outside this DB), FK-proven parameter_set_version, change_treatment. First-class refused (reason, margin_exceeds_effect, expected_verdict_date) and awaiting_parameters (missing_parameter) states. period is the wire grammar (yyyy-qn | yyyy-mm), CHECK-constrained. lineage JSONB serves /lineage (counts, exclusions, versions - references only). The read plane serves, never computes.';
COMMENT ON TABLE reports IS 'Issued report versions behind GET /v1/reports - governed, immutable documents figures roll up into. grade_profile summarizes bound-figure grades (the honesty summary); artifact_path locates the rendered document; corrections issue new versions with supersedes_version and visible diffs; report.issued webhooks reference these rows.';
COMMENT ON TABLE report_figures IS 'The manifest binding a report version to the EXACT figure versions it presents; deployment_id + composite FKs onto both parents forbid binding a figure from another deployment.';
COMMENT ON TABLE alerts IS 'Durable, stateful alert queue behind GET /v1/alerts and the alert.raised webhook (alert_code_type: join_rate_drop, event_silence, counter_monotonic_suspect, ...). Raised by monitors (first_seen/last_seen maintained); alert_status open -> acked via :ack (acked_by/acked_at paired by check) -> resolved when the condition clears; re-triggered conditions open NEW alerts - acking never suppresses recurrence.';

-- ---------- deliberately-not-FK reference columns ----------------------------
COMMENT ON COLUMN events.source_ref IS 'Free feed label from X-TrueFigure-Source. Deliberately NOT an FK to mapping_contracts.source_ref: events legitimately arrive from sources with no mapping contract (native SDK emitters).';
COMMENT ON COLUMN deployment_license_identifiers.licensed_identifier IS 'Identifier VALUE resolved via user_identifiers like event refs. Deliberately not an FK: license lists may cite identifiers not yet in the roster; resolution is pipeline work.';
COMMENT ON COLUMN rejected_events.error_code IS 'NP-<plane>-<nnn> from registry/error-codes.json - versioned code, not a table; a table would force a migration per additive code.';
COMMENT ON COLUMN figures.method_id IS 'Method from the governed, published Measurement Standard - a versioned document registry outside this database; TEXT by design.';
COMMENT ON COLUMN users.job_role IS 'Customer job role from THEIR HR taxonomy (wire field: role) - an open per-company vocabulary keying into parameter_sets.payload labor_rates; correspondence app-validated at parameter write, not FK-enforceable into JSONB. Distinct from api_keys.roles (platform access grants).';
COMMENT ON COLUMN work_items.category IS 'Customer work taxonomy (their claim types / ticket categories) - open per-company vocabulary matched by qa_label_definitions.applies_to_category and comparison grouping; a global enum is impossible by design.';
COMMENT ON COLUMN alerts.acked_by IS 'Deliberately a plain FK (not tenant-composite): platform support staff (workspace_id NULL principals) may acknowledge customer alerts during incidents; app policy restricts customer ackers to the alert''s workspace.';
COMMENT ON COLUMN change_log.superseded_by_id IS 'Direction: set on the SUPERSEDED (old) row when a correction arrives citing wire field supersedes=<old change_event_ref>; the correction is a new append-only row. NULL = current.';
COMMENT ON COLUMN figures.next_compute_at IS 'Engine scheduler stamp: when this figure is next due for recomputation; NULL for final/closed periods.';
COMMENT ON COLUMN import_jobs.expected_events IS '0 = not declared by the caller; progress percentage is rendered only when > 0.';

-- ---------- CI conformance check (enum-column match rule) --------------------
-- SELECT c.table_name, c.column_name, c.udt_name FROM information_schema.columns c
-- JOIN pg_type t ON t.typname = c.udt_name AND t.typtype = 'e'
-- WHERE c.table_schema = 'public' AND c.column_name <> c.udt_name;
-- (must return zero rows)

COMMIT;
