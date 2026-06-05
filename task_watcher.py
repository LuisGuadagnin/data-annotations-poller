#!/usr/bin/env python3
"""
task_watcher.py
---------------
Watches a (login-protected) task website and emails you the moment a new
project row appears in its table.

How it avoids dealing with 2FA:
  You log in once in your normal Chrome browser (you handle the 2FA yourself),
  then paste your session cookie into `cookie.txt` (see README.md). This script
  reuses that cookie to fetch the page, so no password or 2FA touches it.

  (Modern Chrome encrypts its cookie store so tools can't auto-read it, which is
  why we paste the cookie instead of reading it automatically.)
"""

import os
import re
import sys
import time
import json
import smtplib
import argparse
import traceback
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================================
# CONFIG  --  edit these values
# ============================================================================

# The page that shows the table of available projects (the one you'd refresh).
TABLE_URL = "https://app.dataannotation.tech/workers/projects"

# Your logged-in session cookie lives in this file (next to the script).
# Paste either a "Copy as cURL" command or a raw cookie string into it.
# See README.md for the 30-second steps. Refresh it if the session expires.
COOKIE_FILE = Path(__file__).with_name("cookie.txt")

# How often to check, in seconds.
CHECK_INTERVAL_SECONDS = 60

# Where to remember which projects we've already seen (so we only alert on NEW
# ones). Stored next to this script.
STATE_FILE = Path(__file__).with_name("seen_projects.json")

# ---- Email settings (Gmail) ----
# Generate a Gmail "App Password" (needs 2-step verification ON):
#   https://myaccount.google.com/apppasswords
# For safety, set the app password as an environment variable instead of
# pasting it here:  set GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
GMAIL_ADDRESS = "you@example.com"            # the Gmail you send FROM
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_RECIPIENT = "you@example.com"          # where the alert is sent TO

# A normal browser User-Agent makes the request look like your browser.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ============================================================================
# CORE LOGIC
# ============================================================================

def extract_cookie_header(raw):
    """
    Turn the contents of cookie.txt into a Cookie header value.

    Accepts either:
      - a "Copy as cURL" command (we pull the cookie out of it), or
      - a raw cookie string like "name1=val1; name2=val2".
    """
    raw = raw.strip()
    if not raw:
        return ""

    # If it's a curl command, dig the cookie out of it.
    if "curl" in raw.lower() and ("-H" in raw or "-b" in raw):
        patterns = [
            r"-H\s+'\s*cookie:\s*(.*?)'",     # -H 'cookie: ...'   (bash)
            r'-H\s+"\s*cookie:\s*(.*?)"',     # -H "cookie: ..."   (cmd/pwsh)
            r"-b\s+'(.*?)'",                   # -b '...'
            r'-b\s+"(.*?)"',                   # -b "..."
        ]
        for pat in patterns:
            m = re.search(pat, raw, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(1).strip()
        return ""  # curl command but no cookie found

    # Otherwise treat the whole file as a raw cookie header value.
    return raw


def load_cookie_header():
    """Read and parse cookie.txt. Returns the cookie string (or '')."""
    if not COOKIE_FILE.exists():
        return ""
    return extract_cookie_header(COOKIE_FILE.read_text(encoding="utf-8"))


def fetch_table_page(cookie_header):
    """
    Fetch the project page using the pasted session cookie.

    Returns (html, final_url). final_url tells us if we were redirected to a
    login page (i.e. the session/cookie expired).
    """
    resp = requests.get(
        TABLE_URL,
        headers={"User-Agent": USER_AGENT, "Cookie": cookie_header},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text, resp.url


def looks_logged_out(final_url):
    """If we didn't end up on the projects page, the session likely expired."""
    return "/workers/projects" not in final_url


# The table is "hybrid": the server embeds the project data as JSON in this
# div's data-props attribute, then JavaScript builds the visible table from it.
PROJECTS_ROOT_ID = "workers/WorkerProjectsTable-hybrid-root"

# Buckets inside dashboardMerchTargeting that hold available projects.
PROJECT_BUCKETS = ("projects", "easyProjects", "surveyProjects")


def parse_projects(html):
    """
    Turn the page HTML into a list of project dicts.

    Returns: list of {"id", "title", "pay", "tasks"}.

    The data lives as JSON in the WorkerProjectsTable root's data-props, under
    dashboardMerchTargeting.{projects, easyProjects, surveyProjects}. We dedupe
    by the project's UUID.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id=PROJECTS_ROOT_ID)
    if root is None or not root.get("data-props"):
        return []

    try:
        props = json.loads(root.get("data-props"))
    except (ValueError, TypeError):
        return []

    merch = props.get("dashboardMerchTargeting") or {}

    projects = []
    seen_local = set()
    for bucket in PROJECT_BUCKETS:
        for p in merch.get(bucket) or []:
            pid = p.get("id") or p.get("name")
            if not pid or pid in seen_local:
                continue
            seen_local.add(pid)
            projects.append({
                "id": pid,
                "title": p.get("name", "(unnamed project)"),
                "pay": p.get("pay", ""),
                "tasks": p.get("availableTasksFor", ""),
            })

    return projects


def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen(seen_ids):
    STATE_FILE.write_text(json.dumps(sorted(seen_ids), indent=2))


def send_email(new_projects):
    """Email an alert listing the new projects."""
    if not GMAIL_APP_PASSWORD:
        print("!! GMAIL_APP_PASSWORD not set; cannot send email.", flush=True)
        return

    lines = []
    for p in new_projects:
        extra = []
        if p.get("pay"):
            extra.append(p["pay"])
        if p.get("tasks"):
            extra.append(f"{p['tasks']} task(s)")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        lines.append(f"- {p['title']}{suffix}")

    body = (
        f"{len(new_projects)} new project(s) just appeared:\n\n"
        + "\n".join(lines)
        + f"\n\nGo grab them: {TABLE_URL}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = f"🚨 {len(new_projects)} new task(s) available!"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ALERT_RECIPIENT
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)

    print(f"   -> emailed alert for {len(new_projects)} new project(s).", flush=True)


def run_test_email():
    """Send one sample alert so you can confirm the Gmail side works."""
    print(f"Sending a test alert to {ALERT_RECIPIENT} ...", flush=True)

    if not GMAIL_APP_PASSWORD:
        print("!! GMAIL_APP_PASSWORD is not set. Set it first (see README), "
              "then re-run.", flush=True)
        return

    sample = [{
        "id": "test-0000",
        "title": "TEST — sample alert from task_watcher (ignore me)",
        "pay": "$75.00/hr",
        "tasks": "3",
    }]
    try:
        send_email(sample)
        print(f"Sent. Check the inbox of {ALERT_RECIPIENT} "
              "(and the spam folder, just in case).", flush=True)
    except Exception:
        print("!! Failed to send the test email:", flush=True)
        traceback.print_exc()


def check_once(seen_ids, first_run):
    """Do a single fetch+compare. Returns the updated seen set."""
    cookie_header = load_cookie_header()
    if not cookie_header:
        print("   !! No cookie found in cookie.txt. See README.md to add one.",
              flush=True)
        return seen_ids

    html, final_url = fetch_table_page(cookie_header)

    if looks_logged_out(final_url):
        print("   !! Session expired. Re-copy your cookie into cookie.txt "
              "(log in again in Chrome first).", flush=True)
        return seen_ids

    projects = parse_projects(html)
    current_ids = {p["id"] for p in projects}

    new_projects = [p for p in projects if p["id"] not in seen_ids]

    if first_run:
        # On the very first run we just record what's there now, so we don't
        # email you about every existing project.
        print(f"   baseline: {len(current_ids)} project(s) currently listed.",
              flush=True)
    elif new_projects:
        print(f"   NEW: {len(new_projects)} project(s) found!", flush=True)
        send_email(new_projects)
    else:
        print(f"   no change ({len(current_ids)} listed).", flush=True)

    # Remember everything we've seen.
    return seen_ids | current_ids


def main():
    print("task_watcher started. Press Ctrl+C to stop.", flush=True)
    print(f"Watching: {TABLE_URL}", flush=True)
    print(f"Checking every {CHECK_INTERVAL_SECONDS}s.", flush=True)

    if not load_cookie_header():
        print("\n!! cookie.txt is missing or empty.", flush=True)
        print("   Add your session cookie (see README.md) and I'll start "
              "checking on the next cycle.", flush=True)
    print("", flush=True)

    seen_ids = load_seen()
    first_run = len(seen_ids) == 0

    while True:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] checking...", flush=True)
        try:
            seen_ids = check_once(seen_ids, first_run)
            save_seen(seen_ids)
            first_run = False
        except Exception:
            print("   !! error during check:", flush=True)
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Watch DataAnnotation for new projects and email alerts."
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send one sample alert email and exit (verifies Gmail setup).",
    )
    args = parser.parse_args()

    if args.test_email:
        run_test_email()
        sys.exit(0)

    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        sys.exit(0)
