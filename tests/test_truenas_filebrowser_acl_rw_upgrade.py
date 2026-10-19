"""Negative/positive tests for exactly-scoped non-recursive FileBrowser ACL upgrade."""
import copy
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = [
    ("USER_OBJ", None, "rwx"),
    ("GROUP_OBJ", None, "--x"),
    ("USER", "sampleuser", "r-x"),
    ("MASK", None, "r-x"),
    ("OTHER", None, "---"),
]


def module(file):
    path = ROOT / "plugins/modules" / file
    spec = importlib.util.spec_from_file_location("test_" + file[:-3], path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def state(data=True):
    return {
        "acltype": "POSIX1E",
        "uid": 0,
        "user": "root",
        "group": "data_group" if data else "backup_group",
        "trivial": False,
        "acl": [
            {"tag": tag, "who": who, "default": False,
             "perms": {"READ": mode[0] == "r", "WRITE": mode[1] == "w", "EXECUTE": mode[2] == "x"}}
            for tag, who, mode in OLD
        ],
    }


def target(data=True):
    return {
        "owner": "root",
        "group": "data_group" if data else "backup_group",
        "entries": [
            {"tag": tag, **({"who": who} if who else {}),
             "perms": ("rwx" if tag in ("USER", "MASK") else mode),
             "default": False}
            for tag, who, mode in OLD
        ],
    }


def test_existing_ro_to_rw_allowed_only_for_two_expected_roots():
    for file in ("truenas_access_plan.py", "truenas_access_reconcile.py"):
        m = module(file)
        for path, d in (("data", True), ("backups", False)):
            assert m.safe_filebrowser_rw_upgrade(
                state(d), target(d), "/mnt/sample_pool/" + path, "sample_pool"
            )
        assert not m.safe_filebrowser_rw_upgrade(state(), target(), "/mnt/sample_pool/docs", "sample_pool")
        assert not m.safe_filebrowser_rw_upgrade(state(), target(), "/mnt/sample_pool/data/download", "sample_pool")
        assert not m.safe_filebrowser_rw_upgrade(state(), target(), "/mnt/another_pool/data", "sample_pool")


def test_changed_owner_group_other_or_extra_ace_is_rejected():
    for file in ("truenas_access_plan.py", "truenas_access_reconcile.py"):
        checker = module(file).safe_filebrowser_rw_upgrade
        for mutation in ("other", "owner", "group", "named", "default", "mask"):
            v = state()
            t = target()
            if mutation == "other":
                v["acl"][-1]["perms"]["READ"] = True
            elif mutation == "owner":
                v["uid"] = 3200
            elif mutation == "group":
                v["group"] = "docs_group"
            elif mutation == "named":
                v["acl"][2]["who"] = "other"
            elif mutation == "default":
                v["acl"][2]["default"] = True
            elif mutation == "mask":
                t["entries"][3]["perms"] = "r-x"
            assert not checker(v, t, "/mnt/sample_pool/data", "sample_pool"), mutation


def test_existing_fresh_only_acl_guard_still_present():
    for file in ("truenas_access_plan.py", "truenas_access_reconcile.py"):
        src = (ROOT / "plugins/modules" / file).read_text()
        assert "safe_filebrowser_rw_upgrade(" in src
        assert "safe_initial_acl(observed)" in src
        assert "recursive" in src


def test_acl_upgrade_accepts_only_private_declared_user_and_group():
    for filename in ("truenas_access_plan.py", "truenas_access_reconcile.py"):
        check = module(filename).safe_filebrowser_rw_upgrade
        before = state()
        after = target()
        before["acl"][2]["who"] = "anotheruser"
        before["group"] = "new_data_group"
        after["entries"][2]["who"] = "anotheruser"
        after["group"] = "new_data_group"
        assert check(before, after, "/mnt/other_pool/data", "other_pool")
        invalid = copy.deepcopy(after)
        invalid["entries"][2]["who"] = "differentuser"
        assert not check(before, invalid, "/mnt/other_pool/data", "other_pool")
        invalid = copy.deepcopy(after)
        invalid["entries"].append({
            "tag": "USER", "who": "anotheruser2", "perms": "rwx", "default": False
        })
        assert not check(before, invalid, "/mnt/other_pool/data", "other_pool")
        assert not check(before, after, "/mnt/other_pool/docs", "other_pool")
        assert not check(before, after, "/mnt/other_pool/data/subdir", "other_pool")
