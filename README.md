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
- **SSM Parameter Store (SecureString)** holds the session cookie — update it
  without redeploying.
- **SES** sends the alert emails from a verified sender identity.
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
- An **email address you control** to send alerts from (you'll verify it in SES).

---

## One-time setup

### 1. Store the session cookie in SSM

This value never touches git or the CDK source.

```bash
# See "Getting the cookie" below for how to produce cookie.txt
aws ssm put-parameter --name "/task-watcher/cookie" --type SecureString \
  --value "$(cat cookie.txt)"
```

### 2. Deploy the infrastructure

```bash
cd cdk
npm install
npx cdk bootstrap     # first time per account/region only
npx cdk deploy -c senderEmail=you@example.com
```

The output prints the function name, table name, and the sender email.

> `senderEmail` (the address alerts are sent **from**) is required — pass it with
> `-c senderEmail=...` or the `SENDER_EMAIL` env var. Alerts are sent **to** the
> same address unless you override with `-c recipientEmail=other@example.com`
> (or the `RECIPIENT_EMAIL` env var).

### 3. Verify the SES identity

Deploying creates an SES email identity for your sender address. SES emails that
address a verification link — **click it** before alerts can be sent. (If sender
and recipient differ, verify the recipient too: SES starts in the *sandbox*, which
only delivers to verified addresses. Sending to yourself needs no production-access
request.)

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
# Send a sample alert to confirm SES works
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
- `cdk destroy` tears everything down. The cookie SSM parameter is created
  out-of-band, so delete it separately if you want it gone.
