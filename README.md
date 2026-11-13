# Rakazo OCI deployment foundation

Reusable infrastructure deployment code for a self-hosted Rakazo server on an owner-approved Oracle Cloud Always Free target.

This project is being prepared and reviewed. It is not yet an enabled deployment.

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

Start with validation-only mode. A successful syntax or mock test is not proof that a deployment can run. Verify authentication, inventory, target, permission scope, output privacy, state persistence and stopping behavior before enabling scheduled submissions.

GitHub schedules can be delayed or dropped. Keep a separate read-only liveness check; do not run another launcher in parallel. Stop if GitHub billing blocks execution, rather than enabling paid overage.

This is a deployment foundation, not an installed Rakazo application, security audit or guarantee of sustainable free hosting. Application installation and end-to-end checks follow host readiness.

## Current free-policy gate

The runner checks Oracle's published Always Free page before each submission. It requires the documented 1,500 OCPU-hour, 9,000 GB-hour and 200 GB allowances, Ubuntu eligibility and home-region rule. A source change or fetch failure stops submissions for review. This is an extra guard, not a price quote, billing cap or guarantee that an arbitrary account is eligible. This version is for a separately verified dedicated empty tenancy. Do not use it in a shared tenancy or one with other resource usage.

Source: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

Launch privileges are operation-limited and image-limited. OCI's documented policy variables do not provide a shape/OCPU/RAM spending cap here. The exact shape and size are enforced by the script. Protect repository write access and secrets accordingly. If the script or its credentials are changed or exposed, stop and revoke the disposable credential.

`config.example.json` documents the secret's format with placeholders only. Copy it outside the repository, verify every account/region/resource field, and put the resulting JSON into the encrypted `DEPLOY_CONFIG` Actions secret. Never commit the populated copy. `cloud-init.example.yml` is a starting point for SSH-key-only access, root/password-login restrictions, firewall, fail2ban and security updates. Actual completion must be checked over SSH after cloud-init finishes; RUNNING alone does not prove hardening or application readiness.

The API private key goes only in the encrypted `OCI_KEY_PEM` secret. The SSH private key never belongs in this project or its Actions secrets. Use a dedicated key and keep it with the operator who will validate the host.
