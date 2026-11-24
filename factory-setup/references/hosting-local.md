# Host the factory on this machine

Every run acts as the user it runs as: the operator on a workstation, or a service account on a
shared server. It needs Python 3.9 or later, git, kiro-cli and the code host's CLI on that user's
`PATH`, on Linux or macOS.

## Profile

Copy `../../hosting/local/host.example.json` and replace each `<...>` with a real absolute path;
the render refuses a placeholder. Keep `bundle`, `stateDir` and `worktreeRoot` as siblings under one
factory home: every render replaces the bundle directory whole, and refuses to replace a directory
that is not a bundle. `checkout` is a clone
the factory owns, not the user's working copy. Set the instance's `integrations.harness.agent` to
its name, so a second factory on this machine does not overwrite the first one's agent config.

## Credentials

Runs read their user's environment: kiro-cli's login (or `KIRO_API_KEY`), the code host CLI's
login (or `GH_TOKEN`), and whatever the board and publishing configs name. This host refreshes
nothing, so an AWS profile they name must refresh itself (`credential_process`). A workstation's
shell holds other secrets too, so the profile's `env` lists the variables a run keeps and the run
drops the rest. Fill it with what the integrations above read and what the repo's build reads
(its CI config names them, such as a toolchain's `JAVA_HOME` or an `AWS_PROFILE`), and leave the
rest out. A variable left off shows up on the first run in step 2 below: add it and run again.

- **The factory's own account** (README, Security) is what a factory that keeps running uses:
  its token as `GH_TOKEN` in each run's environment. The `rules.md` line: "gh reads `GH_TOKEN` from
  the environment and git uses gh as its credential helper; never set either."
- **The operator's own login** is enough for a first try, on a repo you can afford to have
  changed. The run is then you: it can reach every repo and organization your login can, with your
  rights, and read what your account can on this machine, SSH keys and cloud credentials included.
  Everything the factory posts appears under your name, and you cannot approve the spec PRs it
  opens, because a code host does not let a PR's author approve it. The `rules.md` line: "git and
  gh use the login already on this machine; set no credential helper and no `GH_TOKEN`."

`disk` publishing's `dir` goes under the factory home. Its pages suit one person; a server put in
front of the directory must authenticate readers. For pages others read, use `s3` and create only
the bucket and distribution from `hosting-aws.md`.

## Personal steering

kiro-cli loads `~/.kiro/steering` into every run, so runs follow the operator's personal rules; tell the user.
A factory that keeps running runs as its own OS user or under its own `HOME` (`env HOME=<home>` on
every entry and first run), with its own `KIRO_API_KEY`, `GH_TOKEN` and AWS credentials.
`KIRO_HOME` moves only kiro-cli's `~/.kiro`, which is enough to keep personal steering out.

Runs on this host share one user, so one run can change what the next one does: the checkout (its
git config and hooks), the state directory and `~/.kiro` are all writable to it. Re-clone the
checkout after a run you do not trust. The AWS host gives every run a fresh VM. Never run
`kiro-cli login` there unattended: it waits for a browser.

## Run it

1. Render: `python3 ../../runtime/render/render.py --instance <dir> --host <profile> --out <bundle>`,
   with `<bundle>` the profile's `paths.bundle`: the prompts and the agent config name that path.
2. From a normal shell (the first agent run installs the agent config under `~/.kiro/agents/`):
   `python3 <bundle>/scripts/runtime/run_worker.py triage --dry-run` (prints the command and where it
   runs, runs nothing), then `triage --item <id>`, and read the item's triage comment.
3. Schedules, with the user's go, made on the host as the user the runs run as: one entry per
   worker in `<bundle>/workers.json` at its `schedule`, running `<python>
   <bundle>/scripts/runtime/run_worker.py <worker>`.
   - `PATH` has kiro-cli, the code host CLI and the repo's toolchain; schedulers start with a thin one.
   - Name entries `factory.<instance name>.<worker>`, so two factories don't collide.
   - crontab and launchd use the machine's clock and launchd can't restrict both day fields; a
     worker in another `timezone` needs systemd. A shared server uses system units with `User=`
     and a 0600 `EnvironmentFile=` of secrets.
   - Exit 4 is a skip on a held lease (systemd: `SuccessExitStatus=4`).
   - Until publishing works, schedule only triage and janitor.
   - Every run leaves a record under `<stateDir>/runs/<worker>/`, and nothing deletes them. Add an
     entry that prunes old ones, such as `find <stateDir>/runs -name '*.json' -mtime +90 -delete`
     daily; keep at least as many days as the report's `days` setting and learn's window.

Re-render after any instance or Manual change; the schedules keep pointing at the same bundle. A
render that fails leaves the bundle as it was. A run in progress when a render lands reads the new
prompts and skills from then on, so render between runs when the change is more than wording.
