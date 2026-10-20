# ADR 0009 — Site runtime secrets on PVE, Fleet master offline

Status: Accepted

Date: 2026-10-07

## Context

Routine Ansible operations need site credentials without requiring the operator
to type the Fleet Vault master password for every audit or apply.

Persisting the Fleet master password on a managed PVE host would collapse the
separation between disaster-recovery secrets and the infrastructure they
protect. A compromise of one site could then expose credentials for the whole
fleet.

## Decision

The Fleet master password remains operator-held and is never stored on PVE,
runner, Git, or TrueNAS.

Each site may publish a **site-scoped runtime secret envelope** to its own PVE
host:

```text
/var/lib/ansible/runtime/<site>.yml
```

Properties:

- owner/group: `root:root`;
- mode: `0600`;
- directory mode: `0700`;
- contains only the selected site's `site_secrets`;
- contains no Fleet master password;
- contains no secrets belonging to other sites.

The runner retrieves this file over the existing pinned PVE SSH transport,
using `sudo cat`, into a temporary `0600` file. The temporary file is removed
by the runner wrapper's trap after the playbook exits.

Routine playbooks prefer this runtime source. If it is unavailable, the wrapper
falls back to the operator-held Fleet master password.

Master-required workflows remain explicit:

- fresh/base TrueNAS apply;
- TrueNAS access recovery;
- SMB password changes;
- explicit runtime-secret publication.

After a successful workflow that changes site secrets, the runtime bundle is
refreshed from the current encrypted Fleet Vault while the operator-held master
password is still available in the same process.

## Security boundary

Compromise of one PVE/runner can expose that site's runtime credentials. It must
not expose the Fleet master password or credentials for other sites.

The runtime bundle is an operational cache, not the disaster-recovery source of
truth. The recoverable source of truth remains:

```text
Git + encrypted Fleet Vault + operator-held Fleet master password
```

## Consequences

Routine audits and reconciliations can run unattended.

A rebuilt runner may require the Fleet master password once if the PVE SSH
transport needed to fetch the runtime bundle is not yet available.

The existing PVE host-write sudo policy is broader than required and should be
narrowed in a separate hardening change without blocking this runtime-secret
architecture.
