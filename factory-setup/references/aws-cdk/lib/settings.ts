import * as iam from 'aws-cdk-lib/aws-iam';

/** What differs between factories on this host. bin/app.ts sets it. */
export interface FactorySettings {
  /** Prefix of every resource name: the bucket, functions, image, secrets and schedules. */
  readonly name: string;
  /** The Manual checkout or published tree render.py runs from at synth. */
  readonly manualDir: string;
  /** The instance directory. */
  readonly instanceDir: string;
  /** The instance's host profile for this host, from hosting/aws-cdk/host.example.json. */
  readonly hostProfile: string;
  /** The modules the instance's config lists: name -> directory. */
  readonly modules: Record<string, string>;
  /** The key prefixes the factory publishes pages under, including any a module adds. */
  readonly pagePrefixes: string[];
  /**
   * The page keys each worker publishes, as S3 key patterns (its prompt or script names them). A run
   * may put only its own worker's; a worker not listed publishes nothing.
   */
  readonly pageWrites: Record<string, string[]>;
  /** The workers that read every worker's run records (history.run_records), so the only ones that may list all of runs/. */
  readonly runRecordReaders: string[];
  /** The GitHub App the factory acts as, and its installation on the repo's owner. */
  readonly githubAppId: string;
  readonly githubInstallationId: string;
  /**
   * The App permissions each worker's installation token carries (GitHub's names, `read` or `write`).
   * A worker not listed gets read access only. The App must hold at least what is listed here.
   */
  readonly githubPermissions: Record<string, Record<string, 'read' | 'write'>>;
  /** The App's git identity, set on the checkout: `<app slug>[bot]` and `<bot user id>+<app slug>[bot]@users.noreply.github.com`. */
  readonly gitUserName: string;
  readonly gitUserEmail: string;
  /** Memory for each run's MicroVM: at least 4096, more when implement builds a large repo. */
  readonly memoryMiB: number;
  /** Whether a notification channel posts to a webhook, whose URL then gets a secret. */
  readonly webhook: boolean;
  /** Off until the first runs pass (hosting-aws.md, Deploy). */
  readonly schedulesEnabled: boolean;
  /** What every worker's runs need beyond pages, records and secrets, such as an integration's API. */
  readonly runtimeStatements: iam.PolicyStatement[];
  /**
   * What only some workers use, such as a role in another account, by worker. The runtime role grants it,
   * and only that worker's session allows it.
   */
  readonly workerStatements: Record<string, iam.PolicyStatement[]>;
}

export const RUNS_PREFIX = 'runs/';
export const STATE_PREFIX = 'state/';

export const pagesBucketName = (settings: FactorySettings, account: string) => `${settings.name}-pages-${account}`;

/**
 * The repos a run's GitHub token covers, from the rendered config: the code repo, and the board's
 * when the board is GitHub Issues. One installation token reaches one owner's repos.
 */
export function githubRepositories(config: {
  repo: { slug: string };
  integrations?: { board?: { use?: string; boardId?: string } };
}): string[] {
  const repos = [config.repo.slug];
  const board = config.integrations?.board;
  if (board?.use === 'github' && board.boardId && !repos.includes(board.boardId)) repos.push(board.boardId);
  const owners = new Set(repos.map((slug) => slug.split('/')[0]));
  if (owners.size > 1) {
    throw new Error(`the code repo and the board repo have different owners (${[...owners].join(', ')}); one App installation covers one`);
  }
  return repos;
}
