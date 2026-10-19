#!/usr/bin/env python3
"""Show a site PKCS#12 import password on the operator's own TTY.

No Cloudflare, TrueNAS, Git, file, clipboard, logging, or password-rotation writes.
Secret never reaches stdout or stderr; hidden after the operator presses Enter.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

class Block(Exception):
    pass


def verifier_module():
    spec = importlib.util.spec_from_file_location(
        "mtls_p12_password_recovery", ROOT / "bin/ha-mtls-vault-verify.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recovery_policy(site):
    spec = importlib.util.spec_from_file_location(
        "mtls_site_policy", ROOT / "bin/ha-mtls-certificate-plan.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.accepted_issued_certificate(site)
    except module.Block as exc:
        raise Block(str(exc)) from None


def check_material(verifier, ciphertext, master, site, recovery):
    host, key, csr, generation, entry = verifier.recovery_identity(
        ciphertext, master, site, "issued_unpublished"
    )
    if host != recovery["hostname"] or generation != recovery["generation_id"]:
        raise Block("P12_IMPORT_IDENTITY_DRIFT")
    cert_id = verifier.verify_issued_identity(
        host, key, csr, entry, recovery["certificate_sha256"]
    )
    if cert_id != recovery["certificate_id"]:
        raise Block("P12_IMPORT_CERTIFICATE_ID_CHANGED")
    password = entry.get("p12_password")
    if not isinstance(password, str) or len(password) < 32 or any(
        ch in password for ch in ("\r", "\n", "\x00")
    ):
        raise Block("P12_IMPORT_PASSWORD_INVALID")
    return password


def show_once_on_terminal(password, p12_filename):
    """Show only on a verified interactive terminal, never a captured stream.

    pct exec/runuser can preserve interactive stdin and stdout yet deny
    reading /dev/tty. Use a write-only terminal descriptor, and read the
    dismissal keystroke from already-verified interactive stdin.
    """
    if not os.isatty(0):
        raise Block("INTERACTIVE_TERMINAL_REQUIRED")
    destination = None
    owns_fd = False
    try:
        try:
            destination = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
            owns_fd = True
        except OSError:
            if not os.isatty(1):
                raise Block("LOCAL_TTY_OUTPUT_UNAVAILABLE")
            # Verified real TTY, not a redirected stdout/file/remote log.
            destination = 1
        message = (
            "\nIMPORT PASSWORD FOR " + p12_filename +
            "\nPassword: " + password +
            "\nEnter it on your phone. Press Enter here to clear the screen.\n"
        )
        displayed = False
        try:
            payload = message.encode("utf-8")
            count = 0
            while count < len(payload):
                count += os.write(destination, payload[count:])
            displayed = True
            # Interactive stdin works through pct exec even if read(/dev/tty)
            # fails with EIO because the child has no controlling terminal.
            response = sys.stdin.readline()
            if response == "":
                raise Block("TERMINAL_CONFIRMATION_UNAVAILABLE")
        finally:
            if displayed:
                # Screen + scrollback clear even if the input step fails.
                os.write(destination, b"\x1b[2J\x1b[3J\x1b[H")
    except (OSError, UnicodeError):
        raise Block("SECURE_TERMINAL_UNAVAILABLE") from None
    finally:
        if owns_fd and destination is not None:
            os.close(destination)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--display-once", action="store_true", required=True)
    args = parser.parse_args()
    verifier = verifier_module()
    verifier.safe_runner(args.site)
    if not os.isatty(0):
        raise Block("INTERACTIVE_TERMINAL_REQUIRED")
    recovery = recovery_policy(args.site)
    with tempfile.TemporaryDirectory(prefix="dh-mtls-password-", dir=verifier.BASE) as work:
        repository, _ = verifier.clone_from_github(Path(work))
        ciphertext = verifier.encrypted_vault_from_clone(
            repository, recovery["vault_sha256"]
        )
        master = verifier.master_from_tty()
        password = check_material(verifier, ciphertext, master, args.site, recovery)
    print("P12_CERTIFICATE_RECOVERY=PASS")
    print("P12_FILE=digitalhouses/_cert/" + recovery["p12_filename"])
    print("P12_PASSWORD_DESTINATION=LOCAL_TTY_ONLY")
    show_once_on_terminal(password, recovery["p12_filename"])
    print("P12_PASSWORD_HIDDEN_AFTER_KEYPRESS=YES")
    print("P12_PASSWORD_LOGGED_OR_SAVED=NO")
    print("GITHUB_CLOUDFLARE_TRUENAS_MUTATIONS=NONE")


if __name__ == "__main__":
    try:
        main()
    except Block as error:
        print("BLOCK=" + str(error), file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        print("BLOCK=UNEXPECTED_" + type(error).__name__, file=sys.stderr)
        sys.exit(2)
    finally:
        print("=== DONE ===")
        print("=== TERMINAL STAYS OPEN ===")
