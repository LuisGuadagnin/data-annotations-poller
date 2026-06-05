"""
watcher.py
----------
Pure logic for fetching and parsing the DataAnnotation projects page, plus the
email senders. No file or AWS state lives here — callers (the Lambda handler)
supply the cookie, credentials, and persistence.

Ported from the original local `task_watcher.py`.
"""

import re
import json

import requests
from bs4 import BeautifulSoup


# The page that shows the table of available projects.
TABLE_URL = "https://app.dataannotation.tech/workers/projects"

# A normal browser User-Agent makes the request look like a real browser.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# The table is "hybrid": the server embeds the project data as JSON in this
# div's data-props attribute, then JavaScript builds the visible table from it.
PROJECTS_ROOT_ID = "workers/WorkerProjectsTable-hybrid-root"

# Buckets inside dashboardMerchTargeting that hold available projects.
PROJECT_BUCKETS = ("projects", "easyProjects", "surveyProjects")


def extract_cookie_header(raw):
    """
    Turn the stored cookie value into a Cookie header string.

    Accepts either:
      - a "Copy as cURL" command (we pull the cookie out of it), or
      - a raw cookie string like "name1=val1; name2=val2".
    """
    raw = (raw or "").strip()
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

    # Otherwise treat the whole value as a raw cookie header value.
    return raw


def fetch_table_page(cookie_header):
    """
    Fetch the project page using the session cookie.

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


def build_email_body(new_projects):
    """Build the plain-text body of a new-projects alert."""
    lines = []
    for p in new_projects:
        extra = []
        if p.get("pay"):
            extra.append(p["pay"])
        if p.get("tasks"):
            extra.append(f"{p['tasks']} task(s)")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        lines.append(f"- {p['title']}{suffix}")

    return (
        f"{len(new_projects)} new project(s) just appeared:\n\n"
        + "\n".join(lines)
        + f"\n\nGo grab them: {TABLE_URL}\n"
    )


def new_projects_email(new_projects):
    """Return (subject, body) for a new-projects alert."""
    subject = f"🚨 {len(new_projects)} new task(s) available!"
    return subject, build_email_body(new_projects)


def cookie_expired_email():
    """Return (subject, body) telling you to refresh the session cookie in SSM."""
    subject = "⚠️ Task watcher: session cookie expired"
    body = (
        "Your DataAnnotation session cookie has expired, so the watcher can no "
        "longer check for new projects.\n\n"
        "To fix it:\n"
        "  1. Log in to https://app.dataannotation.tech in Chrome (do your 2FA).\n"
        "  2. Open the projects page, copy the request as cURL (see README).\n"
        "  3. Update the SSM parameter:\n"
        '       aws ssm put-parameter --name "/task-watcher/cookie" '
        '--type SecureString --value "$(cat cookie.txt)" --overwrite\n\n'
        "You will get this alert only once per expiry; checks resume "
        "automatically once the cookie is valid again.\n"
    )
    return subject, body
