import { awsApi } from './aws-api';

/**
 * The run token the launcher puts in each run's hook payload, which the token vending machine
 * (token-vending.ts) takes as proof of the run's worker: `<worker>.<expiry>.<mac>`, where the MAC is a
 * KMS HMAC key's over `<worker>.<expiry>` and the expiry is in epoch seconds. Only the launcher may
 * generate MACs with the key, so a run cannot turn its token into another worker's.
 */

const WORKER_NAME = /^[a-z][a-z0-9-]{0,40}$/;

const MAC_ALGORITHM = 'HMAC_SHA_256';

// The bundled SDK has no KMS client, so this calls KMS's JSON API directly.
async function kms(action: string, body: Record<string, string>): Promise<Record<string, unknown>> {
  return (await awsApi('kms', {
    method: 'POST',
    path: '/',
    headers: { 'content-type': 'application/x-amz-json-1.1', 'x-amz-target': `TrentService.${action}` },
    body: JSON.stringify(body),
  })) as Record<string, unknown>;
}

const base64 = (s: string) => Buffer.from(s).toString('base64');

export async function issueRunToken(keyArn: string, worker: string, expiresAt: number): Promise<string> {
  // The launcher's payload is untyped JSON, and RegExp.test would accept true as its string form.
  if (typeof worker !== 'string' || !WORKER_NAME.test(worker)) {
    throw new Error(`invalid worker name ${JSON.stringify(worker)}`);
  }
  const message = `${worker}.${expiresAt}`;
  const { Mac } = await kms('GenerateMac', { KeyId: keyArn, MacAlgorithm: MAC_ALGORITHM, Message: base64(message) });
  return `${message}.${Mac as string}`;
}

/** The token's parts, if it is unexpired at `now` (epoch seconds). Only the MAC, checked by verifyRunToken, proves them. */
export function parseRunToken(token: unknown, now: number): { worker: string; message: string; mac: string } {
  const [worker, expiry, mac] = typeof token === 'string' ? token.split('.') : [];
  if (mac === undefined) throw new Error('malformed run token');
  // Not `<= now`: an expiry that is not a number must not pass.
  if (!(Number(expiry) > now)) throw new Error('run token expired');
  return { worker, message: `${worker}.${expiry}`, mac };
}

/** The worker a token was issued for and its expiry. Throws unless KMS verifies its MAC; KMS answers a wrong MAC with an error. */
export async function verifyRunToken(
  keyArn: string,
  token: unknown,
  now: number,
): Promise<{ worker: string; expiresAt: number }> {
  const { worker, message, mac } = parseRunToken(token, now);
  const { MacValid } = await kms('VerifyMac', {
    KeyId: keyArn,
    MacAlgorithm: MAC_ALGORITHM,
    Message: base64(message),
    Mac: mac,
  });
  if (MacValid !== true) throw new Error('run token not verified');
  return { worker, expiresAt: Number(message.split('.')[1]) };
}
