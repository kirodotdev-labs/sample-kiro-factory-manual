#!/usr/bin/env node
/**
 * A reference AWS host for a factory, built to ../hosting-aws.md. Copy this directory into the
 * instance's package and adapt it; nothing depends on it in place. Keep it beside the instance
 * directory, not inside it: the worker image carries that whole directory.
 *
 *   FACTORY_MANUAL=<Manual checkout> FACTORY_INSTANCE=<instance dir> npx cdk deploy FactoryPages
 *   (put the PagesBaseUrl output in the instance's integrations.publishing.baseUrl)
 *   FACTORY_MANUAL=... FACTORY_INSTANCE=... npx cdk deploy FactoryHost
 */
import path from 'node:path';
import { App } from 'aws-cdk-lib';
import { FactoryHostStack } from '../lib/host-stack';
import { FactoryPagesStack } from '../lib/pages-stack';
import { FactorySettings } from '../lib/settings';

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return path.resolve(value);
}

const instanceDir = required('FACTORY_INSTANCE');
const settings: FactorySettings = {
  name: 'factory',
  manualDir: required('FACTORY_MANUAL'),
  instanceDir,
  hostProfile: path.join(instanceDir, 'hosts', 'aws-cdk.json'),
  modules: {},
  pagePrefixes: ['items/', 'learn/', 'discover/', 'report/'],
  pageWrites: {
    design: ['items/*'],
    implement: ['items/*'],
    learn: ['learn/*'],
    discover: ['discover/*'],
    report: ['report/*'],
  },
  runRecordReaders: ['learn', 'report'],
  githubAppId: 'APP_ID',
  githubInstallationId: 'INSTALLATION_ID',
  // Issues write is for a board on GitHub Issues; only design and implement push and open PRs. Actions
  // write lets implement rerun a failed check, and also dispatch any workflow_dispatch workflow with
  // inputs it chooses (README, Security). Add workflows: 'write' to implement only if it may edit CI files.
  githubPermissions: {
    triage: { contents: 'read', issues: 'write', pull_requests: 'read' },
    design: { contents: 'write', issues: 'write', pull_requests: 'write', checks: 'read', statuses: 'read' },
    implement: {
      contents: 'write',
      issues: 'write',
      pull_requests: 'write',
      checks: 'read',
      statuses: 'read',
      actions: 'write',
    },
    release: { contents: 'read', issues: 'write', pull_requests: 'read' },
    monitor: { contents: 'read', issues: 'write', pull_requests: 'read' },
    discover: { contents: 'read', issues: 'write', pull_requests: 'read' },
    janitor: { contents: 'read', issues: 'write', pull_requests: 'read' },
    learn: { contents: 'read', issues: 'read', pull_requests: 'read' },
    report: { contents: 'read', issues: 'read', pull_requests: 'read', checks: 'read', statuses: 'read' },
  },
  gitUserName: 'my-factory[bot]',
  gitUserEmail: 'BOT_USER_ID+my-factory[bot]@users.noreply.github.com',
  memoryMiB: 4096,
  webhook: false,
  schedulesEnabled: false,
  runtimeStatements: [],
  // For a monitor reading CloudWatch in this account (the instance's telemetry skill and facts script do
  // the reading):
  //   monitor: [
  //     new iam.PolicyStatement({ actions: ['cloudwatch:GetMetricData', 'cloudwatch:ListMetrics'], resources: ['*'] }),
  //     new iam.PolicyStatement({ actions: ['logs:StartQuery', 'logs:GetQueryResults'], resources: ['*'] }),
  //   ],
  workerStatements: {},
};

const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION };
const app = new App();
new FactoryPagesStack(app, 'FactoryPages', { env, settings });
new FactoryHostStack(app, 'FactoryHost', { env, settings });
