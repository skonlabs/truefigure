"""P6 — webhook delivery: verification challenge, HMAC v1 signing, retry schedule,
drop -> suspend, delivery log, reference-only payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import auth
from truefigure_server.domain.policies import webhooks_delivery as wd


class FakeSender:
    """Records calls; returns a scripted sequence of HTTP statuses (last repeats)."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body) -> int:
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


def _ws_id(conn) -> int:
    return conn.execute("SELECT id FROM workspaces WHERE workspace_ref='ws_prod'").fetchone()["id"]


async def _webhook(client, key, events=("figure.updated",), secret="whsecret") -> str:
    r = await client.post("/v1/webhooks", json={"url": "https://hook.example/x", "events": list(events),
                                                "secret": secret}, headers=auth(key))
    return r.json()["data"]["webhook_id"]


def test_sign_is_verifiable_by_consumer() -> None:
    body = b'{"figure_id":"fig_1"}'
    header = wd.sign("s3cr3t", 1234567890, body)
    assert header.startswith("v1=")
    assert wd.verify_signature("s3cr3t", 1234567890, body, header)
    assert not wd.verify_signature("wrong", 1234567890, body, header)


async def test_verification_challenge(client, conn, tenant) -> None:
    wid = await _webhook(client, tenant["key"])
    sender = FakeSender([200])
    ok_ = wd.verify_webhook(wid, _ws_id(conn), sender)
    assert ok_ is True
    assert "challenge=" in sender.calls[0]["url"] and sender.calls[0]["method"] == "GET"
    status = conn.execute("SELECT webhook_status FROM webhooks WHERE webhook_ref=%s", (wid,)).fetchone()
    assert status["webhook_status"] == "verified"


async def test_emit_only_to_verified_and_matching(client, conn, tenant) -> None:
    key = tenant["key"]
    v = await _webhook(client, key, events=["figure.updated"])
    await _webhook(client, key, events=["alert.raised"])  # unverified -> should NOT receive
    wd.verify_webhook(v, _ws_id(conn), FakeSender([200]))
    n = wd.emit(_ws_id(conn), "figure.updated", {"figure_id": "fig_1", "deployment_id": "dep_1"})
    assert n == 1
    row = conn.execute("SELECT payload, delivery_status FROM webhook_deliveries").fetchone()
    assert row["payload"] == {"figure_id": "fig_1", "deployment_id": "dep_1"}  # reference-only
    assert row["delivery_status"] == "retrying"


async def test_successful_delivery_signs_and_marks_delivered(client, conn, tenant) -> None:
    key = tenant["key"]
    secret = "topsecret"
    wid = await _webhook(client, key, secret=secret)
    wd.verify_webhook(wid, _ws_id(conn), FakeSender([200]))
    wd.emit(_ws_id(conn), "figure.updated", {"figure_id": "fig_9"})
    sender = FakeSender([200])
    counts = wd.deliver_once(sender)
    assert counts["delivered"] == 1
    # The signature the consumer would verify with THEIR secret.
    call = sender.calls[0]
    ts = int(call["headers"]["X-TrueFigure-Timestamp"])
    assert wd.verify_signature(secret, ts, call["body"], call["headers"]["X-TrueFigure-Signature"])
    st = conn.execute("SELECT delivery_status, last_http_status FROM webhook_deliveries").fetchone()
    assert st["delivery_status"] == "delivered" and st["last_http_status"] == 200


async def test_retry_schedule_then_drop_and_suspend(client, conn, tenant) -> None:
    key = tenant["key"]
    wid = await _webhook(client, key)
    wd.verify_webhook(wid, _ws_id(conn), FakeSender([200]))
    wd.emit(_ws_id(conn), "figure.updated", {"figure_id": "fig_x"})
    sender = FakeSender([500])  # always fails

    now = datetime(2026, 12, 1, 12, 0, 0, tzinfo=UTC)
    # Attempt 1 -> retried, next at +60s
    assert wd.deliver_once(sender, now=now)["retried"] == 1
    d = conn.execute("SELECT attempt_count, next_retry_at, delivery_status FROM webhook_deliveries").fetchone()
    assert d["attempt_count"] == 1 and d["delivery_status"] == "retrying"
    assert d["next_retry_at"] == now + timedelta(seconds=wd.RETRY_SCHEDULE[1])

    # Drive attempts 2..6; each advances only when due.
    for i in range(2, wd.MAX_ATTEMPTS + 1):
        due = conn.execute("SELECT next_retry_at FROM webhook_deliveries").fetchone()["next_retry_at"]
        out = wd.deliver_once(sender, now=due)
        if i < wd.MAX_ATTEMPTS:
            assert out["retried"] == 1
        else:
            assert out["dropped"] == 1
    final = conn.execute("SELECT attempt_count, delivery_status FROM webhook_deliveries").fetchone()
    assert final["attempt_count"] == wd.MAX_ATTEMPTS and final["delivery_status"] == "dropped"
    # 7 days of total failure -> webhook suspended.
    assert conn.execute("SELECT webhook_status FROM webhooks WHERE webhook_ref=%s", (wid,)).fetchone()["webhook_status"] == "suspended"


async def test_not_yet_due_delivery_is_skipped(client, conn, tenant) -> None:
    key = tenant["key"]
    wid = await _webhook(client, key)
    wd.verify_webhook(wid, _ws_id(conn), FakeSender([200]))
    wd.emit(_ws_id(conn), "figure.updated", {"figure_id": "f"})
    sender = FakeSender([500])
    now = datetime(2026, 12, 1, 12, 0, 0, tzinfo=UTC)
    wd.deliver_once(sender, now=now)  # attempt 1 -> schedules +60s
    # Before the next slot, nothing is due.
    out = wd.deliver_once(sender, now=now + timedelta(seconds=30))
    assert out == {"delivered": 0, "retried": 0, "dropped": 0}


async def test_delivery_log_endpoint(client, conn, tenant) -> None:
    key = tenant["key"]
    wid = await _webhook(client, key)
    wd.verify_webhook(wid, _ws_id(conn), FakeSender([200]))
    wd.emit(_ws_id(conn), "figure.updated", {"figure_id": "f"})
    wd.deliver_once(FakeSender([200]))
    r = await client.get(f"/v1/webhooks/{wid}/deliveries", headers=auth(key))
    items = r.json()["data"]["results"]
    assert len(items) == 1 and items[0]["status"] == "delivered"
    assert items[0]["event_type"] == "figure.updated"


async def test_delivery_log_unknown_webhook(client, tenant) -> None:
    r = await client.get("/v1/webhooks/wh_nope/deliveries", headers=auth(tenant["key"]))
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "TF-READ-001"


async def test_transport_exception_is_retryable(client, conn, tenant) -> None:
    key = tenant["key"]
    wid = await _webhook(client, key)
    wd.verify_webhook(wid, _ws_id(conn), FakeSender([200]))
    wd.emit(_ws_id(conn), "figure.updated", {"figure_id": "f"})

    def boom(method, url, headers, body):
        raise RuntimeError("connection reset")

    out = wd.deliver_once(boom, now=datetime(2026, 12, 1, tzinfo=UTC))
    assert out["retried"] == 1
