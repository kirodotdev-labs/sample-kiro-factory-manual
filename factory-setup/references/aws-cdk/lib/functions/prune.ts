import { microvmApi } from './aws-api';

/**
 * Custom resource that deletes old worker image versions after each image update. An image holds a
 * limited number of versions, every worker code change adds one, and the image resource never
 * deletes them, so without this deploys fail once the image is full.
 *
 * It keeps the image's latest active version, which the launcher runs, plus the newest few. Nothing
 * is deleted on a stack delete or rollback. A running MicroVM keeps running when its version is
 * deleted.
 */

export const KEEP_VERSIONS = 3;

/** Every wait ends this long after the handler starts, well inside the function's 5-minute timeout. */
const DEADLINE_MS = 3 * 60 * 1000;
/** No delete starts closer than this to the deadline, so the last one has time to settle. */
const SETTLE_MARGIN_MS = 60 * 1000;

const parts = (v: string) => {
  const p = v.split('.').map(Number);
  if (p.some((n) => !Number.isInteger(n))) throw new Error(`unexpected image version ${JSON.stringify(v)}`);
  return p;
};

/** Newest first, comparing each dot-separated part as a number. */
const newestFirst = (a: string, b: string) => {
  const [pa, pb] = [parts(a), parts(b)];
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pb[i] ?? 0) - (pa[i] ?? 0);
  }
  return 0;
};

/** Every version but the newest `keep`, never `active`. */
export function versionsToDelete(versions: readonly string[], active: string | undefined, keep = KEEP_VERSIONS) {
  return [...versions]
    .sort(newestFirst)
    .slice(keep)
    .filter((v) => v !== active);
}

interface PruneEvent {
  RequestType: 'Create' | 'Update' | 'Delete';
  PhysicalResourceId?: string;
  ResourceProperties: { ImageArn: string };
}

export async function handler(event: PruneEvent): Promise<{ PhysicalResourceId: string }> {
  const arn = event.ResourceProperties.ImageArn;
  const result = { PhysicalResourceId: event.PhysicalResourceId ?? `${arn}/prune` };
  if (event.RequestType === 'Delete') return result;
  // A failed or unfinished cleanup never fails the deploy: the image is already updated, and the
  // next deploy prunes the rest. One deadline bounds every wait, since a Lambda timeout can't be caught.
  const deadline = Date.now() + DEADLINE_MS;
  const deleted: string[] = [];
  try {
    const image = imagePath(arn);
    const active = ((await settled(image, deadline)) as { latestActiveImageVersion?: string }).latestActiveImageVersion;
    if (!active) throw new Error('the image has no active version');
    for (const version of versionsToDelete(await listVersions(image), active)) {
      if (Date.now() > deadline - SETTLE_MARGIN_MS) break;
      await microvmApi('DELETE', `${image}/versions/${version}`);
      deleted.push(version);
      // The image takes one version change at a time and rejects the next with a conflict until
      // this one finishes.
      await settled(image, deadline);
    }
    console.log(JSON.stringify({ image: arn, active, deleted }));
  } catch (e) {
    console.error(JSON.stringify({ image: arn, deleted, pruneFailed: String(e) }));
  }
  return result;
}

const imagePath = (arn: string) => `/2025-09-09/microvm-images/${encodeURIComponent(arn)}`;

async function settled(image: string, deadline: number): Promise<unknown> {
  while (Date.now() < deadline) {
    const got = (await microvmApi('GET', image)) as { state?: string };
    if (!got.state?.endsWith('ING')) return got;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  throw new Error('deadline reached while the image was busy; the next deploy prunes the rest');
}

async function listVersions(image: string): Promise<string[]> {
  const versions: string[] = [];
  let nextToken: string | undefined;
  do {
    const page = (await microvmApi('GET', `${image}/versions`, {
      query: nextToken ? { nextToken } : undefined,
    })) as { items?: { imageVersion: string; state?: string }[]; nextToken?: string };
    if (!Array.isArray(page.items)) throw new Error('version list response has no items');
    versions.push(...page.items.filter((v) => v.state !== 'DELETING').map((v) => v.imageVersion));
    nextToken = page.nextToken;
  } while (nextToken);
  return versions;
}
