#!/usr/bin/env python3
"""Audit and stage per-site encrypted passports from legacy Fleet Vault.

OWNER-ONLY OFFLINE TOOL. No GitHub, PVE, network, guest or production mutation.
Never prints plaintext or passwords. Writes only NEW encrypted candidates.
"""
from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

import yaml
from ansible.parsing.vault import VaultLib, VaultSecret


SITE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
OLD_SITE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
FLAT_KEYS = {
    "vault_pve_audit_token_secret": "audit_token_secret",
    "vault_pve_write_token_secret": "write_token_secret",
    "vault_pve_host_write_ssh_private_key": "host_write_ssh_private_key",
}
# Shared material is NEVER silently distributed to all sites.
ALLOWED_SHARED = {"truenas_human_admin_password"}
MAX_INPUT = 4 * 1024 * 1024


class Blocked(ValueError):
    pass


def private_file(path: Path) -> bytes:
    if path.is_symlink():
        raise Blocked("SOURCE_SYMLINK")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise Blocked("SOURCE_UNAVAILABLE") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= MAX_INPUT):
            raise Blocked("SOURCE_OWNERSHIP_MODE_OR_SIZE")
        data = os.read(fd, MAX_INPUT + 1)
        if len(data) != info.st_size:
            raise Blocked("SOURCE_CHANGED_DURING_READ")
        if not data.startswith(b"$ANSIBLE_VAULT;"):
            raise Blocked("SOURCE_NOT_ENCRYPTED")
        return data
    finally:
        os.close(fd)


def parse_legacy(plaintext: bytes):
    try:
        value = yaml.safe_load(plaintext)
    except yaml.YAMLError:
        raise Blocked("LEGACY_YAML_INVALID") from None
    if not isinstance(value, dict) or not isinstance(value.get("fleet_secrets"), dict):
        raise Blocked("LEGACY_FLEET_SECRETS_MISSING")
    unexpected = set(value) - {"fleet_secrets"} - set(FLAT_KEYS)
    if unexpected:
        raise Blocked("UNKNOWN_TOP_LEVEL_SECRET_NAMESPACES")
    fleet = value["fleet_secrets"]
    if set(fleet) - {"sites", "shared"}:
        raise Blocked("UNKNOWN_FLEET_SECRET_NAMESPACES")
    sites = fleet.get("sites")
    shared = fleet.get("shared", {})
    if not isinstance(sites, dict) or not sites:
        raise Blocked("LEGACY_SITES_MISSING")
    if not isinstance(shared, dict):
        raise Blocked("LEGACY_SHARED_INVALID")
    if set(shared) - ALLOWED_SHARED:
        raise Blocked("UNKNOWN_SHARED_SECRET_NAMESPACES")
    if any(not isinstance(k, str) or not OLD_SITE.fullmatch(k)
           or not isinstance(v, dict) for k, v in sites.items()):
        raise Blocked("LEGACY_SITE_NAMESPACE_INVALID")
    flat = {k: value[k] for k in FLAT_KEYS if k in value}
    return sites, shared, flat


def parse_mapping(items: list[str], old_sites) -> dict[str, str]:
    mapping = {}
    for item in items:
        if "=" not in item:
            raise Blocked("MAPPING_REQUIRES_OLD=NEW")
        old, new = item.split("=", 1)
        if old in mapping or old not in old_sites or not SITE_ID.fullmatch(new):
            raise Blocked("MAPPING_SITE_INVALID_OR_DUPLICATE")
        mapping[old] = new
    if set(mapping) != set(old_sites) or len(set(mapping.values())) != len(mapping):
        raise Blocked("MAPPING_NOT_BIJECTIVE_OR_INCOMPLETE")
    return mapping


def partition(sites, shared, flat, mapping, *, duplicate_shared=False, flat_owner=None):
    if shared and not duplicate_shared:
        raise Blocked("SHARED_REQUIRES_EXPLICIT_DUPLICATION")
    if flat and flat_owner is None:
        raise Blocked("FLAT_PVE_REQUIRES_EXPLICIT_OWNER")
    if flat_owner is not None and (flat_owner not in sites or not flat):
        raise Blocked("FLAT_OWNER_NOT_APPLICABLE")
    results = {}
    for old_site, new_site in mapping.items():
        scoped = copy.deepcopy(sites[old_site])
        if old_site == flat_owner:
            pve = scoped.setdefault("pve", {})
            if not isinstance(pve, dict):
                raise Blocked("LEGACY_PVE_NAMESPACE_INVALID")
            for old_key, new_key in FLAT_KEYS.items():
                if old_key not in flat:
                    continue
                if new_key in pve and pve[new_key] != flat[old_key]:
                    raise Blocked("CONFLICTING_LEGACY_PVE_SECRET")
                pve[new_key] = copy.deepcopy(flat[old_key])
        results[new_site] = {
            "site_passport": {
                "schema_version": 1,
                "site_id": new_site,
                "site_secrets": scoped,
                "shared": copy.deepcopy(shared) if duplicate_shared else {},
            }
        }
    # Every site was partitioned, and every shared/flat namespace handled explicitly.
    assert len(results) == len(sites)
    return results


def audited_vault(path: Path, password: bytes):
    original = private_file(path)
    secret = VaultSecret(password)
    vault = VaultLib([("default", secret)])
    try:
        plaintext = vault.decrypt(original)
    except Exception:
        raise Blocked("VAULT_DECRYPTION_FAILED") from None
    sites, shared, flat = parse_legacy(plaintext)
    return vault, secret, original, sites, shared, flat


def private_output_parent(destination: Path):
    if destination.exists() or destination.is_symlink():
        raise Blocked("OUTPUT_ALREADY_EXISTS")
    parent = destination.parent
    if parent.is_symlink() or not parent.is_dir():
        raise Blocked("OUTPUT_PARENT_UNSAFE")
    info = parent.stat()
    if (info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise Blocked("OUTPUT_PARENT_NOT_PRIVATE")


def stage(destination: Path, vault, secret, original: bytes, passports):
    private_output_parent(destination)
    # Encrypt and verify EVERYTHING in memory before creating output files.
    candidates = {}
    for site, passport in sorted(passports.items()):
        serialized = yaml.safe_dump(passport, allow_unicode=True,
                                    sort_keys=False).encode("utf-8")
        try:
            encrypted = vault.encrypt(serialized, secret=secret)
            verified = yaml.safe_load(vault.decrypt(encrypted))
        except Exception:
            raise Blocked("CANDIDATE_ROUNDTRIP_FAILURE") from None
        if verified != passport or not encrypted.startswith(b"$ANSIBLE_VAULT;"):
            raise Blocked("CANDIDATE_MISMATCH")
        candidates[site] = encrypted
    destination.mkdir(mode=0o700)
    try:
        manifest = {"schema_version": 1,
                    "source_sha256": hashlib.sha256(original).hexdigest(),
                    "sites": {site: hashlib.sha256(value).hexdigest()
                              for site, value in candidates.items()},
                    "state": "CANDIDATES_ONLY_NOT_DEPLOYED"}
        secrets_root = destination / "secrets"
        secrets_root.mkdir(mode=0o700)
        for site, encrypted in candidates.items():
            folder = secrets_root / site
            folder.mkdir(mode=0o700)
            target = folder / "vault.yml"
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
        record = destination / "manifest.json"
        fd = os.open(record, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        shutil.rmtree(destination)
        raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--map", action="append", default=[], metavar="OLD=NEW")
    parser.add_argument("--duplicate-shared", action="store_true")
    parser.add_argument("--flat-owner", metavar="OLD_SITE")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.audit == args.prepare:
        raise Blocked("CHOOSE_EXACTLY_ONE_MODE")
    if args.prepare and args.output_dir is None:
        raise Blocked("OUTPUT_DIR_REQUIRED")
    if args.audit and (args.map or args.flat_owner or args.duplicate_shared or args.output_dir):
        raise Blocked("AUDIT_IS_READ_ONLY")
    password = getpass.getpass("Existing Fleet Vault password (hidden): ").encode("utf-8")
    try:
        vault, secret, original, sites, shared, flat = audited_vault(args.source, password)
        if args.audit:
            print("MODE=READ_ONLY")
            print(f"SITE_NAMESPACES={len(sites)}")
            print(f"SHARED_NAMESPACES={len(shared)}")
            print(f"FLAT_PVE_FIELDS={len(flat)}")
            print("VALUES_PRINTED=NO")
            return 0
        mapping = parse_mapping(args.map, sites)
        passports = partition(sites, shared, flat, mapping,
                              duplicate_shared=args.duplicate_shared,
                              flat_owner=args.flat_owner)
        manifest = stage(args.output_dir, vault, secret, original, passports)
        print("ENCRYPTED_CANDIDATES=PASS")
        print(f"SITE_PASSPORT_COUNT={len(manifest['sites'])}")
        print("ROUNDTRIP=PASS")
        print("VALUES_PRINTED=NO")
        print("SOURCE_VAULT_UNCHANGED=YES")
        print("GIT_COMMIT=NONE")
        print("PVE_AND_GUEST_WRITES=NONE")
        return 0
    finally:
        del password


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Blocked as error:
        print("MIGRATION_BLOCKED=" + str(error), file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        print("MIGRATION_FAILED=" + type(error).__name__, file=sys.stderr)
        sys.exit(2)
