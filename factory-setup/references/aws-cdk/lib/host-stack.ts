import { readFileSync } from 'node:fs';
import path from 'node:path';
import { CfnOutput, CfnResource, CustomResource, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Asset } from 'aws-cdk-lib/aws-s3-assets';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import { renderWorkerContext } from './bundle';
import { SessionScope } from './functions/token-vending';
import { checkWorkerTimeouts, scheduledWorkers, schedulerCron } from './schedule';
import {
  FactorySettings,
  RUNS_PREFIX,
  STATE_PREFIX,
  githubRepositories,
  pagesBucketName,
} from './settings';

/** The managed MicroVM base image, pinned so a base image release does not rebuild the worker. */
const BASE_IMAGE = 'al2023-1';
const BASE_IMAGE_VERSION = '2';

/** The port worker.py serves its lifecycle hooks on. */
const HOOK_PORT = 8080;

/**
 * The factory's cloud host: one Lambda MicroVM per worker run.
 *
 * Lambda builds the worker image from the context bundle.ts renders, so the deploy needs no Docker.
 * EventBridge Scheduler invokes the launcher on each bundled worker's schedule, and the launcher starts
 * a MicroVM with a run token for its worker. The MicroVM's execution role may only call the token
 * vending machine, which trades the token for a session of the runtime role scoped to that worker or a
 * GitHub App installation token, and terminates the MicroVM when the run asks.
 */
export class FactoryHostStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps & { settings: FactorySettings }) {
    super(scope, id, props);
    const { settings } = props;
    const name = settings.name;
    const image = `${name}-worker`;
    const launcherName = `${name}-launcher`;
    const vendingName = `${name}-token-vending`;
    const bucketName = pagesBucketName(settings, this.account);
    const bucketArn = `arn:${this.partition}:s3:::${bucketName}`;
    const imageArn = `arn:${this.partition}:lambda:${this.region}:${this.account}:microvm-image:${image}`;
    const connector = (c: string) =>
      `arn:${this.partition}:lambda:${this.region}:aws:network-connector:aws-network-connector:${c}`;
    const lambdaPrincipal = new iam.ServicePrincipal('lambda.amazonaws.com', {
      conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
    }).withSessionTags();

    // Created empty and RETAIN: values are stored by hand (hosting-aws.md, Deploy) and never pass through a deploy.
    const secret = (id: string, secretName: string, description: string) => {
      const s = new secretsmanager.CfnSecret(this, id, { name: `${name}/${secretName}`, description });
      s.applyRemovalPolicy(RemovalPolicy.RETAIN);
      return s;
    };
    // Each is the image environment variable worker.py reads the secret's name from, and the secret.
    const secrets: [string, secretsmanager.CfnSecret][] = [
      ['KIRO_API_KEY_SECRET', secret('KiroApiKey', 'kiro-api-key', "The Kiro API key the factory's runs use")],
    ];
    // Read by the token vending machine only: runs get hourly installation tokens, never the key.
    const githubAppKey = secret('GithubAppKey', 'github-app-key', "The factory GitHub App's private key (PEM)");
    if (settings.webhook) {
      secrets.push(['WEBHOOK_SECRET', secret('Webhook', 'webhook', "The notification channels' webhook URL")]);
    }

    const vendingLogs = new logs.LogGroup(this, 'TokenVendingLogs', {
      logGroupName: `/aws/lambda/${vendingName}`,
      retention: logs.RetentionDays.ONE_YEAR,
    });
    // The runtime role's ARN is built from its name, not referenced, since the runtime role trusts this one.
    const runtimeRoleName = `${name}-runtime`;
    const vendingRole = new iam.Role(this, 'TokenVendingRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Vends each worker run a runtime session scoped to its worker',
      inlinePolicies: {
        Vend: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['sts:AssumeRole'],
              resources: [`arn:${this.partition}:iam::${this.account}:role/${runtimeRoleName}`],
            }),
            // MicroVM actions are authorized on the image ARN.
            new iam.PolicyStatement({ actions: ['lambda:TerminateMicrovm'], resources: [imageArn] }),
            new iam.PolicyStatement({
              actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
              resources: [`${vendingLogs.logGroupArn}:*`],
            }),
          ],
        }),
      },
    });

    // What each run acts as. Only the token vending machine may assume it.
    const runtime = new iam.Role(this, 'Runtime', {
      roleName: runtimeRoleName,
      assumedBy: vendingRole,
      description: `${name} runtime: each run gets a session scoped to its worker from the token vending machine`,
    });
    // The bucket's name is predictable, so every S3 grant holds only for the bucket in this account: a
    // bucket someone else created under the name gets nothing.
    const inAccount = { StringEquals: { 'aws:ResourceAccount': this.account } };
    const s3Statements = [
      // Get as well as Put: a report reads its previous page, and conditional writes compare ETags.
      new iam.PolicyStatement({
        sid: 'PublishPages',
        actions: ['s3:GetObject', 's3:PutObject'],
        resources: settings.pagePrefixes.map((p) => `${bucketArn}/${p}*`),
        conditions: inAccount,
      }),
      // Lists only the factory's own prefixes. S3 then answers a read of a missing key with 403, not 404,
      // and the Manual's S3 readers list the key's prefix to tell missing from denied.
      new iam.PolicyStatement({
        sid: 'ListBucket',
        actions: ['s3:ListBucket'],
        resources: [bucketArn],
        conditions: {
          ...inAccount,
          StringLike: { 's3:prefix': [...settings.pagePrefixes, RUNS_PREFIX, STATE_PREFIX].map((p) => `${p}*`) },
        },
      }),
      // Run records and leases (hosting/aws-cdk/history.py), and each worker's state directory between runs.
      new iam.PolicyStatement({
        sid: 'RunRecordsAndState',
        actions: ['s3:GetObject', 's3:PutObject'],
        resources: [`${bucketArn}/${RUNS_PREFIX}*`, `${bucketArn}/${STATE_PREFIX}*`],
        conditions: inAccount,
      }),
      // Agent runs trust every tool, so delete stays on the lease keys.
      new iam.PolicyStatement({
        sid: 'ReleaseLeases',
        actions: ['s3:DeleteObject'],
        resources: [`${bucketArn}/${RUNS_PREFIX}*/lock`],
        conditions: inAccount,
      }),
    ];
    // Every worker's session allows these as written; the S3 statements above it narrows to its worker.
    const sharedStatements = [
      new iam.PolicyStatement({
        sid: 'ReadSecrets',
        actions: ['secretsmanager:GetSecretValue'],
        resources: secrets.map(
          ([, s]) => `arn:${this.partition}:secretsmanager:${this.region}:${this.account}:secret:${s.name}-??????`,
        ),
      }),
      ...settings.runtimeStatements,
    ];
    new iam.Policy(this, 'RuntimePermissions', {
      roles: [runtime],
      statements: [...s3Statements, ...sharedStatements, ...Object.values(settings.workerStatements).flat()],
    });

    const buildLogs = new logs.LogGroup(this, 'BuildLogs', {
      logGroupName: `/${name}/${image}/build`,
      retention: logs.RetentionDays.THREE_MONTHS,
    });
    const workerLogs = new logs.LogGroup(this, 'WorkerLogs', {
      logGroupName: `/${name}/${image}/runs`,
      retention: logs.RetentionDays.ONE_YEAR,
    });

    const context = renderWorkerContext(settings);
    const config = JSON.parse(readFileSync(path.join(context, 'bundle', 'factory-config.json'), 'utf8'));
    const workers = JSON.parse(readFileSync(path.join(context, 'bundle', 'workers.json'), 'utf8')) as Record<
      string,
      { runTimeoutSecs: number }
    >;
    const code = new Asset(this, 'WorkerCode', { path: context, exclude: ['__pycache__'] });
    const buildRole = new iam.Role(this, 'BuildRole', {
      assumedBy: lambdaPrincipal,
      description: `Builds the ${image} MicroVM image`,
    });
    code.grantRead(buildRole);
    buildLogs.grantWrite(buildRole);

    const workerImage = new CfnResource(this, 'WorkerImage', {
      type: 'AWS::Lambda::MicrovmImage',
      properties: {
        Name: image,
        Description: `${name} worker, code ${code.assetHash.slice(0, 12)}`,
        BaseImageArn: `arn:${this.partition}:lambda:${this.region}:aws:microvm-image:${BASE_IMAGE}`,
        BaseImageVersion: BASE_IMAGE_VERSION,
        BuildRoleArn: buildRole.roleArn,
        CodeArtifact: { Uri: code.s3ObjectUrl },
        CpuConfigurations: [{ Architecture: 'ARM_64' }],
        Resources: [{ MinimumMemoryInMiB: settings.memoryMiB }],
        // Required by the schema, whose only value is ALL. Empty grants no extra OS capabilities.
        AdditionalOsCapabilities: [],
        // Build-time egress, for dnf, the pinned downloads and pip.
        EgressNetworkConnectors: [connector('INTERNET_EGRESS')],
        // Names only: the image is snapshotted, so secrets are read after /run.
        EnvironmentVariables: [
          { Key: 'FACTORY_REGION', Value: this.region },
          { Key: 'PAGES_BUCKET', Value: bucketName },
          { Key: 'FACTORY_TOKEN_VENDING_FUNCTION', Value: vendingName },
          { Key: 'GIT_USER_NAME', Value: settings.gitUserName },
          { Key: 'GIT_USER_EMAIL', Value: settings.gitUserEmail },
          ...secrets.map(([key, s]) => ({ Key: key, Value: s.name! })),
        ],
        Hooks: {
          Port: HOOK_PORT,
          MicrovmImageHooks: { Ready: 'ENABLED', ReadyTimeoutInSeconds: 300 },
          MicrovmHooks: { Run: 'ENABLED', RunTimeoutInSeconds: 30 },
        },
        Logging: { CloudWatch: { LogGroup: buildLogs.logGroupName } },
      },
    });
    workerImage.node.addDependency(buildRole);

    // Signs run tokens (functions/run-token.ts): only the launcher generates MACs, only the token vending
    // machine verifies them.
    const runTokenKey = new kms.Key(this, 'RunTokenKey', {
      description: `Signs the run token the ${name} launcher gives each worker run`,
      keySpec: kms.KeySpec.HMAC_256,
      keyUsage: kms.KeyUsage.GENERATE_VERIFY_MAC,
    });

    const nodeFunction = (id: string, props: {
      functionName?: string;
      description: string;
      entry: string;
      role?: iam.IRole;
      timeout?: Duration;
      environment?: Record<string, string>;
      logGroup: logs.ILogGroup;
    }) =>
      new NodejsFunction(this, id, {
        ...props,
        entry: path.join(__dirname, 'functions', props.entry),
        runtime: lambda.Runtime.NODEJS_22_X,
        architecture: lambda.Architecture.ARM_64,
        memorySize: 256,
        timeout: props.timeout ?? Duration.seconds(30),
        bundling: { externalModules: [] },
      });

    runTokenKey.grantVerifyMac(vendingRole);
    vendingRole.addToPrincipalPolicy(
      new iam.PolicyStatement({ actions: ['secretsmanager:GetSecretValue'], resources: [githubAppKey.ref] }),
    );
    const sessionScope: SessionScope = {
      sharedStatements: sharedStatements.map((s) => s.toStatementJson()),
      workerStatements: Object.fromEntries(
        Object.entries(settings.workerStatements).map(([w, list]) => [w, list.map((s) => s.toStatementJson())]),
      ),
      pagePrefixes: settings.pagePrefixes,
      pageWrites: settings.pageWrites,
      runRecordReaders: settings.runRecordReaders,
      githubPermissions: settings.githubPermissions,
      githubRepositories: githubRepositories(config),
    };
    const vending = nodeFunction('TokenVending', {
      functionName: vendingName,
      description: "Trades a worker run token for that worker's runtime session or a GitHub token, or ends its MicroVM",
      entry: 'token-vending.ts',
      role: vendingRole,
      // A Secrets Manager read and GitHub's 20-second token request, in one call.
      timeout: Duration.seconds(60),
      logGroup: vendingLogs,
      environment: {
        RUN_ROLE_ARN: runtime.roleArn,
        RUN_TOKEN_KEY_ARN: runTokenKey.keyArn,
        PAGES_BUCKET_ARN: bucketArn,
        SESSION_SCOPE: this.toJsonString(sessionScope),
        GITHUB_APP_ID: settings.githubAppId,
        GITHUB_INSTALLATION_ID: settings.githubInstallationId,
        GITHUB_APP_KEY_SECRET: githubAppKey.ref,
      },
    });

    // Anything in the MicroVM that reads instance metadata gets this role, a steered agent included.
    const microvmRole = new iam.Role(this, 'MicrovmRole', {
      assumedBy: lambdaPrincipal,
      description: `Execution role of each ${name} worker MicroVM: calls the token vending machine only`,
      inlinePolicies: {
        Run: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
              resources: [`${workerLogs.logGroupArn}:*`],
            }),
            new iam.PolicyStatement({ actions: ['lambda:InvokeFunction'], resources: [vending.functionArn] }),
          ],
        }),
      },
    });

    const launcherLogs = new logs.LogGroup(this, 'LauncherLogs', {
      logGroupName: `/aws/lambda/${launcherName}`,
      retention: logs.RetentionDays.ONE_YEAR,
    });
    const launcherRole = new iam.Role(this, 'LauncherRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: `Starts ${name} worker MicroVMs`,
      inlinePolicies: {
        Launch: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({ actions: ['lambda:RunMicrovm'], resources: [imageArn] }),
            // Passing each AWS-managed connector RunMicrovm attaches is authorized separately.
            new iam.PolicyStatement({
              actions: ['lambda:PassNetworkConnector'],
              resources: [connector('NO_INGRESS'), connector('INTERNET_EGRESS')],
            }),
            new iam.PolicyStatement({
              actions: ['iam:PassRole'],
              resources: [microvmRole.roleArn],
              conditions: { StringEquals: { 'iam:PassedToService': 'lambda.amazonaws.com' } },
            }),
            new iam.PolicyStatement({
              actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
              resources: [`${launcherLogs.logGroupArn}:*`],
            }),
          ],
        }),
      },
    });
    runTokenKey.grantGenerateMac(launcherRole);
    const launcher = nodeFunction('Launcher', {
      functionName: launcherName,
      description: `Starts one ${name} worker run in its own Lambda MicroVM`,
      entry: 'launch.ts',
      role: launcherRole,
      logGroup: launcherLogs,
      environment: {
        IMAGE_ARN: imageArn,
        EXECUTION_ROLE_ARN: microvmRole.roleArn,
        WORKER_LOG_GROUP: workerLogs.logGroupName,
        RUN_TOKEN_KEY_ARN: runTokenKey.keyArn,
        // Each run token expires with its worker's timeout (functions/launch.ts runTokenExpiry).
        WORKER_TIMEOUTS: JSON.stringify(Object.fromEntries(Object.entries(workers).map(([w, v]) => [w, v.runTimeoutSecs]))),
      },
    });
    launcher.node.addDependency(workerImage);

    // Each image update adds a version, an image holds a limited number, and the image resource never
    // deletes them, so after every update this deletes all but the newest few (functions/prune.ts).
    const pruner = nodeFunction('ImageVersionPruner', {
      description: `Deletes old ${image} image versions after each image update`,
      entry: 'prune.ts',
      timeout: Duration.minutes(5),
      logGroup: new logs.LogGroup(this, 'ImageVersionPrunerLogs', { retention: logs.RetentionDays.THREE_MONTHS }),
    });
    pruner.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['lambda:GetMicrovmImage', 'lambda:ListMicrovmImageVersions', 'lambda:DeleteMicrovmImageVersion'],
        resources: [imageArn, `${imageArn}:*`],
      }),
    );
    const prune = new CustomResource(this, 'ImageVersionPrune', {
      serviceToken: new cr.Provider(this, 'ImageVersionPruneProvider', { onEventHandler: pruner }).serviceToken,
      // Every image update yields a new latest version, whichever property changed, so it reruns this.
      properties: { ImageArn: imageArn, LatestVersion: workerImage.getAtt('LatestActiveImageVersion').toString() },
    });
    prune.node.addDependency(workerImage);

    // One schedule per worker the bundle carries. Each run takes its worker's lease, so a late or
    // repeated delivery is skipped.
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
      description: `Invokes the ${name} launcher on each worker schedule`,
    });
    launcher.grantInvoke(schedulerRole);
    checkWorkerTimeouts(path.join(context, 'bundle'));
    for (const worker of scheduledWorkers(path.join(context, 'bundle'))) {
      new scheduler.CfnSchedule(this, `Schedule-${worker.name}`, {
        name: `${name}-${worker.name}`,
        description: `${name} ${worker.name}: ${worker.schedule} (${worker.timezone})`,
        state: settings.schedulesEnabled ? 'ENABLED' : 'DISABLED',
        scheduleExpression: schedulerCron(worker.schedule),
        scheduleExpressionTimezone: worker.timezone,
        flexibleTimeWindow: { mode: 'OFF' },
        target: {
          arn: launcher.functionArn,
          roleArn: schedulerRole.roleArn,
          input: JSON.stringify({ worker: worker.name }),
          // A slot not delivered within 10 minutes is dropped: the next slot runs the worker anyway.
          retryPolicy: { maximumEventAgeInSeconds: 600, maximumRetryAttempts: 3 },
        },
      });
    }

    new CfnOutput(this, 'WorkerImageArn', { value: imageArn });
    new CfnOutput(this, 'LauncherFunctionName', { value: launcher.functionName });
    new CfnOutput(this, 'WorkerLogGroup', { value: workerLogs.logGroupName });
  }
}
