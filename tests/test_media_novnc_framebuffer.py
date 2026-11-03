"""Separate PVE noVNC (bochs) from Intel Quick Sync (i915) DRM on guest boots."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "roles/media_guest_setup/tasks/novnc_framebuffer.yml"


def test_novnc_preserves_intel_gpu_and_serial_recovery():
    s = TASK.read_text()
    assert "softdep bochs pre: i915" in s
    assert "/etc/modprobe.d/90-dh-bochs-after-i915.conf" in s
    assert "/sbin/modprobe" in s
    assert "--show-depends" in s
    assert "MEDIA_NOVNC_MODULE_ORDER_NOT_APPLIED" in s
    assert "modprobe -r" not in s
    assert "blacklist i915" not in s.split("content: |")[1]
    assert "nomodeset" not in s
    assert "update-initramfs" not in s
    main = (ROOT / "roles/media_guest_setup/tasks/main.yml").read_text()
    assert "import_tasks: novnc_framebuffer.yml" in main
    assert "import_tasks: serial_console.yml" in main
    assert main.index("import_tasks: novnc_framebuffer.yml") < main.index("import_tasks: serial_console.yml")


def test_boot_depends_order_and_guard_only_when_drivers_exist():
    parsed = yaml.safe_load(TASK.read_text())
    names = [t["name"] for t in parsed]
    assert any("availability" in name for name in names)
    assert any("modprobe simulation" in name for name in names)
    require = [t for t in parsed if "Require Intel driver precedes" in t["name"]][0]
    assert "dh_media_novnc_i915.rc == 0" in require["when"]
    assert "dh_media_novnc_bochs.rc == 0" in require["when"]
    assert len(require["ansible.builtin.assert"]["that"]) == 3
    boot = (ROOT / "bootstrap/semaphore-host-install.py").read_text()
    assert '"roles/media_guest_setup/tasks/novnc_framebuffer.yml"' in boot
    assert '("media_guest_setup", "tasks/novnc_framebuffer.yml")' in boot


def test_native_vga_login_is_not_overwritten_by_routine_kernel_messages():
    tasks = yaml.safe_load(TASK.read_text())
    assert "Keep native PVE VGA login readable after background services start" in [t["name"] for t in tasks]
    cfg = next(t for t in tasks if t["name"].startswith("Keep native PVE VGA login"))
    assert cfg["ansible.builtin.copy"]["dest"] == "/etc/sysctl.d/90-dh-media-vga-console.conf"
    assert cfg["ansible.builtin.copy"]["content"] == "kernel.printk = 4 4 1 7\n"
    assert "MEDIA_NOVNC_CONSOLE_LOGLEVEL_NOT_APPLIED" in TASK.read_text()
    assert "journalctl" not in TASK.read_text()
    assert "systemctl restart getty" not in TASK.read_text()
    assert "kernel.printk=4 4 1 7" in TASK.read_text()
