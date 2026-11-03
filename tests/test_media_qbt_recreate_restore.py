from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_qbt_preserved_stamp_can_rehydrate_factory_default():
    script = (ROOT / "roles/media_guest_setup/files/dh-qbt-admin.py").read_text()
    assert 'restore_default = (not first and prefs.get("web_ui_username") == "admin")' in script
    assert "if first or restore_default:" in script
    assert "QBT_ADMIN=RESTORED" in script
    assert "QBT_PASSWORD_ROTATION_NOT_AUTHORIZED" in script
    assert "QBT_MANAGED_LAN_AUTH_BYPASS_DRIFT" in script
    role = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "'QBT_ADMIN=RESTORED' in media_qbt_admin.stdout" in role
