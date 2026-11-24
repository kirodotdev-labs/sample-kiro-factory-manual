# Host the factory in AWS

The Manual runs one piece of the AWS host, `../../hosting/aws-cdk/history.py`, which keeps run
records and leases in S3; the infrastructure below belongs in the instance's package. `aws-cdk/`
beside this guide is a reference CDK app built to it: copy it there and adapt it (`bin/app.ts`
holds the settings, `lib/settings.ts` describes them), or write the same in whatever the user
already deploys with. Nothing depends on the reference in place. Start on the local host if you
only want to try the factory.

The shape: one Lambda MicroVM per worker run, started by a launcher Lambda that one EventBridge
schedule per worker invokes, with pages, run records and worker state in one S3 bucket behind
CloudFront. Each run gets its AWS credentials from a token vending machine, scoped to its worker.

## Before deploying

- **Account and region**: a region with Lambda MicroVMs, bootstrapped for the deploy tool. Show
  the user the account, region and principal (`aws sts get-caller-identity`) and deploy only once
  they confirm it; an account of the factory's own is best.
- **A Kiro API key** (`ksk_...`) for a Kiro user of the factory's own; credits bill to it. With
  a profile that has API keys on, mint one and skip the rest of this item. Keys come from a Kiro
  profile on IAM Identity Center with API keys turned on in its settings (off by default), and
  profiles exist only in us-east-1 and eu-central-1. With no profile, create an Identity Center
  account instance there (RETAIN: deleting it cancels every subscription and key on it), then
  the user (`aws identitystore create-user --identity-store-id <id> --user-name <u>
  --display-name <d> --name GivenName=<g>,FamilyName=<f> --emails Value=<email>,Primary=true`). In
  the Kiro console create a profile on the instance, subscribe the user directly (a group
  subscription can take a day), turn on API keys, and mint one.
- **A GitHub App** as the factory's account (README, Security, for its permissions, plus Checks
  read), installed on the repo, with its id, installation id and a private key. It must hold
  everything `githubPermissions` asks for: GitHub refuses a token that asks for more, so every run
  of that worker fails. The token vending machine keeps the key and gives runs installation tokens
  limited to the repo, each lasting an hour, so no run holds the App's key. `botLogins` is `<app slug>[bot]`.
- **Viewer authentication for the pages**, or public pages for a public project. Ship none of your
  own: use what the organization already has, and have new auth code security reviewed before it
  serves pages. Until then, deploy the distribution locked (a viewer-request function that answers
  403) and say so. Cognito in front of CloudFront needs Lambda@Edge in us-east-1 (bootstrap it; no
  environment variables, so the pool, client and domain are built into the code), a public app
  client with the authorization-code grant whose callback is set once the distribution exists, and
  a function that redirects, exchanges the code, checks the JWT against the pool's JWKS and sets a
  cookie scoped to the distribution. Every run can publish under the page prefixes, so script a run
  writes runs for every viewer: keep the pages on a domain of their own, and make the auth cookie
  HttpOnly and scoped to the distribution.

## Resources

- **Bucket**: private, versioned with old versions expiring after about 30 days (every run rewrites
  its keys), a fixed name such as `<name>-pages-<account>`, and RETAIN. Pages sit at the bucket
  root (publishing `prefix` empty) under `items/`, `learn/`, `discover/` and `report/`, and any a
  module adds; the host
  keeps run records and leases under `runs/` and worker state under `state/`. Nothing deletes a run
  record, so give `runs/` a lifecycle rule that expires objects after about 90 days: at least as
  many as the report's `days` setting and learn's window.
- **Distribution**: origin access control with a bucket policy that lets it read only the page
  prefixes (run records carry traces, and path patterns alone are a thin wall), `runs/*` and
  `state/*` refused as well, caching off (keys are rewritten in place), and the content security
  policy `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src
  'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, whose first four the `s3`
  skill's page packaging assumes.
- **Secrets**: the Kiro key, the GitHub App's private key (readable by the token vending machine
  only) and, with a webhook, its URL. Created empty and
  RETAIN; their values never go through the deploy. (CDK's `Secret` generates a value; use
  `CfnSecret` with neither `SecretString` nor `GenerateSecretString`.)
- **Execution role**, the MicroVM's, trusted by `lambda.amazonaws.com` for `sts:AssumeRole` and
  `sts:TagSession` (MicroVMs need both). Anything in the VM that reads instance metadata gets it,
  a steered agent included, so it may only write the run log group and invoke the token vending
  machine.
- **Runtime role**, what each run acts as, trusted only by the token vending machine's role. Get
  and put on the page prefixes, `runs/` and `state/`; delete only on `runs/*/lock`;
  `s3:ListBucket` with an `s3:prefix` condition on those same prefixes (a missing key then reads as
  403, and the S3 publishing client and `history.py` list the key to tell missing from denied); read
  the Kiro key and webhook secrets; and what the integrations need.
- **Token vending machine**: a Lambda that trades a run token for a session of the runtime role
  scoped to the run's worker. The launcher signs `<worker>.<expiry>` with a KMS HMAC key
  (`GenerateMac`, granted to the launcher only) and the function checks it with `VerifyMac`, then
  calls `sts:AssumeRole` with a session policy that allows puts only on the pages that worker
  publishes (its prompt or script names the keys) and its `runs/<worker>/` and `state/<worker>/`
  keys, delete only on its lease, reads and lists only of the pages and its own `runs/` and `state/`
  keys (learn and the report read all of `runs/`, since they read every worker's records), the
  statements every worker shares (the secrets, the integrations' grants), and what only that worker
  uses, such as a telemetry role. The policy is an allowlist the role also grants, built from the
  same statements, so anything it doesn't list is refused. Name each
  session for its launch (the worker and the token's expiry), so CloudTrail tells runs apart. A
  role assumed from a Lambda's role is role chaining, so the session lasts at most an hour: return
  it in `credential_process` output, and SDKs call again before it ends. For the same token it
  mints a GitHub App installation token with that worker's permissions (read only for a worker
  that writes nothing) on the repo and, with a board on GitHub Issues, the board's repo, signing
  the App's JWT with the key only it reads. The execution role can't end
  its MicroVM, so the function also terminates the MicroVM a run names (`lambda:TerminateMicrovm`
  on the image). A token proves the worker, not the MicroVM, so a run can still end another run's
  MicroVM.
- **Image** (`AWS::Lambda::MicrovmImage`; every property is required): ARM_64 on the managed
  base `arn:aws:lambda:<region>:aws:microvm-image:al2023-1` at a pinned `BaseImageVersion`
  (`aws lambda-microvms list-managed-microvm-image-versions`), built by Lambda from a code asset
  zip with the Dockerfile at its root, the run server and the bundle rendered for this host at
  `/opt/factory` (run `render.py` from the Manual checkout or published tree at synth). It needs a
  build role that can read the asset and write the build log group named in `Logging`,
  INTERNET_EGRESS at build, `AdditionalOsCapabilities: []`, ready and run hooks ENABLED on the
  server's port, and at least 4096 MiB (more when implement builds a large repo). Its environment
  variables hold names only: region, bucket, secret ids, the bot's git name and email. The image
  rebuilds only when its asset changes, which is also the only time kiro-cli moves. Each rebuild
  adds an image version, the resource never deletes old ones, and an image holds a limited number,
  so deploys start failing with "reached the maximum number of versions". After each image update,
  run a custom resource that deletes all but the newest few versions, never the image's
  `LatestActiveImageVersion`, and does nothing on delete; one keyed on a version deletes the live
  one when a deploy rolls back. The image takes one version change at a time, so wait for it to
  leave `UPDATING` between deletes. A MicroVM keeps running when its version is deleted.
- **Launcher**: a Lambda that calls RunMicrovm with the execution role, the AWS-managed connectors
  `arn:aws:lambda:<region>:aws:network-connector:aws-network-connector:NO_INGRESS` and
  `...:INTERNET_EGRESS`, a maximum duration (2 hours), `{"worker": "<name>", "token": "<run
  token>"}` as the run hook payload and the run log group. Give the MicroVM no idle policy: idle
  time counts only inbound traffic, so a run that only calls out would be suspended. A Lambda runtime's SDK may
  predate RunMicrovm, so sign it yourself: `POST https://lambda.<region>.amazonaws.com/2025-09-09/microvms`,
  SigV4 service `lambda`. It needs RunMicrovm on the image,
  PassNetworkConnector on both connectors, PassRole of the execution role to Lambda, and
  `kms:GenerateMac` on the token key. A token expires with its worker's timeout from
  `workers.json` plus about 30 minutes for start-up and uploads, so a leaked one stops working soon
  after its run would have.
- **Schedules**: one EventBridge Scheduler schedule per worker in the bundle's `workers.json`, in
  its `timezone`, invoking the launcher with the worker's name, created disabled until the first
  runs pass. Scheduler's cron has six fields, numbers weekdays 1-7 from Sunday and needs `?` in one
  day field, so refuse a crontab line that restricts both. Drop deliveries older than about 10
  minutes; a repeated one only skips on the lease.

## The image

- `FROM public.ecr.aws/lambda/microvms:al2023-minimal` with git, unzip, python3.12 (its
  python3 is 3.9, older than boto3 supports) with boto3 1.36 or later, and the toolchain the repo's
  CI config installs. Pin everything the image downloads by version and sha256: gh, kiro-cli's
  `kirocli-aarch64-linux-musl.zip` (version and sha256 from
  `https://desktop-release.q.us-east-1.amazonaws.com/latest/manifest.json`; the glibc build needs
  glibc 2.39, AL2023 has 2.34), and the Python packages with their dependencies through a
  `--require-hashes` requirements file. `HOME` must be writable: the harness installs its agent
  config there.
- The run server answers POSTs to `/aws/lambda-microvms/runtime/v1/ready` and `.../run`. Lambda
  snapshots the image once ready answers, so nothing reads a secret or creates an AWS client before
  run: whatever exists then is in every MicroVM.
- The run hook times out within 60 seconds, so it returns at once. Its body is `{"microvmId",
  "runHookPayload"}`, the payload being the launcher's JSON as a string; the id is what the run
  asks the token vending machine to terminate. In the background: write an AWS config whose
  `credential_process` invokes the token vending machine with the token, as the execution role read
  from instance metadata, and point `AWS_CONFIG_FILE` at it, so the run and everything it starts
  use the scoped session (a host profile `env` list must name `AWS_CONFIG_FILE`, or runs lose it);
  read the Kiro key into `KIRO_API_KEY`, and the webhook into the profile's `webhookFile` (mode
  0600); put a `gh` in front of the real one that fetches an installation token from the token
  vending machine and reuses it under an hour; clone the repo to `paths.checkout` with gh as git's
  credential helper and the bot's name and email as local config; download `state/<worker>/` into
  `paths.stateDir`; run `python3.12 /opt/factory/scripts/runtime/run_worker.py <worker>` with a
  timeout under the maximum duration; when it exits 0 or 1, upload `state/<worker>/` (uploads only
  add and overwrite, so a deleted state file comes back); have its MicroVM terminated.
- `run_worker.py`'s exit status says whether it wrote the record (its docstring), and it masks the
  factory's own secrets (the Kiro key, code host and AWS tokens in its environment, integration
  secret files) and token shapes in the record it writes. It does not know the run token or the
  vended AWS keys, so a run that prints one leaves it in that record until it expires (the run
  token with the run, the session within the hour). When it did not write one, write one to
  `runs/<worker>/<start>.json` with the fields `history.py` reads. Mask the secrets, the run token
  and installation tokens (by their `ghs_` shape) in the run server's own output and that record,
  masking before truncating. Accept the run hook once: the run's own processes can reach the port.
- A `smoke` payload checks that kiro-cli answers and gh reads the repo, and runs no worker.
- Never run `kiro-cli login` or `kiro-cli agent upgrade` in the image or a run.

## Deploy

1. Copy `../../hosting/aws-cdk/host.example.json` to the instance's `hosts/aws-cdk.json`. Set the
   instance's `integrations.publishing` to the bucket, its region and an empty `prefix`, with a
   placeholder `baseUrl`. A webhook channel's `webhookFile` goes in the host profile's
   `integrationOverrides` (its `_README`); both channels share the one webhook secret. The
   `rules.md` line: "git uses the credential helper the checkout is configured with, and gh fetches
   its own token; never set `GH_TOKEN` or a credential helper."
2. Deploy the bucket and distribution as their own stack first, and put the distribution's URL in
   `baseUrl`: the rendered bundle carries it. This deploy and the next ask to approve their IAM
   changes; the user answers in their own terminal, and the prompt stays on.
3. Deploy the rest. The image builds for several minutes; read its build log if it fails.
4. Store each secret from the user's own shell, never through a file or chat, and not as an
   argument, which stays in the shell's history: `aws secretsmanager put-secret-value --secret-id
   <secret> --secret-string file:///dev/stdin`, then paste the value and press Ctrl-D.
5. Invoke the launcher with `smoke`, then `triage` on an item the user files (`aws lambda invoke
   --function-name <launcher> --cli-binary-format raw-in-base64-out --payload '{"worker":"<worker>"}'
   out.json`). It returns at once and the run takes minutes. Read its record under `runs/<worker>/`
   and its log, and the item's triage comment.
6. Enable the schedules once the user agrees, after checking with them the README Security
   settings the repo needs.

Commit the lockfile your first install writes with your copy of the app (the reference ships
none), since the token vending machine's bundle holds what reads the App's key. For an audit trail beyond CloudTrail's management events, turn on S3 data events or
server access logs for the bucket, and CloudFront standard logs, at the cost they carry.

## Stopping a steered run

Ending the MicroVM revokes nothing: a run's execution-role credentials and its token keep vending
sessions and GitHub tokens until the token expires. To cut a run off:

1. Set the token vending function's reserved concurrency to 0. No run gets new credentials, and
   every run fails within the hour its last session has left.
2. To end what is already vended, revoke the active sessions of the runtime role and the MicroVM
   execution role (IAM console, each role's "Revoke active sessions") and suspend or uninstall the
   GitHub App, whose tokens otherwise last their hour.
3. Rotate the Kiro key, the App's private key and any webhook URL the run could read.
4. Put the concurrency back once the cause is fixed and the run's token has expired: the launcher
   logs `expiresAt`, at most 2 hours 10 minutes after launch. To restore sooner, point both
   functions at a new run-token key; re-enabling a disabled key makes its unexpired tokens valid
   again.
