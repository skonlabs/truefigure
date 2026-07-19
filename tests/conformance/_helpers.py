"""Shared helpers for the conformance suite (drives the real API + ops CLI)."""

from __future__ import annotations

import re
from typing import Any


def auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def secret_from(out: str) -> str:
    m = re.search(r"SECRET \(shown once, store it now\): (\S+)", out)
    assert m, out
    return m.group(1)


def provision(ops, *, org="org_acme", ws="ws_prod", env="production", plan="enterprise",
              user="u_admin", roles=("finance_params",), mode="production") -> str:
    ops("org", "create", "--ref", org, "--legal-name", "Acme Inc", "--plan", plan)
    ops("workspace", "create", "--org-ref", org, "--ref", ws, "--name", "Prod", "--environment", env)
    argv = ["user", "create", "--workspace-ref", ws, "--user-ref", user]
    for r in roles:
        argv += ["--role", r]
    ops(*argv)
    ki = ["key", "issue", "--workspace-ref", ws, "--owner-user-ref", user, "--mode", mode, "--scope", "workspace"]
    for r in roles:
        ki += ["--role", r]
    return secret_from(ops(*ki))


async def make_deployment(client, key, *, dtype="saas_tool", name="Claims AI", **extra) -> str:
    body: dict[str, Any] = {"name": name, "type": dtype}
    body.update(extra)
    r = await client.post("/v1/deployments", json=body, headers=auth(key))
    assert r.status_code == 201, r.text
    return r.json()["data"]["deployment_id"]


async def post_events(client, key, events, source=None):
    h = auth(key)
    if source:
        h["X-TrueFigure-Source"] = source
    return await client.post("/v1/events:batch", json={"events": events}, headers=h)


def activity(dep, user, wi, ts, action="suggestion_accepted", origin="customer_system"):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "activity", "origin": origin,
            "payload": {"user_ref": user, "timestamp": ts, "work_item_id": wi, "action_type": action}}


def lifecycle(dep, wi, event, ts, origin="customer_system", **extra):
    p = {"work_item_id": wi, "event": event, "timestamp": ts}
    p.update(extra)
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "lifecycle", "origin": origin,
            "payload": p}


def cost_meter(dep, meter, qty, ts):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "cost_meter", "origin": "customer_system",
            "payload": {"meter": meter, "quantity": qty, "timestamp": ts}}


def quality(dep, wi, signal, ts, **extra):
    p = {"work_item_id": wi, "signal": signal, "timestamp": ts}
    p.update(extra)
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "quality_signal", "origin": "customer_system",
            "payload": p}


def revenue(dep, wi, ref, ts, touchpoint="influenced"):
    return {"schema_version": "1.0", "deployment_id": dep, "event_type": "revenue_signal", "origin": "customer_system",
            "payload": {"work_item_id": wi, "revenue_ref": ref, "timestamp": ts, "touchpoint": touchpoint}}
