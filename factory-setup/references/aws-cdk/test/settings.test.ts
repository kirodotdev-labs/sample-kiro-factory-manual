import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { checkWorkerTimeouts, schedulerCron } from '../lib/schedule';
import { githubRepositories } from '../lib/settings';

test('crontab days of week map to Scheduler, 0 and 7 both Sunday', () => {
  const cases: [string, string][] = [
    ['0 9 * * 1-5', 'cron(0 9 ? * 2,3,4,5,6 *)'],
    ['0 9 * * 5-7', 'cron(0 9 ? * 1,6,7 *)'],
    ['0 9 * * 0,7', 'cron(0 9 ? * 1 *)'],
    ['0 9 * * 1-7', 'cron(0 9 ? * 1,2,3,4,5,6,7 *)'],
    ['0 9 * * 0-7', 'cron(0 9 ? * 1,2,3,4,5,6,7 *)'],
    ['0 9 * * */2', 'cron(0 9 ? * 1,3,5,7 *)'],
    ['0 * * * *', 'cron(0 * * * ? *)'],
    ['0 9 15 * *', 'cron(0 9 15 * ? *)'],
    ['0 9 * * MON-FRI', 'cron(0 9 ? * 2,3,4,5,6 *)'],
    ['0 9 * * sun,sat', 'cron(0 9 ? * 1,7 *)'],
  ];
  for (const [crontab, want] of cases) assert.equal(schedulerCron(crontab), want, crontab);
});

test('a schedule Scheduler cannot express is refused', () => {
  for (const crontab of ['0 9 15 * 1', '0 9 1 * */2', '0 9 1 * */1', '0 9 1 * 0-7', '0 9 */2 * 0-7', '0 9 * * MONDAY', '0 9 * * 8', '0 9 * * 5-2', '0 9 * *']) {
    assert.throws(() => schedulerCron(crontab), crontab);
  }
});

test('a worker timeout the run server would cut short is refused at synth', () => {
  const bundle = (workers: object) => {
    const dir = mkdtempSync(path.join(tmpdir(), 'bundle-'));
    writeFileSync(path.join(dir, 'workers.json'), JSON.stringify(workers));
    return dir;
  };
  assert.doesNotThrow(() => checkWorkerTimeouts(bundle({ learn: { runTimeoutSecs: 6000 } })));
  assert.throws(() => checkWorkerTimeouts(bundle({ implement: { runTimeoutSecs: 6600 } })), /implement/);
});

test('the GitHub token covers the code repo and a GitHub board repo of the same owner', () => {
  const config = (board?: object) => ({ repo: { slug: 'acme/widgets' }, integrations: { board } as any });
  assert.deepEqual(githubRepositories(config()), ['acme/widgets']);
  assert.deepEqual(githubRepositories(config({ use: 'other-board', boardId: 'x' })), ['acme/widgets']);
  assert.deepEqual(githubRepositories(config({ use: 'github', boardId: 'acme/widgets' })), ['acme/widgets']);
  assert.deepEqual(githubRepositories(config({ use: 'github', boardId: 'acme/tracker' })), ['acme/widgets', 'acme/tracker']);
  assert.throws(() => githubRepositories(config({ use: 'github', boardId: 'other/tracker' })), /different owners/);
});
