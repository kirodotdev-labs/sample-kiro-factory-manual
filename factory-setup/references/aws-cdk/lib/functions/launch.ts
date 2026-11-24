import { randomUUID } from 'node:crypto';
import { microvmApi, setting } from './aws-api';
import { issueRunToken } from './run-token';

/**
 * Starts one worker run in its own Lambda MicroVM: RunMicrovm, signed with SigV4.
 *
 *   aws lambda invoke --function-name <launcher> --cli-binary-format raw-in-base64-out \
 *     --payload '{"worker":"smoke"}' out.json
 *
 * `item` starts the run for one board item (run_worker.py --item): '{"worker":"implement","item":"<id>"}'.
 */

/** Where the run goes and what it runs as. The host stack sets all of these. */
export interface LaunchSettings {
  readonly imageArn: string;
  readonly executionRoleArn: string;
  readonly logGroup: string;
}

/** The longest a run may take, after which Lambda terminates the MicroVM. */
export const MAX_RUN_SECONDS = 2 * 60 * 60;

/**
 * How long a run token outlives the launch beyond the worker's timeout: the MicroVM starting, the
 * clone before the run and the record and state uploads after it.
 */
export const RUN_TOKEN_MARGIN_SECONDS = 30 * 60;

/** When a token for `worker` launched at `now` expires: its timeout from workers.json plus the margin, never past the longest run. */
export function runTokenExpiry(worker: string, timeouts: Record<string, number>, now: number): number {
  const timeout = timeouts[worker] ?? MAX_RUN_SECONDS;
  return now + Math.min(timeout + RUN_TOKEN_MARGIN_SECONDS, MAX_RUN_SECONDS + 10 * 60);
}

// The image ARN names the partition and region the connectors live in.
const connector = (imageArn: string, name: string) => {
  const [, partition, , region] = imageArn.split(':');
  return `arn:${partition}:lambda:${region}:aws:network-connector:aws-network-connector:${name}`;
};

/**
 * The RunMicrovm body for one run of `worker`. Nothing may connect in, and there is no idle
 * policy: idle time counts only inbound traffic, so a run that only calls out would be suspended.
 */
export function runMicrovmRequest(
  worker: string,
  settings: LaunchSettings,
  clientToken: string,
  runToken: string,
  item?: string,
) {
  // run_worker.py checks the id itself.
  if (item !== undefined && typeof item !== 'string') {
    throw new Error(`invalid item id ${JSON.stringify(item)}`);
  }
  return {
    imageIdentifier: settings.imageArn,
    executionRoleArn: settings.executionRoleArn,
    ingressNetworkConnectors: [connector(settings.imageArn, 'NO_INGRESS')],
    egressNetworkConnectors: [connector(settings.imageArn, 'INTERNET_EGRESS')],
    maximumDurationInSeconds: MAX_RUN_SECONDS,
    runHookPayload: JSON.stringify({ worker, ...(item === undefined ? {} : { item }), token: runToken }),
    logging: { cloudWatch: { logGroup: settings.logGroup } },
    clientToken,
  };
}

export async function handler(event: {
  worker?: string;
  item?: string;
}): Promise<{ microvmId: string; state: string }> {
  const settings: LaunchSettings = {
    imageArn: setting('IMAGE_ARN'),
    executionRoleArn: setting('EXECUTION_ROLE_ARN'),
    logGroup: setting('WORKER_LOG_GROUP'),
  };
  const worker = event.worker ?? '';
  const timeouts = JSON.parse(setting('WORKER_TIMEOUTS')) as Record<string, number>;
  const expiresAt = runTokenExpiry(worker, timeouts, Math.floor(Date.now() / 1000));
  const runToken = await issueRunToken(setting('RUN_TOKEN_KEY_ARN'), worker, expiresAt);
  const body = runMicrovmRequest(worker, settings, randomUUID(), runToken, event.item);
  const { microvmId, state } = (await microvmApi('POST', '/2025-09-09/microvms', { body })) as {
    microvmId: string;
    state: string;
  };
  // expiresAt names the run's sessions in CloudTrail (token-vending.ts sessionName).
  console.log(JSON.stringify({ worker: event.worker, item: event.item, microvmId, state, expiresAt }));
  return { microvmId, state };
}
