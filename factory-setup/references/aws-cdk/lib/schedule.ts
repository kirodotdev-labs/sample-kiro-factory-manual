import { readFileSync } from 'node:fs';
import path from 'node:path';

/** One worker the bundle's workers.json schedules: a 5-field crontab line in an IANA time zone. */
export interface ScheduledWorker {
  readonly name: string;
  readonly schedule: string;
  readonly timezone: string;
}

/** The workers in a rendered bundle that carry a schedule. The renderer writes only enabled workers. */
export function scheduledWorkers(bundleDir: string): ScheduledWorker[] {
  const workers = JSON.parse(readFileSync(path.join(bundleDir, 'workers.json'), 'utf8')) as Record<
    string,
    { schedule?: string; timezone?: string }
  >;
  return Object.entries(workers)
    .filter(([, w]) => w.schedule)
    .map(([name, w]) => ({ name, schedule: w.schedule!, timezone: w.timezone || 'UTC' }));
}

/**
 * The longest worker timeout this host runs in full: worker.py's RUN_DEADLINE_SECONDS, less 15 minutes for
 * the clone and state restore before run_worker.py and its record after. A run killed before its own
 * timeout writes no record and holds its lease until the claim expires.
 */
const MAX_WORKER_TIMEOUT_SECONDS = 2 * 60 * 60 - 300 - 900;

/** Refuses a bundle with a worker whose timeout the run server would cut short. */
export function checkWorkerTimeouts(bundleDir: string): void {
  const workers = JSON.parse(readFileSync(path.join(bundleDir, 'workers.json'), 'utf8')) as Record<
    string,
    { runTimeoutSecs?: number }
  >;
  for (const [name, w] of Object.entries(workers)) {
    if ((w.runTimeoutSecs ?? 0) > MAX_WORKER_TIMEOUT_SECONDS) {
      throw new Error(
        `worker ${name}: timeoutSecs ${w.runTimeoutSecs} is more than this host allows (${MAX_WORKER_TIMEOUT_SECONDS})`,
      );
    }
  }
}

const DAY_NAMES = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

/** The days a crontab day-of-week field names, Sunday as 0 (crontab accepts 7 for it too, and names). */
function crontabDays(field: string, crontab: string): Set<number> {
  const days = new Set<number>();
  const numeric = field.toUpperCase().replace(/[A-Z]+/g, (name) => {
    const day = DAY_NAMES.indexOf(name);
    if (day < 0) throw new Error(`schedule ${JSON.stringify(crontab)}: unknown day ${JSON.stringify(name)}`);
    return String(day);
  });
  for (const part of numeric.split(',')) {
    const m = /^(\*|(\d)(?:-(\d))?)(?:\/(\d+))?$/.exec(part);
    if (!m) throw new Error(`schedule ${JSON.stringify(crontab)}: day of week ${JSON.stringify(part)} is not numeric`);
    const step = Number(m[4] ?? 1);
    const start = m[1] === '*' ? 0 : Number(m[2]);
    const end = m[1] === '*' ? 7 : m[3] !== undefined ? Number(m[3]) : m[4] !== undefined ? 7 : start;
    if (end > 7 || start > end || step < 1) {
      throw new Error(`schedule ${JSON.stringify(crontab)}: day of week ${JSON.stringify(part)} is out of range`);
    }
    for (let d = start; d <= end; d += step) days.add(d % 7);
  }
  return days;
}

/**
 * A crontab line as an EventBridge Scheduler cron expression. Scheduler takes six fields, numbers days of
 * the week 1-7 from Sunday (crontab: 0-7, both 0 and 7 Sunday, or names), and needs `?` in one of the two
 * day fields. So a line may restrict the day of month or the day of week, not both: crontab's rules for
 * combining them have no Scheduler form, and such a line is refused rather than approximated.
 */
export function schedulerCron(crontab: string): string {
  const fields = crontab.trim().split(/\s+/);
  if (fields.length !== 5) throw new Error(`schedule ${JSON.stringify(crontab)} is not a 5-field crontab line`);
  const [minute, hour, dom, month, dow] = fields;
  if (dow === '*') return `cron(${minute} ${hour} ${dom} ${month} ? *)`;
  if (dom !== '*') {
    throw new Error(`schedule ${JSON.stringify(crontab)} restricts both day fields, which Scheduler cannot express`);
  }
  const days = [...crontabDays(dow, crontab)].sort((x, y) => x - y).map((d) => String(d + 1));
  return `cron(${minute} ${hour} ? ${month} ${days.join(',')} *)`;
}
