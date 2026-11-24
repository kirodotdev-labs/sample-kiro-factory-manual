import { createSign } from 'node:crypto';
import { AssumeRoleCommand, STSClient } from '@aws-sdk/client-sts';
import { awsApi, microvmApi, setting } from './aws-api';
import { verifyRunToken } from './run-token';

/**
 * The token vending machine: the only thing a worker MicroVM's execution role may call. Given the run
 * token from the run hook payload (run-token.ts), it returns a session of the runtime role scoped to
 * that token's worker, in credential_process's output shape; a GitHub App installation token with that
 * worker's permissions on the factory's repos; or terminates the MicroVM the run names.
 *
 *   {"token": "<run token>"}                              -> {"Version": 1, "AccessKeyId": ..., "Expiration": ...}
 *   {"token": "<run token>", "github": true}              -> {"token": "ghs_...", "expiresAt": ...}
 *   {"token": "<run token>", "terminate": "<microvmId>"}  -> {}
 *
 * The App's private key stays here, so a run holds only installation tokens, which expire within the hour.
 * A token proves the worker, not the MicroVM, so a run can still end another run's MicroVM.
 */

// The function's own credentials come from an assumed role, so this is role chaining, which STS caps at an hour.
const SESSION_SECONDS = 3600;
const MICROVM_ID = /^microvm-[0-9a-f-]{1,64}$/;

/** The host stack's settings that scope a session (lib/settings.ts), passed as SESSION_SCOPE. */
export interface SessionScope {
  /** IAM statement JSON every worker's session allows. */
  readonly sharedStatements: object[];
  /** IAM statement JSON only the named worker's session allows. */
  readonly workerStatements: Record<string, object[]>;
  readonly pagePrefixes: string[];
  readonly pageWrites: Record<string, string[]>;
  readonly runRecordReaders: string[];
  readonly githubPermissions: Record<string, Record<string, 'read' | 'write'>>;
  /** The repos (owner/name, one owner) the installation tokens cover: the code repo, and the board's on GitHub. */
  readonly githubRepositories: string[];
}

/** A worker not listed in githubPermissions: enough to read the repo, its issues and PRs. */
export const GITHUB_READ_ONLY = { contents: 'read', issues: 'read', pull_requests: 'read' } as const;

/**
 * Everything one worker's run may do, as an allowlist the runtime role also grants: reads and lists of the
 * pages and its own runs/ and state/ keys (all of runs/ for the run record readers), puts on the pages it
 * publishes and its own keys, delete on its lease, the statements every worker shares, and its own.
 * Anything not listed is refused, so a statement for one worker reaches no other.
 */
export function sessionPolicy(arn: string, scope: SessionScope, worker: string): string {
  const runs = scope.runRecordReaders.includes(worker) ? 'runs/*' : `runs/${worker}/*`;
  const readable = [...scope.pagePrefixes.map((prefix) => `${prefix}*`), runs, `state/${worker}/*`];
  const statements: object[] = [
    { Effect: 'Allow', Action: 's3:GetObject', Resource: readable.map((key) => `${arn}/${key}`) },
    {
      Effect: 'Allow',
      Action: 's3:PutObject',
      Resource: [
        ...(scope.pageWrites[worker] ?? []).map((key) => `${arn}/${key}`),
        `${arn}/runs/${worker}/*`,
        `${arn}/state/${worker}/*`,
      ],
    },
    { Effect: 'Allow', Action: 's3:DeleteObject', Resource: `${arn}/runs/${worker}/lock` },
    { Effect: 'Allow', Action: 's3:ListBucket', Resource: arn, Condition: { StringLike: { 's3:prefix': readable } } },
    ...scope.sharedStatements,
    ...(scope.workerStatements[worker] ?? []),
  ];
  return JSON.stringify({ Version: '2012-10-17', Statement: statements });
}

/** The body of GitHub's installation token request for one worker's run. */
export function githubTokenRequest(scope: SessionScope, worker: string) {
  return {
    repositories: scope.githubRepositories.map((slug) => slug.split('/')[1]),
    permissions: { metadata: 'read', ...(scope.githubPermissions[worker] ?? GITHUB_READ_ONLY) },
  };
}

/** Names each session for its launch, so CloudTrail tells runs of one worker apart; the launcher logs the same expiry. */
export const sessionName = (worker: string, expiresAt: number) => `run-${worker}-${expiresAt}`;

const sts = new STSClient({});

const base64url = (b: Buffer | string) => Buffer.from(b).toString('base64url');

/** The App's JWT: RS256 over {iat, exp, iss}, backdated a minute for clock skew, valid well under GitHub's 10-minute cap. */
export function appJwt(appId: string, privateKey: string, now: number): string {
  const unsigned = `${base64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))}.${base64url(
    JSON.stringify({ iat: now - 60, exp: now + 540, iss: appId }),
  )}`;
  return `${unsigned}.${base64url(createSign('RSA-SHA256').update(unsigned).sign(privateKey))}`;
}

async function githubToken(scope: SessionScope, worker: string): Promise<{ token: string; expiresAt: string }> {
  const { SecretString: key } = (await awsApi('secretsmanager', {
    method: 'POST',
    path: '/',
    headers: { 'content-type': 'application/x-amz-json-1.1', 'x-amz-target': 'secretsmanager.GetSecretValue' },
    body: JSON.stringify({ SecretId: setting('GITHUB_APP_KEY_SECRET') }),
  })) as { SecretString: string };
  const jwt = appJwt(setting('GITHUB_APP_ID'), key, Math.floor(Date.now() / 1000));
  const response = await fetch(
    `https://api.github.com/app/installations/${setting('GITHUB_INSTALLATION_ID')}/access_tokens`,
    {
      method: 'POST',
      headers: { authorization: `Bearer ${jwt}`, accept: 'application/vnd.github+json' },
      body: JSON.stringify(githubTokenRequest(scope, worker)),
      signal: AbortSignal.timeout(20_000),
    },
  );
  const body = (await response.json()) as { token?: string; expires_at?: string; message?: string };
  if (!response.ok || !body.token || !body.expires_at) {
    throw new Error(`GitHub refused an installation token: ${response.status} ${body.message ?? ''}`);
  }
  return { token: body.token, expiresAt: body.expires_at };
}

export async function handler(event: { token?: unknown; terminate?: unknown; github?: unknown }): Promise<object> {
  const { worker, expiresAt } = await verifyRunToken(
    setting('RUN_TOKEN_KEY_ARN'),
    event.token,
    Math.floor(Date.now() / 1000),
  );
  const scope = JSON.parse(setting('SESSION_SCOPE')) as SessionScope;
  const run = sessionName(worker, expiresAt);
  if (event.github === true) {
    const token = await githubToken(scope, worker);
    console.log(JSON.stringify({ run, github: token.expiresAt }));
    return token;
  }
  if (event.terminate !== undefined) {
    if (typeof event.terminate !== 'string' || !MICROVM_ID.test(event.terminate)) {
      throw new Error('invalid MicroVM id');
    }
    await microvmApi('DELETE', `/2025-09-09/microvms/${event.terminate}`);
    console.log(JSON.stringify({ run, terminated: event.terminate }));
    return {};
  }
  const { Credentials: c } = await sts.send(
    new AssumeRoleCommand({
      RoleArn: setting('RUN_ROLE_ARN'),
      RoleSessionName: run,
      Policy: sessionPolicy(setting('PAGES_BUCKET_ARN'), scope, worker),
      DurationSeconds: SESSION_SECONDS,
    }),
  );
  if (!c?.AccessKeyId || !c.SecretAccessKey || !c.SessionToken || !c.Expiration) {
    throw new Error('AssumeRole returned no credentials');
  }
  console.log(JSON.stringify({ run, expires: c.Expiration.toISOString() }));
  return {
    Version: 1,
    AccessKeyId: c.AccessKeyId,
    SecretAccessKey: c.SecretAccessKey,
    SessionToken: c.SessionToken,
    Expiration: c.Expiration.toISOString(),
  };
}
