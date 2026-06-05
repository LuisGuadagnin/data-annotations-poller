# Task Watcher (AWS)

Watches DataAnnotation's projects page and emails you the moment a **new
project** appears — running unattended in the cloud, so you don't have to keep a
machine on.

It reuses your existing **logged-in browser session** via a cookie you paste in
once (you handle login + 2FA yourself in the browser, no password lives here).

## Architecture

- **Lambda** (`lambda/`) runs a single check per invocation.
- **EventBridge Scheduler** triggers it every minute.
- **DynamoDB** remembers which projects we've already seen (with a TTL so it
  self-prunes) and a one-shot "cookie expired" flag.
- **SSM Parameter Store (SecureString)** holds the session cookie and the Gmail
  app password — update them without redeploying.
- **AWS CDK (TypeScript)** in `cdk/` provisions all of it.

When the session cookie expires you get **one** email; checks resume
automatically once you refresh the cookie parameter.

```
lambda/   handler.py (entry), watcher.py (fetch/parse/email), requirements.txt
cdk/      bin/app.ts, lib/task-watcher-stack.ts
```

---

## Prerequisites

- An AWS account with the **AWS CLI configured** (`aws configure`).
- **Node.js 18+** and **Docker running** (CDK bundles the Python deps in Docker
  at deploy time).
- A **Gmail App Password** (needs 2-Step Verification on):
  https://myaccount.google.com/apppasswords

---

## One-time setup

### 1. Store your secrets in SSM

These values never touch git or the CDK source.

```bash
# Session cookie — see "Getting the cookie" below for how to produce cookie.txt
aws ssm put-parameter --name "/task-watcher/cookie" --type SecureString \
  --value "$(cat cookie.txt)"

# Gmail app password
aws ssm put-parameter --name "/task-watcher/gmail-app-password" --type SecureString \
  --value "your16charapppassword"
```

### 2. Deploy the infrastructure

```bash
cd cdk
npm install
npx cdk bootstrap     # first time per account/region only
npx cdk deploy -c gmailAddress=you@example.com
```

The output prints the function name, table name, and the two SSM parameter names.

> `gmailAddress` (the address alerts are sent **from**) is required — pass it with
> `-c gmailAddress=...` or the `GMAIL_ADDRESS` env var. Alerts are sent **to** the
> same address unless you override with `-c alertRecipient=other@example.com`
> (or the `ALERT_RECIPIENT` env var).

---

## Getting the cookie (`cookie.txt`)

1. Log in to https://app.dataannotation.tech in **Chrome** (do your 2FA).
2. Open the projects page, press **F12**, open the **Network** tab.
3. Filter to **Doc** (or **All**) and **reload** (F5).
4. Click the top request named **`projects`**.
5. Right-click → **Copy** → **Copy as cURL (bash)**.
6. Paste into a file named **`cookie.txt`**. Save.

The handler pulls the cookie out of the cURL text automatically (a raw
`name=value; name2=value2` string also works).

---

## When the session expires

You'll get a one-time "session cookie expired" email. To fix it, redo the cookie
steps above, then overwrite the parameter (no redeploy needed):

```bash
aws ssm put-parameter --name "/task-watcher/cookie" --type SecureString \
  --value "$(cat cookie.txt)" --overwrite
```

The next scheduled run picks it up and re-arms the expiry alert.

---

## Verifying it works

```bash
# Send a sample alert to confirm Gmail works
aws lambda invoke --function-name <FunctionName> \
  --payload '{"action":"test-email"}' /dev/stdout

# Force a normal check now (instead of waiting for the schedule)
aws lambda invoke --function-name <FunctionName> --payload '{}' /dev/stdout
```

The **first real run** records a baseline of currently-listed projects (no email)
so you aren't spammed about existing ones. After that, any new project triggers an
email within ~60 seconds. Check the function's **CloudWatch Logs** for per-run
summaries.

---

## Notes

- Default check interval is every minute (`rate(1 minute)` in the stack). Don't go
  lower — hammering the site is impolite.
- Seen projects expire from DynamoDB after `SEEN_TTL_DAYS` (default 7), so the
  table stays small.
- `cdk destroy` tears everything down. The SSM parameters are created out-of-band,
  so delete them separately if you want them gone.
