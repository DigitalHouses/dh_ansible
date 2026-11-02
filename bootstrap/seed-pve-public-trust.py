#!/usr/bin/env python3
"""Host-root Bootstrap trust seed, NOT PVE SSH commissioning.

Invoked automatically by bootstrap/install.sh while already executing on the
actual PVE host. Only public server identity is copied into site-local runner:
PVE CA, live leaf TLS SHA256, server SSH host public key and vmbr0/node.
No root SSH key authorization, password, token, or managed-host modification.
"""
import argparse
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner-ctid", type=int, required=True)
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    if not 100 <= args.runner_ctid <= 999999:
        raise ValueError("CTID_INVALID")
    # Reuse audited host-root, site and path checks from commissioning module,
    # but NEVER call its key-generation or authorization functions.
    old = runpy.run_path(
        str(Path(__file__).with_name("commission-pve-runner.py")),
        run_name="dh_pve_public_trust_library",
    )
    iface, node = old["observed_host"]()
    site, runner_ip = old["runner_identity"](args.runner_ctid, iface)
    if site != args.site:
        raise ValueError("PVE_TRUST_WRONG_SITE")
    fingerprint = old["read_live_certificate"](iface.ip)
    old["install_guest_trust"](args.runner_ctid, str(iface.ip), node, fingerprint)
    print("PVE_PUBLIC_TRUST_SEED=PASS")
    print("PVE_PUBLIC_TRUST_SITE=" + site)
    print("PVE_PUBLIC_TRUST_NODE=" + node)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print exceptions containing file contents or secret material.
        print("PVE_PUBLIC_TRUST_SEED=BLOCKED: " + type(error).__name__, file=sys.stderr)
        sys.exit(2)
