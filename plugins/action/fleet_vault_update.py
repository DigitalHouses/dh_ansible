# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import yaml

from ansible.parsing.vault import VaultLib, VaultSecret
from ansible.plugins.action import ActionBase


FLAT_PVE_KEYS = {
    "vault_pve_audit_token_secret": "audit_token_secret",
    "vault_pve_write_token_secret": "write_token_secret",
    "vault_pve_host_write_ssh_private_key": "host_write_ssh_private_key",
}


def to_plain_data(value):
    if isinstance(value, dict):
        return {
            str(to_plain_data(key)): to_plain_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [to_plain_data(item) for item in value]
    if isinstance(value, str):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def deep_merge(dst, src):
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value


class ActionModule(ActionBase):
    TRANSFERS_FILES = False

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        task_vars = task_vars or {}

        try:
            args = self._task.args.copy()

            path = Path(self._templar.template(args["path"]))
            pass_path = Path(
                self._templar.template(args["vault_password_file"])
            )
            site_id = str(self._templar.template(args["site_id"]))

            site_update = to_plain_data(
                self._templar.template(
                    args.get("site_update", {}),
                    fail_on_undefined=True,
                )
            )
            shared_update = to_plain_data(
                self._templar.template(
                    args.get("shared_update", {}),
                    fail_on_undefined=True,
                )
            )
            migrate_flat_pve = bool(
                self._templar.template(
                    args.get("migrate_flat_pve", True)
                )
            )

            if not isinstance(site_update, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": "site_update must be a mapping.",
                }
            if not isinstance(shared_update, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": "shared_update must be a mapping.",
                }

            password = pass_path.read_text(
                encoding="utf-8"
            ).rstrip("\r\n")

            if not password:
                return {
                    **result,
                    "failed": True,
                    "msg": "Vault password file is empty.",
                }

            secret = VaultSecret(password.encode("utf-8"))
            vault = VaultLib([("default", secret)])

            ciphertext = path.read_bytes()
            plaintext = vault.decrypt(ciphertext)
            data = yaml.safe_load(
                plaintext.decode("utf-8")
            ) or {}

            if not isinstance(data, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": "Fleet vault plaintext must be a YAML mapping.",
                }

            original_data = copy.deepcopy(data)

            fleet = data.setdefault("fleet_secrets", {})
            if not isinstance(fleet, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": "fleet_secrets must be a mapping.",
                }

            schema_version = fleet.setdefault("schema_version", 1)
            if schema_version != 1:
                return {
                    **result,
                    "failed": True,
                    "msg": (
                        "Unsupported fleet vault schema_version="
                        f"{schema_version!r}."
                    ),
                }

            shared = fleet.setdefault("shared", {})
            if not isinstance(shared, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": "fleet_secrets.shared must be a mapping.",
                }
            deep_merge(shared, shared_update)

            sites = fleet.setdefault("sites", {})
            if not isinstance(sites, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": "fleet_secrets.sites must be a mapping.",
                }

            site = sites.setdefault(site_id, {})
            if not isinstance(site, dict):
                return {
                    **result,
                    "failed": True,
                    "msg": (
                        f"fleet_secrets.sites.{site_id} "
                        "must be a mapping."
                    ),
                }

            migrated = False

            if migrate_flat_pve:
                pve = site.setdefault("pve", {})
                if not isinstance(pve, dict):
                    return {
                        **result,
                        "failed": True,
                        "msg": (
                            f"fleet_secrets.sites.{site_id}.pve "
                            "must be a mapping."
                        ),
                    }

                for old_key, new_key in FLAT_PVE_KEYS.items():
                    if old_key not in data:
                        continue

                    if (
                        new_key in pve
                        and pve[new_key] != data[old_key]
                    ):
                        return {
                            **result,
                            "failed": True,
                            "msg": (
                                "Refusing conflicting migration for "
                                f"{old_key}: nested PVE secret "
                                "already differs."
                            ),
                        }

                    pve[new_key] = data.pop(old_key)
                    migrated = True

            deep_merge(site, site_update)

            rendered = yaml.safe_dump(
                data,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )

            changed = data != original_data

            result.update(
                changed=changed,
                schema_version=1,
                site_id=site_id,
                migrated_flat_pve=migrated,
                updated_sections=sorted(str(key) for key in site_update.keys()),
                updated_shared_sections=sorted(
                    str(key) for key in shared_update.keys()
                ),
            )

            if self._task.check_mode or not changed:
                return result

            encrypted = vault.encrypt(
                rendered.encode("utf-8")
            )

            mode = path.stat().st_mode & 0o777
            fd, temp_name = tempfile.mkstemp(
                prefix=".fleet-vault-",
                dir=str(path.parent),
            )

            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(encrypted)
                    fh.flush()
                    os.fsync(fh.fileno())

                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

            return result

        except KeyError as exc:
            result.update(
                failed=True,
                msg=f"Missing required fleet_vault_update argument: {exc.args[0]}",
            )
            return result
        except Exception as exc:
            result.update(
                failed=True,
                msg=(
                    "Fleet Vault controller-side update failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            return result
