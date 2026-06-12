import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as scheduler from "aws-cdk-lib/aws-scheduler";
import * as ses from "aws-cdk-lib/aws-ses";
import { PythonFunction } from "@aws-cdk/aws-lambda-python-alpha";
import * as path from "path";

export interface TaskWatcherStackProps extends cdk.StackProps {
  /** SSM SecureString parameter holding the session cookie. */
  readonly cookieParamName: string;
  /** Email address alerts are sent FROM; its domain is verified in SES. */
  readonly senderEmail: string;
  /** Route 53 public hosted zone ID for the sender's domain. */
  readonly hostedZoneId: string;
  /** Email address alerts are sent TO. */
  readonly recipientEmail: string;
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
        SENDER_EMAIL: props.senderEmail,
        RECIPIENT_EMAIL: props.recipientEmail,
        SEEN_TTL_DAYS: props.seenTtlDays,
      },
    });

    table.grantReadWriteData(fn);

    // ---- Read access to the cookie SecureString parameter ----
    // The parameter is created out-of-band (CloudFormation can't make
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
        resources: [paramArn(props.cookieParamName)],
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

    // ---- SES: verify the sending DOMAIN (Easy DKIM + custom MAIL FROM) ----
    // Sending FROM a domain we control (not a freemail @gmail.com address) is
    // what makes alerts pass DKIM/SPF/DMARC and land in the inbox instead of
    // spam. Because the zone is in Route 53, CDK writes every record for us:
    //   - Identity.publicHostedZone -> 3 Easy-DKIM CNAMEs (DKIM alignment)
    //   - mailFromDomain            -> MAIL FROM MX + SPF TXT (SPF alignment)
    // The domain verifies automatically once these propagate (no link to click).
    const senderDomain = props.senderEmail.split("@")[1];
    const zone = route53.PublicHostedZone.fromPublicHostedZoneAttributes(
      this,
      "SenderZone",
      { hostedZoneId: props.hostedZoneId, zoneName: senderDomain }
    );

    new ses.EmailIdentity(this, "SenderDomainIdentity", {
      identity: ses.Identity.publicHostedZone(zone),
      mailFromDomain: `mail.${senderDomain}`,
    });

    // DMARC record — CDK doesn't create this. p=none is enough to satisfy
    // alignment (DKIM/SPF already pass), without quarantining anything.
    new route53.TxtRecord(this, "DmarcRecord", {
      zone,
      recordName: `_dmarc.${senderDomain}`,
      values: ["v=DMARC1; p=none;"],
    });

    const sesIdentityArn = (name: string) =>
      cdk.Stack.of(this).formatArn({
        service: "ses",
        resource: "identity",
        resourceName: name,
      });

    // ses:SendEmail must be allowed on the sending DOMAIN identity (the FROM)...
    const sendEmailResources = [sesIdentityArn(senderDomain)];

    // ...and, in the SES sandbox, ALSO on the RECIPIENT's identity: the sandbox
    // requires the recipient be a verified identity, and SES authorizes the send
    // against it too. The gmail address was verified by the original stack under
    // this same logical ID ("SenderIdentity"); we keep that ID stable so
    // CloudFormation treats it as unchanged rather than replacing it. Both the
    // identity and the extra grant are skipped when the recipient already lives
    // on the verified sending domain (covered by the domain identity above).
    if (props.recipientEmail.split("@")[1] !== senderDomain) {
      new ses.EmailIdentity(this, "SenderIdentity", {
        identity: ses.Identity.email(props.recipientEmail),
      });
      sendEmailResources.push(sesIdentityArn(props.recipientEmail));
    }

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ses:SendEmail"],
        resources: sendEmailResources,
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
    new cdk.CfnOutput(this, "SenderEmail", { value: props.senderEmail });
  }
}
