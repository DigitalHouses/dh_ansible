# Fleet Vault -> one encrypted passport per site

**State:** offline candidate migration only. This is not production cutover.

## Accepted paths and identity

- PRIVATE Fleet Detail: `inventories/<owner>-<type>/...`
- PRIVATE Fleet Detail: `secrets/<owner>-<type>/vault.yml`
- One whole-file Ansible Vault-encrypted YAML passport per site, grouped by component.
- Current legacy source `secrets/fleet.vault.yml` stays unchanged until **separate accepted cutover**.
- A shared operator-held password is supported today; future per-site passwords can be introduced without changing the path scheme. No password, plaintext or GitHub token may be supplied as a command-line argument.

## Source-aware blockers

Read-only review of the universal code shows:
- legacy `fleet_secrets.sites`;
- legacy `fleet_secrets.shared` (the runtime publisher consumes `truenas_human_admin_password`);
- optional legacy top-level `vault_pve_*_secret` values.
  
Therefore the converter refuses silent loss or distribution of any of these namespaces. Only the explicitly recognized shared TrueNAS admin password may be duplicated with `--duplicate-shared`; unrecognized shared/global fields **block**. Top-level PVE credentials require explicit `--flat-owner OLD_SITE` when present. Conflicting PVE credentials **block**.

The encrypted output envelope is:

```yaml
site_passport:
  schema_version: 1
  site_id: demo-office
  site_secrets:
    pve: {}
    truenas: {}
  shared: {}
```

(The above is a **synthetic structural illustration**, not a real inventory or secret.)

## Offline operator procedure

Work on a **trusted, isolated, non-production workstation**, not inside a normal site runner. Install Python with Ansible Core and PyYAML. Copy the existing encrypted Fleet Vault to a new 0600 root/owner-controlled file. Keep staging in a newly prepared 0700 private directory. Do **not** add plaintext or unencrypted files to Git. All command blocks involving diagnostic output begin with `clear`.

1. Audit the legacy source in memory, without outputting values:

```bash
clear
echo "=== BEGIN · FLEET VAULT SCHEMA AUDIT (READ ONLY) ==="
python3 bootstrap/split-fleet-vault.py \
  --source /private/source/fleet.vault.yml \
  --audit
echo "=== END · NO SOURCE CHANGES ==="
```

The tool requests the existing master password interactively and reports only the counts of site namespaces, shared namespaces, and legacy flat PVE keys. It never prints secrets.

2. Confirm all old-to-new site mappings against PRIVATE inventory metadata **before staging**. Do not copy real owner names, hostnames, addresses or existing site names into public code, tests, or documentation. For a synthetic example, `demo` becomes `demo-office`.

3. After audit, prepare **all sites in one operation** using the complete mappings, an explicit shared-secret decision, and `--flat-owner` only if needed. This is a parameterized pattern, **not a command ready to run as-is**:

```text
python3 bootstrap/split-fleet-vault.py
  --source <private-legacy-ciphertext>
  --prepare
  --map <old-site>=<canonical-owner-type>
  [--map <another-old-site>=<canonical-owner-type>]
  [--duplicate-shared]
  [--flat-owner <old-site>]
  --output-dir <new-private-staging-directory>
```

Output directory must not exist; its parent must be owner-controlled with mode 0700. Output contains only encrypted `secrets/<site>/vault.yml` candidate files plus SHA256 manifest; files are 0600, directories 0700. The converter decrypts and roundtrip-verifies every candidate **in memory** before writing any candidate.

4. Acceptance gates before any data-only Fleet Detail PR: check new ciphertext on a fresh clone, verify each site passport's exact site identity and payload equivalence, ensure no secrets appear in logs/CI, audit consumer compatibility and Vault ID/password usage, and confirm none of the old paths are still required by active deployments. Merge only after explicit owner acceptance. Do not remove or rewrite the existing source Vault history; preserve it privately for rollback under normal retention and access policies.

## Implementation guardrails

- The consumer role `roles/fleet_secrets` accepts both the new `site_passport` envelope and the legacy Vault during transition; validates `site_passport.site_id == site.id`; keeps `no_log: true`.
- The current legacy `bin/ansimble-run` and legacy secret-writer modules still require the old aggregate Vault and **must not be used as site-passport writers** before their separate migration.
- The existing Semaphore design uses site-local inventory, with no Fleet Git key or master password delivered to the guest. The site passport must also **never** be delivered whole to the guest.
- No production, PVE, LXC, TrueNAS, Cloudflare, or GitHub Fleet Detail source mutations are authorized by this offline converter.

## Canonical identifier release gate

The bootstrap verifies `site.id`, `site.owner_slug` and `site.type` against the `<owner>-<type>` command-line site ID **before any guest write**. Old `amir` and `home` identifiers intentionally block new deployments until their complete inventory, runner hostname, references and secrets have been migrated and accepted. This guard is not a migration executor.
