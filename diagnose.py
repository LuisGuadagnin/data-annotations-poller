"""
Diagnostic: dump the WorkerProjectsTable data-props JSON (minus noise) so we can
see exactly where/how the project list is stored.

Run:  python.exe diagnose.py
Then paste the printed JSON back.
"""
import json
from bs4 import BeautifulSoup
from task_watcher import load_cookie_header, fetch_table_page

cookie = load_cookie_header()
if not cookie:
    print("No cookie found in cookie.txt — add it first (see README).")
    raise SystemExit(1)

html, _ = fetch_table_page(cookie)
soup = BeautifulSoup(html, "html.parser")

target = soup.find(id="workers/WorkerProjectsTable-hybrid-root")
if target is None:
    print("Could not find the WorkerProjectsTable root.")
    raise SystemExit(1)

props = json.loads(target.get("data-props"))

# Drop the big noisy blobs so the rest is readable.
for noisy in ("session", "bugReportContext"):
    props.pop(noisy, None)

text = json.dumps(props, indent=2, ensure_ascii=False)

# Safety cap so we don't flood the terminal; the useful part is small.
if len(text) > 8000:
    text = text[:8000] + "\n…(truncated)…"

print(text)
