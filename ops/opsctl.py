#!/usr/bin/env python3
"""opsctl — the Console substitute (build-spec §5, population map §1.5).

Performs the control-plane operations the HTTP API deliberately does NOT expose:
create organizations, workspaces, member users; issue/revoke API keys (secret
printed ONCE at issuance); key->deployment grants; plan/limit changes; and
monthly events partition provisioning.

Connects with DATABASE_URL (direct/session connection). No secret is ever
persisted in plaintext — only the SHA-256 hash is stored.

Usage:
  python ops/opsctl.py org create --ref org_acme --legal-name "Acme Inc" [--plan enterprise]
  python ops/opsctl.py workspace create --org-ref org_acme --ref ws_prod --name "Prod" [--environment production]
  python ops/opsctl.py user create --workspace-ref ws_prod --user-ref u_admin [--type user] [--role finance_params]
  python ops/opsctl.py key issue --workspace-ref ws_prod --owner-user-ref u_admin [--mode production] [--scope workspace] [--role finance_params] [--deployment-ref dep_x ...]
  python ops/opsctl.py key revoke --key-ref ak_...
  python ops/opsctl.py grant add --key-ref ak_... --deployment-ref dep_x
  python ops/opsctl.py plan set --org-ref org_acme --plan enterprise
  python ops/opsctl.py limits set --workspace-ref ws_prod [--rpm 600] [--epm 60000] [--backfill-days 400]
  python ops/opsctl.py partitions ensure --months 3 [--start 2026-07]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

SYSTEM_USER_ID = 1  # seeded platform system user; writer for ops-created rows


def _conn() -> psycopg.Connection[dict[str, Any]]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    return psycopg.connect(url, row_factory=dict_row, autocommit=False)


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _one(cur: psycopg.Cursor[dict[str, Any]], sql: str, params: Any) -> dict[str, Any]:
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        raise SystemExit("not found: " + sql.split("WHERE", 1)[-1].strip())
    return row


# ---- org --------------------------------------------------------------------
def org_create(a: argparse.Namespace) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO organizations (org_status, plan_type, org_ref, legal_name, created_by, updated_by)
               VALUES ('active', %s, %s, %s, %s, %s) RETURNING id, org_ref""",
            (a.plan, a.ref, a.legal_name, SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
        row = cur.fetchone()
        assert row is not None
        print(f"organization {row['org_ref']} (id={row['id']}) plan={a.plan}")


def plan_set(a: argparse.Namespace) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE organizations SET plan_type=%s, updated_by=%s WHERE org_ref=%s RETURNING id",
            (a.plan, SYSTEM_USER_ID, a.org_ref),
        )
        if cur.fetchone() is None:
            raise SystemExit(f"organization {a.org_ref} not found")
        print(f"organization {a.org_ref} plan -> {a.plan}")


# ---- workspace --------------------------------------------------------------
def workspace_create(a: argparse.Namespace) -> None:
    with _conn() as c, c.cursor() as cur:
        org = _one(cur, "SELECT id FROM organizations WHERE org_ref=%s", (a.org_ref,))
        cur.execute(
            """INSERT INTO workspaces (organization_id, environment_type, workspace_status,
                   workspace_ref, name, backfill_horizon_days, rate_limit_rpm, rate_limit_epm,
                   created_by, updated_by)
               VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s, %s) RETURNING id, workspace_ref""",
            (org["id"], a.environment, a.ref, a.name, a.backfill_days, a.rpm, a.epm,
             SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
        row = cur.fetchone()
        assert row is not None
        print(f"workspace {row['workspace_ref']} (id={row['id']}) env={a.environment}")


def limits_set(a: argparse.Namespace) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if a.rpm is not None:
        sets.append("rate_limit_rpm=%s")
        params.append(a.rpm)
    if a.epm is not None:
        sets.append("rate_limit_epm=%s")
        params.append(a.epm)
    if a.backfill_days is not None:
        sets.append("backfill_horizon_days=%s")
        params.append(a.backfill_days)
    if not sets:
        raise SystemExit("nothing to set")
    sets.append("updated_by=%s")
    params.append(SYSTEM_USER_ID)
    params.append(a.workspace_ref)
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"UPDATE workspaces SET {', '.join(sets)} WHERE workspace_ref=%s RETURNING id", params)
        if cur.fetchone() is None:
            raise SystemExit(f"workspace {a.workspace_ref} not found")
        print(f"workspace {a.workspace_ref} limits updated")


# ---- users (workspace members) ---------------------------------------------
def user_create(a: argparse.Namespace) -> None:
    with _conn() as c, c.cursor() as cur:
        ws = _one(cur, "SELECT id FROM workspaces WHERE workspace_ref=%s", (a.workspace_ref,))
        cur.execute(
            """INSERT INTO users (workspace_id, user_type, user_status, user_ref, display_name, job_role,
                   created_by, updated_by)
               VALUES (%s, %s, 'active', %s, %s, %s, %s, %s) RETURNING id, user_ref""",
            (ws["id"], a.type, a.user_ref, a.display_name, a.role, SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
        row = cur.fetchone()
        assert row is not None
        print(f"user {row['user_ref']} (id={row['id']}) type={a.type}")


# ---- api keys ---------------------------------------------------------------
def key_issue(a: argparse.Namespace) -> None:
    prefix = "tf_live_" if a.mode == "production" else "tf_test_"
    secret = secrets.token_hex(16)  # 32 hex chars
    presented = prefix + secret
    key_ref = "ak_" + secrets.token_hex(12)
    with _conn() as c, c.cursor() as cur:
        ws = _one(cur, "SELECT id FROM workspaces WHERE workspace_ref=%s", (a.workspace_ref,))
        owner = _one(
            cur,
            "SELECT id FROM users WHERE workspace_id=%s AND user_ref=%s",
            (ws["id"], a.owner_user_ref),
        )
        cur.execute(
            """INSERT INTO api_keys (workspace_id, owner_user_id, api_key_mode, api_key_scope,
                   api_key_status, api_key_ref, secret_hash, roles, created_by, updated_by)
               VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s, %s) RETURNING id""",
            (ws["id"], owner["id"], a.mode, a.scope, key_ref, _hash_key(presented),
             a.role or [], SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
        krow = cur.fetchone()
        assert krow is not None
        key_id = krow["id"]
        for dep_ref in (a.deployment_ref or []):
            dep = _one(
                cur,
                "SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s",
                (dep_ref,),
            )
            cur.execute(
                """INSERT INTO api_key_deployments (workspace_id, api_key_id, deployment_id, created_by, updated_by)
                   VALUES (%s, %s, %s, %s, %s)""",
                (dep["workspace_id"], key_id, dep["id"], SYSTEM_USER_ID, SYSTEM_USER_ID),
            )
    # Secret shown exactly once. Never logged, never stored in plaintext.
    print(f"api key issued: ref={key_ref} scope={a.scope} mode={a.mode}")
    print(f"SECRET (shown once, store it now): {presented}")


def key_revoke(a: argparse.Namespace) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """UPDATE api_keys SET api_key_status='revoked', revoked_dt=now(), updated_by=%s
               WHERE api_key_ref=%s AND api_key_status='active' RETURNING id""",
            (SYSTEM_USER_ID, a.key_ref),
        )
        if cur.fetchone() is None:
            raise SystemExit(f"active key {a.key_ref} not found")
        print(f"api key {a.key_ref} revoked")


def grant_add(a: argparse.Namespace) -> None:
    with _conn() as c, c.cursor() as cur:
        key = _one(cur, "SELECT id, workspace_id FROM api_keys WHERE api_key_ref=%s", (a.key_ref,))
        dep = _one(cur, "SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (a.deployment_ref,))
        if dep["workspace_id"] != key["workspace_id"]:
            raise SystemExit("cross-tenant grant refused")
        cur.execute(
            """INSERT INTO api_key_deployments (workspace_id, api_key_id, deployment_id, created_by, updated_by)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (api_key_id, deployment_id) DO NOTHING""",
            (key["workspace_id"], key["id"], dep["id"], SYSTEM_USER_ID, SYSTEM_USER_ID),
        )
        print(f"grant added: {a.key_ref} -> {a.deployment_ref}")


# ---- partitions -------------------------------------------------------------
def _month_bounds(y: int, m: int) -> tuple[str, str]:
    start = date(y, m, 1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = date(ny, nm, 1)
    return start.isoformat(), end.isoformat()


def partitions_ensure(a: argparse.Namespace) -> None:
    if a.start:
        y, m = (int(x) for x in a.start.split("-"))
    else:
        today = date.today()
        y, m = today.year, today.month
    created = []
    with _conn() as c, c.cursor() as cur:
        for _ in range(a.months):
            name = f"events_{y:04d}_{m:02d}"
            lo, hi = _month_bounds(y, m)
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF events "
                f"FOR VALUES FROM (%s) TO (%s)",
                (lo, hi),
            )
            # New partitions inherit the parent's RLS posture; enable + revoke to be safe.
            cur.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            created.append(name)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    print("partitions ensured: " + ", ".join(created))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="opsctl", description="TrueFigure Console-substitute CLI")
    sub = p.add_subparsers(dest="group", required=True)

    org = sub.add_parser("org").add_subparsers(dest="cmd", required=True)
    oc = org.add_parser("create")
    oc.add_argument("--ref", required=True)
    oc.add_argument("--legal-name", required=True)
    oc.add_argument("--plan", default="free")
    oc.set_defaults(func=org_create)

    ws = sub.add_parser("workspace").add_subparsers(dest="cmd", required=True)
    wc = ws.add_parser("create")
    wc.add_argument("--org-ref", required=True)
    wc.add_argument("--ref", required=True)
    wc.add_argument("--name", required=True)
    wc.add_argument("--environment", default="production", choices=["production", "sandbox"])
    wc.add_argument("--rpm", type=int, default=600)
    wc.add_argument("--epm", type=int, default=60000)
    wc.add_argument("--backfill-days", type=int, default=400)
    wc.set_defaults(func=workspace_create)

    usr = sub.add_parser("user").add_subparsers(dest="cmd", required=True)
    uc = usr.add_parser("create")
    uc.add_argument("--workspace-ref", required=True)
    uc.add_argument("--user-ref", required=True)
    uc.add_argument("--type", default="user", choices=["user", "shared", "service", "bot"])
    uc.add_argument("--display-name", default=None)
    uc.add_argument("--role", default=None)
    uc.set_defaults(func=user_create)

    key = sub.add_parser("key").add_subparsers(dest="cmd", required=True)
    ki = key.add_parser("issue")
    ki.add_argument("--workspace-ref", required=True)
    ki.add_argument("--owner-user-ref", required=True)
    ki.add_argument("--mode", default="production", choices=["production", "test"])
    ki.add_argument("--scope", default="workspace", choices=["workspace", "deployment"])
    ki.add_argument("--role", action="append")
    ki.add_argument("--deployment-ref", action="append")
    ki.set_defaults(func=key_issue)
    kr = key.add_parser("revoke")
    kr.add_argument("--key-ref", required=True)
    kr.set_defaults(func=key_revoke)

    grant = sub.add_parser("grant").add_subparsers(dest="cmd", required=True)
    ga = grant.add_parser("add")
    ga.add_argument("--key-ref", required=True)
    ga.add_argument("--deployment-ref", required=True)
    ga.set_defaults(func=grant_add)

    plan = sub.add_parser("plan").add_subparsers(dest="cmd", required=True)
    ps = plan.add_parser("set")
    ps.add_argument("--org-ref", required=True)
    ps.add_argument("--plan", required=True)
    ps.set_defaults(func=plan_set)

    lim = sub.add_parser("limits").add_subparsers(dest="cmd", required=True)
    lset = lim.add_parser("set")
    lset.add_argument("--workspace-ref", required=True)
    lset.add_argument("--rpm", type=int)
    lset.add_argument("--epm", type=int)
    lset.add_argument("--backfill-days", type=int)
    lset.set_defaults(func=limits_set)

    part = sub.add_parser("partitions").add_subparsers(dest="cmd", required=True)
    pe = part.add_parser("ensure")
    pe.add_argument("--months", type=int, default=1)
    pe.add_argument("--start", default=None)
    pe.set_defaults(func=partitions_ensure)

    pipe = sub.add_parser("pipeline").add_subparsers(dest="cmd", required=True)
    pr = pipe.add_parser("run")
    pr.add_argument("--limit", type=int, default=500)
    pr.set_defaults(func=pipeline_run)

    imp = sub.add_parser("imports").add_subparsers(dest="cmd", required=True)
    ir = imp.add_parser("run")
    ir.add_argument("--import-id", required=True)
    ir.set_defaults(func=imports_run)

    wh = sub.add_parser("webhooks").add_subparsers(dest="cmd", required=True)
    whd = wh.add_parser("deliver")
    whd.add_argument("--limit", type=int, default=100)
    whd.set_defaults(func=webhooks_deliver)

    eng = sub.add_parser("engine").add_subparsers(dest="cmd", required=True)
    er = eng.add_parser("run")
    er.add_argument("--deployment-ref", required=True)
    er.add_argument("--period", required=True)
    er.set_defaults(func=engine_run)

    rep = sub.add_parser("report").add_subparsers(dest="cmd", required=True)
    ri = rep.add_parser("issue")
    ri.add_argument("--deployment-ref", required=True)
    ri.add_argument("--period", required=True)
    ri.set_defaults(func=report_issue)

    mon = sub.add_parser("monitors").add_subparsers(dest="cmd", required=True)
    mr = mon.add_parser("run")
    mr.add_argument("--workspace-ref", required=True)
    mr.set_defaults(func=monitors_run)

    return p


def _add_src_path() -> None:
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "src"))


def pipeline_run(a: argparse.Namespace) -> None:
    _add_src_path()
    from truefigure_sdk.domain.policies import pipeline

    counts = pipeline.run_pipeline(limit=a.limit)
    print("pipeline: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def imports_run(a: argparse.Namespace) -> None:
    _add_src_path()
    from truefigure_sdk.api.routes import imports

    counts = imports.run_import(a.import_id)
    print("import " + a.import_id + ": " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def webhooks_deliver(a: argparse.Namespace) -> None:
    _add_src_path()
    from truefigure_sdk.domain.policies import webhooks_delivery

    counts = webhooks_delivery.deliver_once(webhooks_delivery._default_sender, limit=a.limit)
    print("deliveries: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def _resolve_dep_ids(deployment_ref: str) -> tuple[int, int]:
    with _conn() as c, c.cursor() as cur:
        row = _one(cur, "SELECT id, workspace_id FROM deployments WHERE deployment_ref=%s", (deployment_ref,))
        return int(row["workspace_id"]), int(row["id"])


def engine_run(a: argparse.Namespace) -> None:
    _add_src_path()
    from truefigure_sdk.domain import engine

    ws, dep = _resolve_dep_ids(a.deployment_ref)
    written = engine.compute_deployment(ws, dep, a.period)
    print(f"engine: computed {len(written)} figures for {a.deployment_ref} {a.period}: {', '.join(written)}")


def report_issue(a: argparse.Namespace) -> None:
    _add_src_path()
    from truefigure_sdk.domain import engine

    ws, dep = _resolve_dep_ids(a.deployment_ref)
    ref = engine.issue_report(ws, dep, a.period)
    print(f"report issued: {ref} for {a.deployment_ref} {a.period}")


def monitors_run(a: argparse.Namespace) -> None:
    _add_src_path()
    from truefigure_sdk.domain import engine

    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM workspaces WHERE workspace_ref=%s", (a.workspace_ref,))
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"workspace {a.workspace_ref} not found")
    raised = engine.run_monitors(int(row["id"]))
    print(f"monitors: raised {len(raised)} alerts")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
