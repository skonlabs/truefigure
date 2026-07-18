SELECT 'A_enum_column_match_rule',
  CASE WHEN count(*)=0 THEN 'PASS' ELSE 'FAIL: '||string_agg(c.table_name||'.'||c.column_name,',') END
  FROM information_schema.columns c JOIN pg_type t ON t.typname=c.udt_name AND t.typtype='e'
  WHERE c.table_schema='public' AND c.column_name<>c.udt_name
UNION ALL
SELECT 'B_all_enum_values_lowercase',
  CASE WHEN count(*)=0 THEN 'PASS' ELSE 'FAIL' END FROM pg_enum WHERE enumlabel<>lower(enumlabel)
UNION ALL
SELECT 'C_table_descriptions_31',
  CASE WHEN count(*)=31 THEN 'PASS' ELSE 'FAIL: '||count(*) END
  FROM pg_description d JOIN pg_class c ON c.oid=d.objoid
  WHERE c.relkind IN ('r','p') AND d.objsubid=0 AND c.relnamespace='public'::regnamespace
UNION ALL
SELECT 'D_audit_columns_every_table',
  CASE WHEN count(*)=0 THEN 'PASS' ELSE 'FAIL: '||string_agg(t.tablename,',') END
  FROM pg_tables t WHERE t.schemaname='public' AND t.tablename<>'events_default'
  AND EXISTS (SELECT 1 FROM unnest(ARRAY['created_dt','created_by','updated_dt','updated_by']) a(col)
              WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c
                                WHERE c.table_name=t.tablename AND c.column_name=a.col))
UNION ALL
SELECT 'E_updated_dt_trigger_31',
  CASE WHEN count(DISTINCT event_object_table)=31 THEN 'PASS' ELSE 'FAIL: '||count(DISTINCT event_object_table) END
  FROM information_schema.triggers WHERE trigger_name LIKE 'trg_%_updated_dt'
UNION ALL
SELECT 'F_constraint_naming_convention',
  CASE WHEN count(*)=0 THEN 'PASS' ELSE 'FAIL: '||string_agg(conname,',') END
  FROM pg_constraint WHERE connamespace='public'::regnamespace
  AND conname !~ '^(pk_|fk_|uq_|ck_)' AND contype IN ('p','f','u','c')
  AND conrelid::regclass::text NOT LIKE 'events_default'
UNION ALL
SELECT 'G_fk_orphan_id_columns',
  CASE WHEN count(*)=0 THEN 'PASS'
       WHEN count(*)=1 AND min(c.table_name||'.'||c.column_name)='figures.method_id'
         THEN 'PASS (figures.method_id: documented deliberate TEXT registry reference)'
       ELSE 'FAIL: '||string_agg(c.table_name||'.'||c.column_name,',') END
  FROM information_schema.columns c
  WHERE c.table_schema='public' AND c.table_name<>'events_default'
  AND c.column_name LIKE '%\_id' AND c.column_name NOT IN ('request_id')
  AND NOT EXISTS (SELECT 1 FROM information_schema.key_column_usage k
                  JOIN information_schema.table_constraints tc
                    ON tc.constraint_name=k.constraint_name AND tc.constraint_type='FOREIGN KEY'
                  WHERE k.table_name=c.table_name AND k.column_name=c.column_name);
