#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { TaskWatcherStack } from "../lib/task-watcher-stack";

const app = new cdk.App();

// Read from `cdk deploy -c key=value` first, then an env var, else undefined.
const fromContext = (key: string, envVar: string): string | undefined =>
  app.node.tryGetContext(key) ?? process.env[envVar];

const required = (key: string, envVar: string): string => {
  const value = fromContext(key, envVar);
  if (!value) {
    throw new Error(
      `Missing ${key}. Provide it with \`cdk deploy -c ${key}=you@example.com\` ` +
        `or set the ${envVar} environment variable.`
    );
  }
  return value;
};

const gmailAddress = required("gmailAddress", "GMAIL_ADDRESS");

new TaskWatcherStack(app, "TaskWatcherStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  // Override any of these via `cdk deploy -c key=value` if you like.
  cookieParamName: fromContext("cookieParamName", "COOKIE_PARAM_NAME") ?? "/task-watcher/cookie",
  gmailParamName:
    fromContext("gmailParamName", "GMAIL_PARAM_NAME") ?? "/task-watcher/gmail-app-password",
  gmailAddress,
  // Defaults to the sender if a separate recipient isn't given.
  alertRecipient: fromContext("alertRecipient", "ALERT_RECIPIENT") ?? gmailAddress,
  seenTtlDays: fromContext("seenTtlDays", "SEEN_TTL_DAYS") ?? "7",
});
