# Kiro Factory Manual

The Kiro Factory turns items on a team's task board into released changes without a person driving
each step. It triages new items against the real code, writes a spec when one is needed, builds the
change with tests, and answers review comments and CI failures until the PR is ready.
After the merge it follows the change until it is released, checks it against telemetry, and files
what regressed or did not help as new items.

People stay in charge of the decisions that need them, at the step or steps where each one comes
up (by default, approving a spec and reviewing and merging a PR), and of anything the factory
escalates. Everything else runs on a schedule, one item per run, with
its state kept on the board and the PR where anyone can read it.

This package is the factory for any project. You write an **instance** (your repo, your board,
your rules), render the Manual and the instance into a **bundle**, and run the bundle on a **host**.
Each worker is a coding agent run headless by [Kiro CLI](https://kiro.dev/docs/cli/) unless the
instance picks another harness, or a plain script where no judgement is needed.

**Status.** Early, and shared for people to try and build on: it takes no contributions
(`CONTRIBUTING.md`). There are no numbered releases yet, so an instance pins a commit (Versions),
and the config format can still change between commits.

## Try it

You need Linux or macOS, Python 3.9 or later and git. The Manual itself uses only the standard
library. To see what a factory is made of without setting anything up, render the example instance
and ask a worker what it would run:

```
python3 runtime/render/render.py --instance examples/acme --host examples/acme/hosts/local.json --out /tmp/acme-bundle
python3 /tmp/acme-bundle/scripts/runtime/run_worker.py triage --dry-run
```

The first writes a bundle and prints its manifest (and a note that the example's profile installs
the bundle elsewhere). The second prints the command a triage run would execute and runs nothing;
its `cannotRun` line is there because the example's profile names paths that are not on your
machine.
Read `/tmp/acme-bundle/prompts/triage.md` to see what that agent is told.

## Adopting it

A real factory also needs:

- An agent harness. The default is [Kiro CLI](https://kiro.dev/docs/cli/), which must be installed
  and logged in (or given a `KIRO_API_KEY`); or set up any other harness
  (`integrations/harness/KIND.md`).
- The [GitHub CLI](https://cli.github.com/) `gh`, with the factory account's token (Security), for
  the shipped GitHub board and code host.
- `boto3` 1.36 or later, only for S3 publishing and the AWS host.

Read Security below first. Then point an agent at `factory-setup/SKILL.md`. It asks for what it
needs, writes the instance and the host profile, creates the board's labels, renders the bundle and
tries a worker with you before anything runs on a schedule. `examples/minimal/` is the smallest
instance that renders (a GitHub board and code host, pages on disk) and `examples/acme/` one that
uses most of what an instance can set. The local host (your machine, your scheduler) is the short
path; the AWS host is a guide to infrastructure you write yourself.

If your instance has a build of its own, render there, so a Manual update that breaks the instance
fails the build. `build-tools/build` copies what a render reads, plus a `COMMIT` file, to `public/`
for a build that consumes the Manual as an artifact rather than a checkout.

## Security

**A run can do whatever you give it access to.** An agent worker is a coding agent with every tool
allowed and no approval prompt. It runs as the host's user, in a checkout of your repo, with the
credentials in its environment. What it can reach is set by what you grant: the code host account
and its token, the OS user and machine it runs on, and the secrets your CI gives the branches it
pushes. Nothing in the Manual narrows that. Decide the access first, and treat a run as you would
a CI job that executes the pull request it is testing.

**What a run reads and runs.** It reads text other people write: board items, PR comments and the
repo. On a public repo that is text from anyone: an issue's author can edit it after it is on the
board, and anyone can comment on it or on the factory's PRs. It runs the repo's build and tests on
the branches it works on (its own, and a branch of your repo whose PR a person on the team linked
to an item; never a fork's), and what it pushes runs in your CI.

**What the prompts add.** They tell the agent that item, PR and review text is data, that only
the team directs it (the board and code host skills say who the team is on each platform), never
to run what an item says to run, and never to paste such text into a command line. These are
instructions to a model: they make a steered run less likely, not impossible. The control is the
access above.

**What to set up.**

- **Account.** Give the factory an account of its own on the code host: a machine user that is a
  collaborator on this repo only, and on the board's repo when that is another one. Its token is a
  fine-grained one limited to those repos: Contents, Pull requests, Issues and Actions read and
  write (Actions to re-run a failed check), Commit statuses and Metadata read, and Workflows write
  only if implement may edit CI files. GitHub's fine-grained tokens have no Checks permission, so
  on a private repo whose CI reports check runs the `ci-green` signal cannot read them with one.
  There, use a classic token with the `repo` scope for the same account, which reaches what the
  account reaches, or a GitHub App where your host mints its hourly tokens (the AWS reference in
  `factory-setup/references/aws-cdk/` does). One token for every worker is the simple setup. The host sets each run's
  environment, so it can give a worker a narrower token than implement's: triage, for one, reads
  the repo and writes only to issues. Set the account's login as `botLogins` on the board and the
  code host, so its own posts count as the factory's. Your own login is for a first try only
  (`factory-setup/references/hosting-local.md`).
- **Branch protection.** Require a human review on the default branch, with "Dismiss stale pull
  request approvals when new commits are pushed" and "Require approval of the most recent
  reviewable push" on, and keep the factory's account off every bypass list. As shipped, the
  factory merges only a spec PR that a person approved, and never an implementation PR; without
  those two settings an approval also covers what the factory pushes after it.
- **CI secrets.** What a run pushes runs in your CI as a branch of your own repo, with the secrets
  your workflows give such branches. Keep release and deploy secrets in environments that require
  a reviewer.
- **Host.** Run it as its own OS user, or in a disposable VM per run as the AWS guide describes, on
  a machine that holds no other secrets. A run inherits the environment it is started in. Start it
  with only the variables that worker needs, or list the ones a run keeps in the host profile's
  `env`.
- **Board.** On a public repo, the shipped GitHub board takes only issues carrying the scope label,
  which people with triage access add by hand. An issue template or a workflow that adds the
  label puts anyone's issue on the board, so let neither add it. The label chooses which issues
  are worked, not who can put text in front of a run; a
  private tracker repo narrows that. Set `teamLogins` on the board and the code host when
  organization membership is wider than the people who should direct the factory.
- **Pages.** `disk` publishing authenticates nobody. Put `s3` pages behind your own sign-in unless
  they are meant to be public, on an origin that serves nothing else: agents write them from item
  and PR text. Never serve run records: they carry traces of what a run did.
- **Learn.** Read a patch learn proposes before applying it: an agent that read item and PR text
  wrote it, and it changes the rules every later run follows.
- **Instance.** Keep secrets out of the instance directory: the render copies it into the bundle.

Report a vulnerability as `CONTRIBUTING.md` says, not in a public issue.

## The loop

```
  discover ───▶ triage ───▶ design
      ▲                        │
      │                        │        ◀──  learn
      │                        ▼
   monitor ◀── release ◀── implement
```

- **discover** files new items from monitor's regressions and learn's product findings.
- **triage** reads the code an item touches, classifies it and routes it.
- **design** drafts requirements (and a design when engineering needs one) in a draft PR, revises
  it from review comments, merges it once a human approves it with a review, and then hands it to
  implement.
- **implement** builds in a new PR off the default branch, runs the repo's own checks and reviewers
  on its diff, then answers review comments, fixes CI and resolves mechanical merge conflicts until
  the PR is ready for a human. It escalates after a bounded number of rounds on the same problem.
- **release** finds each item whose PR merged, whoever built it, follows the change through the
  instance's release channels and closes it.
- **monitor** gives each released change a verdict from the instance's telemetry.
- **learn** reads the human corrections since its last run and proposes changes to the repo's agent files or
  the instance; a human makes them.
- **janitor** runs beside the loop: stale claims and unanswered items.
- **report**, off by default, publishes one page of what waits on a person, the board over time
  and each worker's health.

A module adds more workers, and skills for these; see Modules.

Each message the factory posts starts with a fixed header and the id of the run that wrote it.

## Layout

| Path | What it is |
|---|---|
| `workers/<step>/` | One directory per step in The loop: `triage`, `design`, `implement`, `release`, `monitor`, `discover`, `learn`, `janitor`, `report`. |
| `workers/common/` | Rules every prompt includes: writing (the message header and style), linking PRs, reading human input, claims, git, guardrails, risk levels, the label vocabulary. |
| `workers/lib/` | Helpers the scripts import, including `records.py`, which reads a post's attribution header and PR links. |
| `workers/signals/` | Verification signals the steps share: `ci_green.py`, and the `reviews-converged`, `agent-review` and `risk` skills. |
| `integrations/<kind>/` | The platforms the workers talk to, and the agent harness that runs them. Each kind (`board`, `codehost`, `publishing`, `notifications`, `harness`) defines typed capabilities in `KIND.md`; each `<kind>/<name>/` implements them on one platform, as a skill for the agent-facing kinds. `harness/kiro-cli` is the default harness. |
| `runtime/render/` | `render.py`, which turns the Manual, an instance and a host profile into a bundle, and the instance config schema. |
| `runtime/run/` | `run_worker.py`, which runs one worker once on any host. |
| `hosting/<host>/` | Where workers keep their records and leases, per host: `aws-cdk` (S3, for a host in AWS) and `local` (files on your machine). That module is all the Manual runs of a host: the schedules, and in AWS the infrastructure, belong to the instance, and `factory-setup/` is the guide to them, with a reference CDK app to copy for AWS. |
| `factory-setup/` | A skill that walks an agent and a person through setting up a factory. |
| `examples/acme/` | A complete instance for a fictitious project, as a reference. |
| `tests/` | The Manual's tests (Developing the Manual). |

## Steps

A step is a directory under `workers/`, and a worker of the same name runs it. `defaults.json`
holds the step's defaults: `enabled` (off for monitor, discover and report), `implementation`, the paths of its implementations
(`agent`, a prompt; `script`, an executable), `requires`, `schedule`, timeouts and `settings`.
A worker's claim TTL is its timeout plus 5 minutes; the renderer writes it into `workers.json`.
Schedules and timeouts are starting points; an instance should set its own.

A prompt opens with its contract (`## Inputs`, `## Outputs`, `## Verification`, `## Decisions`),
then the procedure. Inputs say what the step reads and how it checks it; Verification names the
signals that check its output; Decisions say when it retries (and how often), and their
`### Escalate`, where a step has one, lists every place the step stops for a person. An instance adds its own in its rules
or skills, naming the step. A script's docstring carries
the same four. A passthrough input check
names the gate it trusts with a `relies-on:` line, and the render fails when that gate is not
configured.

The instance's `workers.<name>` overrides any default. Its `agent` and `script` paths replace the
Manual's (relative to the instance), and `implementation` picks which one runs, so an instance can
run a script step as an agent, or the reverse, with its own file. A worker name the Manual does not
ship adds an instance worker.

## Verification

A step decides whether to proceed, iterate, wait or ask a human from its verification signals.
`verification` in `defaults.json` lists them, each `{"skill": <name>}` or `{"script": <path>}`. The
Manual's signals live in `workers/signals/`, so steps share them:

```json
"verification": [{"script": "ci_green.py"}, {"skill": "reviews-converged"}, {"skill": "agent-review"}, {"skill": "risk"}]
```

A script runs with the subject (for implement, the PR number) as its argument; a skill is loaded on
every run. Each returns plain text and the step's prompt weighs them. `workers.<name>.verification`
adds to the list, and an entry naming a default with `"enabled": false` turns that one off. So a
project without CI on its code host runs its own checks instead
(`[{"script": "ci_green.py", "enabled": false}, {"script": "scripts/run_tests.sh"}]`), and an external test such as QA in a real environment is a
worker of its own that posts on the PR, plus a signal skill that reads its post. `agent-review` has a
subagent review the PR and posts the review; a project whose CI already runs an agent reviewer turns
it off. A script signal
runs inside the worker's run and counts against its timeout.

## Risk

Every item triage routes carries one of five risk labels, very low to very high
(`workers/common/risk.md`). Triage sets the level
from its axes. The `risk` signal re-judges it in implement and release from
the change as built and the other signals' readings, so an instance adds evidence by adding signals.
A person's change to the label is final. For now the level is recorded, not acted on: no step changes
what it does by level, so the labels and the comments that change them build a history to audit before any
rule depends on them. An instance maps its own axes and evidence to levels under a `risk-levels`
heading (Instance rules and skills).

## Links

Workers depend on each other through as little exact text as possible. Agents read each other's
posts as prose. Scripts read only a post's attribution header, its PR link lines
(`workers/common/links.md`, read by `workers/lib/records.py`) and the labels.

## Modules

A module adds to a factory without the Manual knowing about it: workers of its own, and what existing
workers get. The instance turns a module on in its config, `"modules": ["weekly"]`, and the render is
told where it is, `--module weekly=<dir>`. Modules apply in the config's order.
`examples/modules/weekly/` is the module described here.

A module directory holds `module.json`, plus the files it names (scripts, prompts, context files), a
`skills/` directory and an optional `integrations/` directory. `module.json` has one key, `workers`, in
the instance's `workers` format, and may have a `_README` note:

```json
{"workers": {
  "weekly-summary": {"implementation": "script", "script": "workers/weekly-summary/summary.py",
                     "schedule": "0 9 * * 1", "requires": ["board"], "settings": {"maxItems": 20}},
  "weekly-notes": {"implementation": "agent", "agent": "prompts/weekly-notes.md",
                   "schedule": "0 10 * * 1", "requires": ["board"]},
  "triage": {"context": ["context/summary-lines.md"], "skills": ["summary-tags"]}
}}
```

- **A worker of its own** is an entry with an `implementation`, declared in full as an instance
  would: `agent` or `script` (relative to the module), `schedule`, `requires`, timeouts, and
  `settings` with their defaults. It must not be a Manual step or another module's worker. Its
  prompt takes the same directives as a step's (see Instance rules and skills), including
  `<!-- include: instance -->` for the instance rules, and reads its settings as
  `{{worker.settings.<name>}}`.
- **An existing worker** (a Manual step or the instance's) takes only `context`, `skills` and
  `settings`, which add to what it has. The instance config sets anything else.
  - `context`: files in that agent's context from the start of every run. Use it for rules that must
    hold on every run, such as how to write what the module's scripts read. The harness has limits
    (kiro-cli: 32 files and 100,000 characters per worker, 50,000 per file), and the render fails
    past them.
  - `skills`: names of skills in the module's `skills/<name>/SKILL.md`, with `name` and `description`
    frontmatter, disclosed the harness's way: the name and description up front and the body when
    the agent needs it. A module skill ships only for workers that name it. Its name must differ
    from the Manual's skills (the integrations', `workers/signals/` and `workers/skills/`; the
    bundle's `manifest.json` lists them) and from other modules'.
  - `settings`: values for settings the worker's step lists.
- **An integration** is a directory `integrations/<kind>/<name>/` in the Integrations format, for a
  kind the Manual defines. The instance picks it in its config with `"use": "<name>"`, as it would a
  Manual integration; an instance integration of the same name wins. Its name must differ from the
  Manual's integrations and other modules'.
- **The instance wins.** Its config merges last: it can turn off or retune a module's worker, and
  only it sets verification signals. A host's `workerOverrides` skip a worker the instance does not
  have, so a host can tune a module's worker for the instances that turn the module on.
- **Modules stand alone.** A module depends only on the Manual: it neither declares nor adds to
  another module's worker, and nothing in the Manual names a module.

In the bundle, a module's prompts go to `prompts/<worker>.md`, its scripts to `scripts/`, its skills
to `skills/<name>/`, its context files to `modules/<module>/` and its integrations where the Manual's
go. `manifest.json` records each module's git commit, empty for a module outside git.

## Instance rules and skills

- `rules.md`: short rules every run needs. The renderer puts it, fenced, where a prompt has
  `<!-- include: instance -->`. Every step's prompt has it; an instance's or module's own prompt
  needs the line too.
- `skills/<name>/SKILL.md`: everything else, as [Agent Skills](https://kiro.dev/docs/skills/). The
  agent loads a skill's name and description up front and its body, references and scripts only
  when the run needs them, so project knowledge does not pile up in every prompt. The harness
  discloses skills its own way: each worker's agent lists the skills it names (its step's,
  `workers.<name>.skills` and its signals'), every integration's, and each instance skill no worker
  names.

Three headings are read by name, in whichever instance skill has them; a step uses its own
defaults for one the instance leaves out. `examples/acme/skills/widgets-triage/` shows all three.

| Heading | Read by | What it holds |
|---|---|---|
| `verification-levels` | triage | The project's ladder of test rigor, lowest first, and which level is the fast-track ceiling. |
| `axis-taxonomy` | triage | The project's public contracts, config surfaces and security surfaces, for the design and security axes. |
| `risk-levels` | triage and the `risk` signal | How the project's own axes and evidence map to risk levels. |

`factory-config.json` holds only what a script or the renderer reads, and ids and addresses that must
be exact. Anything only an agent reads goes in rules or skills, including a different value for a
number a prompt states ("the design sync cap here is 5").

An instance may state its `goal` in the config: what the factory is for there, in a few plain
sentences. The renderer puts it beside the instance rules in every prompt, where it guides choices a
step leaves open and never overrides a guardrail or a person's decision. Learn judges each run's period
against it, and its page opens with the goal and that assessment.

The Manual ships skills too: each configured integration (with its kind's capability contract as
`references/capabilities.md`), the signal skills under `workers/signals/` and the step skills under
`workers/skills/`. An instance skill wins over a Manual skill of the same name.

Prompt directives: `<!-- include: common/<name> -->`, `<!-- include: integration <kind> -->` (a
pointer to that integration's skill), `<!-- include: instance -->`, `<!-- include: verification -->`, and values `{{config.*}}`,
`{{label.*}}`, `{{host.*}}`, `{{worker.*}}`, `{{instance.*}}`, expanded in prompts,
rules and skills. The `render.py` docstring is the reference.

## Integrations

Prompts name capabilities such as `board.list(scope, filters)`, never a tool. A capability's
required parameters carry the guardrails every platform must honour: a board read always takes the
board scope, because one without it picks up other teams' items. An integration directory holds
`SKILL.md` (named `<kind>-<name>`: how an agent performs each capability on that platform),
`config.schema.json`, and optionally `client.py` for scripts, conforming to
`integrations/<kind>/interface.py`. The Manual ships a board on GitHub Issues, GitHub
as the code host, S3 and disk publishing, and Slack Workflow notifications. An instance adds its own under `integrations/<kind>/<name>/`,
and a module can ship one (Modules).

A platform an agent can reach only through an MCP server ships `mcp_proxy.py` in its integration
directory: a stdio MCP server, started as `<python> mcp_proxy.py` with `FACTORY_BUNDLE` set. Beside
it goes `tool_catalog.json`, `{"tools": [{"name", "description", "inputSchema"}, ...]}`, which the
proxy returns for `tools/list`. The render then gives every agent that server, named after the
kind, and the integration's skill names its tools.

## Bundle and hosts

```
python3 runtime/render/render.py --instance <dir> --host <host profile> --out <bundle>
```

The profile's `paths` are where things are on the machine that runs the bundle
(`runtime/render/host.schema.json`), so `--out` is the profile's `paths.bundle` whenever the render
runs on that machine. The render replaces `--out` whole: it refuses a directory that is neither
empty nor a bundle, or one inside the instance, and a render that fails leaves the previous bundle
as it was. An agent worker refuses to run from a bundle that is not at `paths.bundle`. The checkout,
worktree root and state directory are beside the bundle, never inside it. A run in progress when a
render lands reads the new bundle's prompts and skills from then on.

The bundle is the only thing a host installs: `manifest.json` (the Manual commit, the skills, a
content hash), `factory-config.json`, `vocabulary.json`, `workers.json` (each worker
resolved to one implementation), `host.json`, the harness's own files (for kiro-cli, `agents/<worker>.json`
with the bundle's skills and MCP servers), `prompts/<worker>.md`, `skills/` and `scripts/`. Rendering is
deterministic and fails on an invalid config or host profile, an unconfigured `relies-on` or
`requires`, a schedule that is not a five-field cron expression, a missing implementation, or a
skill whose front matter does not match its directory. A notifications channel the config leaves
out posts nowhere.

Every host runs a worker with `python3 <bundle>/scripts/runtime/run_worker.py <worker>`. It takes the
worker's single-flight lease, runs one headless turn of the agent harness on the rendered prompt
(the harness installs its config and builds the command) or runs the
script, writes a run record and releases the lease. Hosts differ only in how runs are scheduled, how
credentials arrive, and where records and leases live (`hosting/<host>/history.py`). A worker gets
no instructions from its host: any rule it relies on is in the Manual or the instance, so personal
steering must not be loaded into factory runs. kiro-cli loads the running user's, so a host gives
the factory a home directory of its own (`integrations/harness/kiro-cli/README.md`).

## Versions

The Manual has no numbered releases yet. An instance pins it by commit: check the Manual out at
that commit (a submodule works), and every bundle's `manifest.json` records the commit it was
rendered from. The config's `_schema` number changes when an existing config has to be edited, and
the render refuses the old number.

## Developing the Manual

`python3 -m unittest discover -s tests` renders the example, checks every step's defaults and contract,
checks every integration skill covers its kind, runs the script workers and the runner against
throwaway bundles, and keeps the Manual generic: no platform name from `tests/platforms*.txt` in
the prompts, `workers/common/` or the worker scripts, and no UUID or 12-digit account id anywhere
outside `examples/` and `tests/`. The project-name list, `tests/denylist.txt`, ships empty; put your own names in
a `tests/denylist-<name>.txt` or in files `FACTORY_DENYLIST` names, and the same test refuses them.
`build-tools/build test` runs the tests after publishing `public/`. Prompts are run by agents: keep
every bound, guardrail and marker string other runs parse, and never write an instance's specifics
into the Manual.
