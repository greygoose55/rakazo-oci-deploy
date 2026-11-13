# Rakazo OCI deployment foundation

Reusable infrastructure deployment code for a self-hosted Rakazo server on an owner-approved Oracle Cloud Always Free target.

The workflow supports owner-only dispatch. An optional external timer source is included below. Configure and verify the dedicated free-only target before enabling it in your own repository. Forks must not inherit any credential or account configuration.

## Privacy

The public repository contains generic code only. Never commit account configuration, private keys, SSH keys, instance IDs, IP addresses, user data, or provider response logs. Use repository Actions secrets for deployment configuration and credentials. Do not enable pull request or fork triggers for credential-bearing work.

## Safety design

- Exact approved A1 Flex target: 1 OCPU, 6 GB RAM, 50 GB boot volume.
- The owner must verify current Always Free entitlement in the intended home region before enabling. This code does not make an account eligible for free resources or guarantee capacity.
- Reconcile current instances and volumes before submitting. No blind repeat after an ambiguous outcome.
- Automatic SDK retries are disabled. A persistent generic deployment marker is committed before each create call. Interrupted requests stop further submissions for private review.
- Three attempts per ten-minute cycle, spaced three minutes apart. A definite capacity error can continue; throttling, authentication errors, unexpected responses and uncertain state pause the workflow.
- Stop once the exact intended host is confirmed RUNNING. Preserve the public reusable source. Revoke only disposable credentials created for this deployment after private verification.
- Public logs must contain generic status only. Never print raw exceptions or SDK debug output.

## Setup requirements

Use a separate non-admin OCI identity with only deployment and inventory permissions. Never place an owner/admin API key in a public project's automation. Configure `DEPLOY_CONFIG` and `OCI_KEY_PEM` as repository secrets. Keep SSH private keys outside GitHub.

Start with validation-only mode. The offline safety tests run before every hosted deployment. A successful syntax or mock test is not proof that a deployment can run. Verify authentication, inventory, target, permission scope, output privacy, state persistence and stopping behavior before enabling scheduled submissions.

GitHub schedules can be delayed or dropped. Keep a separate read-only liveness check; do not run another launcher in parallel. Stop if GitHub billing blocks execution, rather than enabling paid overage.

This is a deployment foundation, not an installed Rakazo application, security audit or guarantee of sustainable free hosting. Application installation and end-to-end checks follow host readiness.

## Current free-policy gate

The runner checks Oracle's published Always Free page before each submission. It requires the documented 1,500 OCPU-hour, 9,000 GB-hour and 200 GB allowances, Ubuntu eligibility and home-region rule. A source change or fetch failure stops submissions for review. This is an extra guard, not a price quote, billing cap or guarantee that an arbitrary account is eligible. This version is for a separately verified dedicated empty tenancy. Do not use it in a shared tenancy or one with other resource usage.

Source: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

Launch privileges are operation-limited and image-limited. OCI's documented policy variables do not provide a shape/OCPU/RAM spending cap here. The exact shape and size are enforced by the script. Protect repository write access and secrets accordingly. If the script or its credentials are changed or exposed, stop and revoke the disposable credential.

`config.example.json` documents the secret's format with placeholders only. Copy it outside the repository, verify every account/region/resource field, and put the resulting JSON into the encrypted `DEPLOY_CONFIG` Actions secret. Never commit the populated copy. `cloud-init.example.yml` is a starting point for SSH-key-only access, root/password-login restrictions, firewall, fail2ban and security updates. Actual completion must be checked over SSH after cloud-init finishes; RUNNING alone does not prove hardening or application readiness.

The API private key goes only in the encrypted `OCI_KEY_PEM` secret. The SSH private key never belongs in this project or its Actions secrets. Use a dedicated key and keep it with the operator who will validate the host.

## Optional isolated external timer

`external-timer.mjs` is generic Cloudflare Worker source for a scheduled handler plus a SQLite-backed Durable Object class named `DispatchGate`. It calls GitHub's workflow-dispatch API, not Oracle. It must never receive Oracle credentials. Keep its repository-only Actions read/write token in an encrypted `GH_TOKEN` Worker secret, not in source. A temporary credential used to deploy the timer is separate and should be revoked after setup.

Bind the SQLite Durable Object as `GATE`. Declare its class in deployment metadata as an export with type `durable-object` and storage `sqlite`. Plain bindings are `OWNER`, `REPO`, `WORKFLOW` (for example `deploy.yml`), `ENABLED` (initially `false`) and `BASELINE_RUN` (a privately reconciled completed resource-enabled run ID). The timer expects the paired runner's latest three generic deployment markers to be definite capacity failures newer than that run began. This is not a general-purpose workflow scheduler.

Before enabling, remove any native repository schedule, verify there are no other launchers, and verify all free-plan limits and account permissions. Keep workers.dev, preview URLs, routes and custom domains disabled. No public HTTP trigger is provided. Enable only one ten-minute cron after configuration and secret verification. Free timers can be delayed; propagation alone can take 15 minutes. Prove real scheduled dispatches and complete runner cycles before calling the automation operational.

The single globally named Durable Object uses atomic SQLite state changes before network access and before dispatch. Overlapping ticks cannot both claim the gate. A crash, unknown response, throttling, auth failure, disabled workflow, untracked run or ambiguous dispatch leaves a non-expiring review latch. Never reset a paused/checking/pending latch without reconciling the remote state first. Workers KV is not an atomic substitute. The runner independently retains concurrency, rolling attempt limits, pending request reconciliation and all free-only target checks.

Local safety checks include deterministic SQLite cases and actual Workers-runtime tests for overlapping invocations and cold-restart persistence. These checks are not evidence of a live schedule working. On a verified host or a safety pause, stop the timer. After successful host verification, delete the isolated timer and its state and revoke only its dedicated repository token. Preserve generic source and unrelated credentials.

### Safe cycle completion and scheduling jitter

The runner keeps the maximum of three launch requests in a rolling 600 seconds.
It uses definite failure completion times, with a one-second safety margin, rather
than timestamps recorded before requests. A wait of at most 60 seconds can absorb
small differences in scheduled start or runner startup. A longer wait ends the
cycle without another launch.

A green job alone does not prove that a cycle made three attempts. The runner
records a separate `foundation-cycle-v2` deployment only after a definitive safe
ending. Its payload binds the run, run attempt, commit, outcome and exact attempt
IDs. The completion must have a committed success status. Zero or partial attempt
cycles must explicitly say `rate_limited`; a full `capacity_only` cycle must list
three definite capacity failures. Accepted requests, unknown outcomes, interrupted
cycles and pending markers do not produce safe completion evidence.

The external timer independently checks this evidence before another dispatch.
Missing, stale or inconsistent evidence pauses the timer. It never treats an
ordinary successful job as permission to ignore an unknown infrastructure outcome.

A legacy paused cycle needs a separately reviewed, one-time recovery, not an
ordinary retry. Recovery parameters bind an exact run, commit and prior failed
markers to a reviewed window no longer than 15 minutes. The existing Durable
Object consumes a persistent one-use receipt before it can dispatch. The same
receipt cannot rearm the timer after a crash, lost response, restart or redeploy.
No public HTTP handler can arm or trigger recovery. Reviewers must reconcile live
infrastructure and the exact zero-attempt legacy job before supplying those
parameters. This is not an automatic reset mechanism.
