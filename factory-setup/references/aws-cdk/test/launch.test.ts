import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MAX_RUN_SECONDS, runMicrovmRequest, runTokenExpiry } from '../lib/functions/launch';
import { issueRunToken, parseRunToken } from '../lib/functions/run-token';

const settings = { imageArn: 'arn:aws-cn:lambda:cn-north-1:ACCOUNT:microvm-image:factory', executionRoleArn: 'arn:role', logGroup: '/runs' };

test('a run admits no ingress, carries its worker and token, and ends at the longest run', () => {
  const body = runMicrovmRequest('triage', settings, 'client', 'tok', 'P-7');
  assert.deepEqual(JSON.parse(body.runHookPayload), { worker: 'triage', item: 'P-7', token: 'tok' });
  assert.equal(
    body.ingressNetworkConnectors[0],
    'arn:aws-cn:lambda:cn-north-1:aws:network-connector:aws-network-connector:NO_INGRESS',
  );
  assert.equal(body.maximumDurationInSeconds, MAX_RUN_SECONDS);
  assert.equal(body.executionRoleArn, 'arn:role');
});

test('a bad worker name or item id starts nothing', async () => {
  for (const worker of ['', 'Triage', '-x', '../x', 'a'.repeat(42), true as unknown as string, ['x'] as unknown as string]) {
    await assert.rejects(issueRunToken('arn:key', worker, 0), /invalid worker name/);
  }
  for (const item of [0 as unknown as string, true as unknown as string]) {
    assert.throws(() => runMicrovmRequest('triage', settings, 'c', 't', item));
  }
});

test("a run token expires with its worker's timeout, never past the longest run", () => {
  assert.equal(runTokenExpiry('triage', { triage: 1800 }, 1000), 1000 + 1800 + 1800);
  assert.equal(runTokenExpiry('learn', { learn: 99_999 }, 0), MAX_RUN_SECONDS + 600);
  assert.equal(runTokenExpiry('smoke', {}, 0), MAX_RUN_SECONDS + 600);
});

test('a run token parses into its three parts, and only before it expires', () => {
  assert.deepEqual(parseRunToken('triage.2000.AbC+/=', 1000), { worker: 'triage', message: 'triage.2000', mac: 'AbC+/=' });
  for (const expired of ['triage.1000.AbC', 'triage.NaN.AbC', 'triage..AbC']) {
    assert.throws(() => parseRunToken(expired, 1000), /expired/);
  }
  for (const bad of [undefined, 7, '', 'triage.2000']) {
    assert.throws(() => parseRunToken(bad, 1000), /malformed/);
  }
});
