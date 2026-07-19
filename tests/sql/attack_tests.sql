-- =============================================================================
-- attack_tests.sql — the three cross-tenant / integrity attacks that MUST fail.
-- Run against a freshly-migrated database. Exit is non-zero (ON_ERROR_STOP) if
-- any attack is NOT blocked. Prints a NOTICE per blocked attack.
-- =============================================================================

-- Fixtures: two tenants, a deployment in ws1, a service user in ws2, a param set.
INSERT INTO organizations (org_ref, legal_name, created_by, updated_by)
  VALUES ('atk_o1','O1',1,1), ('atk_o2','O2',1,1);
INSERT INTO workspaces (organization_id, workspace_ref, name, created_by, updated_by)
  VALUES ((SELECT id FROM organizations WHERE org_ref='atk_o1'),'atk_w1','W1',1,1),
         ((SELECT id FROM organizations WHERE org_ref='atk_o2'),'atk_w2','W2',1,1);
INSERT INTO deployments (workspace_id, deployment_type, deployment_ref, name, created_by, updated_by)
  VALUES ((SELECT id FROM workspaces WHERE workspace_ref='atk_w1'),'saas_tool','atk_d1','D1',1,1);
INSERT INTO users (workspace_id, user_type, user_ref, display_name, created_by, updated_by)
  VALUES ((SELECT id FROM workspaces WHERE workspace_ref='atk_w2'),'service','atk_svc2','S2',1,1);
INSERT INTO parameter_sets (workspace_id, version, effective_from, payload, created_by, updated_by)
  VALUES ((SELECT id FROM workspaces WHERE workspace_ref='atk_w1'),1,'2024-01-01','{}'::jsonb,1,1);

DO $atk$
DECLARE
  ws1  bigint := (SELECT id FROM workspaces WHERE workspace_ref='atk_w1');
  dep1 bigint := (SELECT id FROM deployments WHERE deployment_ref='atk_d1');
  svc2 bigint := (SELECT id FROM users WHERE user_ref='atk_svc2');
BEGIN
  -- Attack 1: cross-tenant service link (user in ws2 attached under ws1).
  BEGIN
    INSERT INTO deployment_service_users (workspace_id, deployment_id, user_id, created_by, updated_by)
      VALUES (ws1, dep1, svc2, 1, 1);
    RAISE EXCEPTION 'ATTACK 1 NOT BLOCKED: cross-tenant service link accepted';
  EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'attack 1 blocked (fk) ✓';
  END;

  -- Attack 2: duplicate event (same workspace_id, event_key, occurred_at twice).
  BEGIN
    INSERT INTO events (workspace_id, deployment_id, event_type, origin_type, event_key,
                        schema_version, occurred_at, payload, created_by, updated_by)
    VALUES (ws1, dep1, 'activity','customer_system','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','1',
            '2024-06-01T00:00:00Z','{}'::jsonb,1,1),
           (ws1, dep1, 'activity','customer_system','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','1',
            '2024-06-01T00:00:00Z','{}'::jsonb,1,1);
    RAISE EXCEPTION 'ATTACK 2 NOT BLOCKED: duplicate event accepted';
  EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'attack 2 blocked (unique) ✓';
  END;

  -- Attack 3: phantom parameter version (cite version 9 when only 1 exists).
  BEGIN
    INSERT INTO figures (workspace_id, deployment_id, figure_status, grade_type, figure_ref,
                         period, claim, value, parameter_set_version, computed_at, created_by, updated_by)
      VALUES (ws1, dep1, 'computed','measured','atk_f1','2024-q2','c',1.0, 9, now(), 1, 1);
    RAISE EXCEPTION 'ATTACK 3 NOT BLOCKED: phantom parameter version accepted';
  EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'attack 3 blocked (fk) ✓';
  END;

  RAISE NOTICE 'ALL THREE ATTACKS BLOCKED';
END $atk$;
