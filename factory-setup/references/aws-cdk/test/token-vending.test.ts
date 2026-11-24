import assert from 'node:assert/strict';
import { test } from 'node:test';
import { GITHUB_READ_ONLY, SessionScope, githubTokenRequest, sessionName, sessionPolicy } from '../lib/functions/token-vending';

const scope: SessionScope = {
  sharedStatements: [{ Effect: 'Allow', Action: 'secretsmanager:GetSecretValue', Resource: 'arn:secret:kiro-*' }],
  workerStatements: { monitor: [{ Effect: 'Allow', Action: 'sts:AssumeRole', Resource: 'arn:aws:iam::ACCOUNT:role/Read' }] },
  pagePrefixes: ['items/', 'learn/'],
  pageWrites: { design: ['items/*'], learn: ['learn/*'] },
  runRecordReaders: ['learn'],
  githubPermissions: { implement: { contents: 'write', pull_requests: 'write' } },
  githubRepositories: ['acme/widgets', 'acme/tracker'],
};

type Statement = {
  Effect: string;
  Action?: string | string[];
  NotAction?: string | string[];
  Resource?: string | string[];
  NotResource?: string[];
  Condition?: any;
};
const statements = (worker: string): Statement[] => JSON.parse(sessionPolicy('arn:aws:s3:::b', scope, worker)).Statement;
const allowed = (worker: string, action: string, effect = 'Allow') =>
  statements(worker)
    .filter(
      (s) =>
        s.Effect === effect &&
        s.Resource &&
        (s.Action === action || (Array.isArray(s.Action) && s.Action.includes(action))),
    )
    .flatMap((s) => [s.Resource!].flat());

test('a worker reads the pages and only its own runs and state', () => {
  assert.deepEqual(allowed('design', 's3:GetObject'), [
    'arn:aws:s3:::b/items/*',
    'arn:aws:s3:::b/learn/*',
    'arn:aws:s3:::b/runs/design/*',
    'arn:aws:s3:::b/state/design/*',
  ]);
});

test('a run record reader reads every run but only its own state', () => {
  assert.ok(allowed('learn', 's3:GetObject').includes('arn:aws:s3:::b/runs/*'));
  assert.ok(allowed('learn', 's3:GetObject').includes('arn:aws:s3:::b/state/learn/*'));
  assert.ok(!allowed('learn', 's3:GetObject').some((r) => r.endsWith('/state/*')));
});

test('a worker lists the same keys it may read', () => {
  const list = statements('design').find((s) => s.Action === 's3:ListBucket')!;
  assert.deepEqual(list.Condition.StringLike['s3:prefix'], ['items/*', 'learn/*', 'runs/design/*', 'state/design/*']);
});

test('a worker puts only the pages it publishes and its own records and state', () => {
  assert.deepEqual(allowed('design', 's3:PutObject'), [
    'arn:aws:s3:::b/items/*',
    'arn:aws:s3:::b/runs/design/*',
    'arn:aws:s3:::b/state/design/*',
  ]);
  assert.deepEqual(allowed('triage', 's3:PutObject'), ['arn:aws:s3:::b/runs/triage/*', 'arn:aws:s3:::b/state/triage/*']);
  assert.deepEqual(allowed('design', 's3:DeleteObject'), ['arn:aws:s3:::b/runs/design/lock']);
});

test('a worker the settings do not name gets nothing beyond its own keys', () => {
  assert.deepEqual(allowed('smoke', 's3:PutObject'), ['arn:aws:s3:::b/runs/smoke/*', 'arn:aws:s3:::b/state/smoke/*']);
  assert.equal(statements('smoke').length, 5);
});

test("a worker's GitHub token carries its permissions, or read-only, on the factory's repos", () => {
  assert.deepEqual(githubTokenRequest(scope, 'implement'), {
    repositories: ['widgets', 'tracker'],
    permissions: { metadata: 'read', contents: 'write', pull_requests: 'write' },
  });
  assert.deepEqual(githubTokenRequest(scope, 'triage').permissions, { metadata: 'read', ...GITHUB_READ_ONLY });
});

test("a session allows only its listed statements: shared ones, and its own worker's", () => {
  for (const worker of ['design', 'monitor']) {
    assert.ok(statements(worker).every((s) => s.Effect === 'Allow' && s.Action && s.Resource), worker);
    assert.deepEqual(allowed(worker, 'secretsmanager:GetSecretValue'), ['arn:secret:kiro-*']);
  }
  assert.deepEqual(allowed('monitor', 'sts:AssumeRole'), ['arn:aws:iam::ACCOUNT:role/Read']);
  assert.deepEqual(allowed('design', 'sts:AssumeRole'), []);
});

test('a session name fits STS and differs per launch', () => {
  const name = sessionName('a'.repeat(41), 9_999_999_999);
  assert.ok(name.length <= 64 && /^[\w+=,.@-]+$/.test(name));
  assert.notEqual(sessionName('triage', 1), sessionName('triage', 2));
});
