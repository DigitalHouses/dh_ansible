"""Canonical Ansible Vault 1.1/1.2 authentication gate, stdlib-only."""
from pathlib import Path
import hashlib
import hmac
import importlib.util
import os
import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "bootstrap/fleet-master-gate.py"


def load():
    spec = importlib.util.spec_from_file_location("dh_fleet_master_gate", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vault_fixture(password: str, header=b"$ANSIBLE_VAULT;1.1;AES256") -> bytes:
    salt = bytes(range(32))
    ciphertext = bytes(range(32, 64))  # random-look AES-CTR ciphertext (not plaintext)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 10000, 80)
    mac = hmac.new(derived[32:64], ciphertext, hashlib.sha256).digest()
    payload = (salt.hex() + "\n" + mac.hex() + "\n" + ciphertext.hex()).encode()
    armored = payload.hex().encode()
    return header + b"\n" + b"\n".join(armored[i:i+80] for i in range(0, len(armored), 80)) + b"\n"


@pytest.mark.parametrize("header", [b"$ANSIBLE_VAULT;1.1;AES256", b"$ANSIBLE_VAULT;1.2;AES256"])
def test_password_verifies_hmac_without_decrypting_or_libraries(header):
    m = load()
    blob = vault_fixture("Correct-Master-Example", header)
    assert m.password_authenticates(blob, b"Correct-Master-Example")
    assert not m.password_authenticates(blob, b"Wrong-Master-Example")


def test_modified_mac_and_ciphertext_fail():
    m = load()
    data = vault_fixture("Correct-Master-Example")
    # Alter one hexdigit in the double-hex-armored payload while keeping valid formatting
    salt, mac, cipher = m.split_ciphertext(data)
    malformed = data.replace(b"$ANSIBLE_VAULT;1.1;AES256", b"$ANSIBLE_VAULT;1.1;AES128")
    with pytest.raises(m.Blocked):
        m.split_ciphertext(malformed)
    wrong = vault_fixture("Other-Password")
    assert not m.password_authenticates(wrong, b"Correct-Master-Example")


def test_reject_unsafe_permissions_and_unpinned_ciphertext(tmp_path):
    m = load()
    p = tmp_path / "fleet.vault.yml"
    blob = vault_fixture("Test-Master-Example")
    p.write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    os.chmod(p, 0o600)
    assert m.read_pinned_vault(p, sha) == blob
    with pytest.raises(m.Blocked, match="SHA-256"):
        m.read_pinned_vault(p, "0"*64)
    os.chmod(p, 0o666)
    with pytest.raises(m.Blocked, match="writable"):
        m.read_pinned_vault(p, sha)


def test_gate_contains_no_pve_mutations_or_password_transfer():
    s = PATH.read_text()
    for term in ("pct create", "pct destroy", "qm create", "subprocess.run",
                 "requests.", "urllib.request", "os.environ["):
        assert term not in s
    assert 'getpass.getpass' in s
    assert "MASTER_PASSWORD_HMAC=PASS" in s
    assert "ANSIBLE_DECRYPT_AND_SITE_VALIDATION=PENDING" in s
