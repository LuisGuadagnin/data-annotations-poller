# Task Watcher

Watches DataAnnotation's projects page and emails you the moment a **new
project** appears in the table.

It reuses your existing **logged-in browser session** via a cookie you paste in
once — so you handle login + 2FA yourself in the browser, and no password ever
lives in the script.

> **Why paste a cookie?** Modern Chrome (v127+) encrypts its cookie store so
> outside tools can't read it automatically. Pasting the cookie sidesteps that
> cleanly.

---

## Setup steps

### 1. Install dependencies
```bash
python.exe -m pip install -r requirements.txt
```
(`python.exe` is your Windows Python. Any Python 3 with these packages works.)

### 2. Set up the Gmail App Password
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
3. Set it as an environment variable (so it isn't saved in the file):
   ```powershell
   setx GMAIL_APP_PASSWORD "your16charpassword"
   ```
   Reopen the terminal after `setx` so it takes effect.

The `GMAIL_ADDRESS` / `ALERT_RECIPIENT` in the script are already set to your
Gmail — change them if you want alerts sent elsewhere.

### 3. Add your session cookie  →  `cookie.txt`
1. Log in to https://app.dataannotation.tech in **Chrome** (do your 2FA).
2. Go to the projects page, press **F12**, open the **Network** tab.
3. Check the **Doc** (or **All**) filter and **reload** the page (F5).
4. Click the top request named **`projects`**.
5. Right-click it → **Copy** → **Copy as cURL (bash)**.
6. Paste that into a file named **`cookie.txt`** next to the script. Save.

That's it — the script pulls the cookie out of the cURL text automatically.
(You can also paste just a raw `name=value; name2=value2` cookie string if you
prefer.)

### 4. Run it
```bash
python.exe task_watcher.py
```

Keep the terminal open. The **first run** records what's already listed (so you
aren't spammed about existing projects). After that, any new project triggers an
email within ~60 seconds.

### Test the email setup
To confirm Gmail works without waiting for a real new project, send a sample
alert and exit:
```bash
python.exe task_watcher.py --test-email
```
Check your inbox (and spam folder) for a "TEST — sample alert" message.

---

## When the session expires

If you see `Session expired. Re-copy your cookie…`, just:
1. Make sure you're still logged in at app.dataannotation.tech in Chrome.
2. Redo step 3 (copy as cURL → overwrite `cookie.txt`).

No need to restart the script — it re-reads `cookie.txt` on every check.

---

## How it works

1. Reads your session cookie from `cookie.txt`.
2. Fetches the projects page as if it were your browser.
3. Parses the table — each project's name cell (`td[data-column-id="name"]`).
4. Compares against `seen_projects.json` (auto-created).
5. Emails you about anything new, then waits and repeats.

## Notes
- Default check interval is 60s (`CHECK_INTERVAL_SECONDS` in the script). Don't
  go too low — hammering the site is impolite. 30s is a reasonable floor.
- Delete `seen_projects.json` to reset the "seen" memory.
- `cookie.txt` contains a live session — keep it private, don't share it.
- Stop the script with `Ctrl+C`.
