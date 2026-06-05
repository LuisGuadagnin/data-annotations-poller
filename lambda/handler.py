"""
handler.py
----------
Lambda entry point. Runs a single check per invocation (the EventBridge
Scheduler is the "loop"). Wires SSM (cookie + Gmail password) and DynamoDB
(seen projects + cookie-status flag) around the pure logic in watcher.py.

Config comes from environment variables set by the CDK stack:
  TABLE_NAME, COOKIE_PARAM_NAME, GMAIL_PARAM_NAME,
  GMAIL_ADDRESS, ALERT_RECIPIENT, SEEN_TTL_DAYS (default 7)
"""

import os
import time

import boto3
from botocore.exceptions import ClientError

import watcher


TABLE_NAME = os.environ["TABLE_NAME"]
COOKIE_PARAM_NAME = os.environ["COOKIE_PARAM_NAME"]
GMAIL_PARAM_NAME = os.environ["GMAIL_PARAM_NAME"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
ALERT_RECIPIENT = os.environ["ALERT_RECIPIENT"]
SEEN_TTL_DAYS = int(os.environ.get("SEEN_TTL_DAYS", "7"))

# Reserved partition keys (project IDs are UUIDs, so these never collide).
STATUS_PK = "__cookie_status__"
INIT_PK = "__initialized__"

_ssm = boto3.client("ssm")
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _get_secret(name):
    """Read a SecureString SSM parameter, decrypted."""
    resp = _ssm.get_parameter(Name=name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _item_exists(pk):
    return "Item" in _table.get_item(Key={"pk": pk})


def _put_if_new(project):
    """
    Conditionally record a project. Returns True if it was new (and thus
    written), False if we'd already seen it.
    """
    ttl = int(time.time()) + SEEN_TTL_DAYS * 86400
    try:
        _table.put_item(
            Item={
                "pk": project["id"],
                "title": project.get("title", ""),
                "ttl": ttl,
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _mark_cookie_alerted():
    _table.put_item(Item={"pk": STATUS_PK, "alerted": True})


def _cookie_already_alerted():
    resp = _table.get_item(Key={"pk": STATUS_PK})
    return bool(resp.get("Item", {}).get("alerted"))


def _clear_cookie_alert():
    """Idempotent: drop the expiry flag so a future expiry re-alerts."""
    _table.delete_item(Key={"pk": STATUS_PK})


def _send_test_email():
    sample = [{
        "id": "test-0000",
        "title": "TEST — sample alert from task watcher (ignore me)",
        "pay": "$75.00/hr",
        "tasks": "3",
    }]
    watcher.send_email(sample, GMAIL_ADDRESS, ALERT_RECIPIENT,
                       _get_secret(GMAIL_PARAM_NAME))
    print(f"Sent test alert to {ALERT_RECIPIENT}.", flush=True)


def handler(event, context):
    event = event or {}

    # Manual test path: `aws lambda invoke --payload '{"action":"test-email"}'`.
    if event.get("action") == "test-email":
        _send_test_email()
        return {"ok": True, "action": "test-email"}

    cookie_header = watcher.extract_cookie_header(_get_secret(COOKIE_PARAM_NAME))
    if not cookie_header:
        print("!! No cookie in SSM; nothing to check.", flush=True)
        return {"ok": False, "reason": "no-cookie"}

    html, final_url = watcher.fetch_table_page(cookie_header)

    if watcher.looks_logged_out(final_url):
        if _cookie_already_alerted():
            print("Session still expired; already alerted.", flush=True)
        else:
            watcher.send_cookie_expired_email(
                GMAIL_ADDRESS, ALERT_RECIPIENT, _get_secret(GMAIL_PARAM_NAME))
            _mark_cookie_alerted()
            print("Session expired; sent one-time alert.", flush=True)
        return {"ok": False, "reason": "cookie-expired"}

    # Cookie is healthy — re-arm the expiry alert for next time.
    _clear_cookie_alert()

    projects = watcher.parse_projects(html)
    current_ids = {p["id"] for p in projects}

    # First ever run: record a baseline so we don't alert on pre-existing
    # projects, then stop.
    if not _item_exists(INIT_PK):
        for p in projects:
            _put_if_new(p)
        _table.put_item(Item={"pk": INIT_PK, "initialized": True})
        print(f"Baseline recorded: {len(current_ids)} project(s).", flush=True)
        return {"ok": True, "baseline": len(current_ids)}

    new_projects = [p for p in projects if _put_if_new(p)]

    if new_projects:
        watcher.send_email(new_projects, GMAIL_ADDRESS, ALERT_RECIPIENT,
                           _get_secret(GMAIL_PARAM_NAME))
        print(f"NEW: emailed alert for {len(new_projects)} project(s).",
              flush=True)
    else:
        print(f"No change ({len(current_ids)} listed).", flush=True)

    return {"ok": True, "new": len(new_projects), "listed": len(current_ids)}
