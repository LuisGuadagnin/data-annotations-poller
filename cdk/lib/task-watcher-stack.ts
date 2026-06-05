import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as scheduler from "aws-cdk-lib/aws-scheduler";
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha";
import * as path from "path";

export interface TaskWatcherStackProps extends cdk.StackProps {
  /** SSM SecureString parameter holding the session cookie. */
  readonly cookieParamName: string;
  /** SSM SecureString parameter holding the Gmail app password. */
  readonly gmailParamName: string;
  /** Gmail address alerts are sent FROM. */
  readonly gmailAddress: string;
  /** Address alerts are sent TO. */
  readonly alertRecipient: string;
  /** How many days to remember a seen project (DynamoDB TTL). */
  readonly seenTtlDays: string;
}

export class TaskWatcherStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: TaskWatcherStackProps) {
    super(scope, id, props);

    // ---- Seen-projects + cookie-status store ----
    const table = new dynamodb.Table(this, "SeenProjects", {
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.DESTROY, // regenerable cache
    });

    // ---- The checker function (one check per invocation) ----
    const logGroup = new logs.LogGroup(this, "WatcherLogs", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const fn = new PythonFunction(this, "Watcher", {
      entry: path.join(__dirname, "..", "..", "lambda"),
      index: "handler.py",
      handler: "handler",
      runtime: lambda.Runtime.PYTHON_3_12,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      reservedConcurrentExecutions: 1, // never overlap ticks
      logGroup,
      environment: {
        TABLE_NAME: table.tableName,
        COOKIE_PARAM_NAME: props.cookieParamName,
        GMAIL_PARAM_NAME: props.gmailParamName,
        GMAIL_ADDRESS: props.gmailAddress,
        ALERT_RECIPIENT: props.alertRecipient,
        SEEN_TTL_DAYS: props.seenTtlDays,
      },
    });

    table.grantReadWriteData(fn);

    // ---- Read access to the two SecureString parameters ----
    // The parameters are created out-of-band (CloudFormation can't make
    // SecureString values), so we only grant read here.
    const paramArn = (name: string) =>
      cdk.Stack.of(this).formatArn({
        service: "ssm",
        resource: "parameter",
        // ARN merges the parameter's own leading slash, so strip it here.
        resourceName: name.replace(/^\//, ""),
      });

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameter"],
        resources: [paramArn(props.cookieParamName), paramArn(props.gmailParamName)],
      })
    );

    // Decrypt SecureStrings sealed with the AWS-managed SSM key.
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["kms:Decrypt"],
        resources: ["*"],
        conditions: {
          StringEquals: { "kms:ViaService": `ssm.${this.region}.amazonaws.com` },
        },
      })
    );

    // ---- EventBridge Scheduler: invoke the function every minute ----
    const schedulerRole = new iam.Role(this, "SchedulerRole", {
      assumedBy: new iam.ServicePrincipal("scheduler.amazonaws.com"),
    });
    fn.grantInvoke(schedulerRole);

    new scheduler.CfnSchedule(this, "EveryMinute", {
      flexibleTimeWindow: { mode: "OFF" },
      scheduleExpression: "rate(1 minute)",
      target: {
        arn: fn.functionArn,
        roleArn: schedulerRole.roleArn,
      },
    });

    // ---- Handy outputs ----
    new cdk.CfnOutput(this, "FunctionName", { value: fn.functionName });
    new cdk.CfnOutput(this, "TableName", { value: table.tableName });
    new cdk.CfnOutput(this, "CookieParamName", { value: props.cookieParamName });
    new cdk.CfnOutput(this, "GmailParamName", { value: props.gmailParamName });
  }
}
