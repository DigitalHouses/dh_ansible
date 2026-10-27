# DigitalHouses Media Server: initial account contract

Status: staged, not deployed.

Private Fleet defines precisely two names and roles for serzhan-home:

```yaml
file_access:
  accounts:
    - {name: serzhan, role: owner}
    - {name: serzhan_admin, role: administrator}
```

The owner belongs to media_group and doc_group. The administrator also belongs to backup_group, download_group and admin_group. These are non-login POSIX accounts with corresponding SMB identities.

Initial credentials are randomly generated once, eight alphanumeric characters each, unique to each house, and persist in root-only records under the ZFS digitalhouses dataset. Subsequent deployments reuse them. They are not published through the service portal, Samba, or public GitHub. A privileged operator retrieves individual records for secure client handoff.

Shares: Media and Documents permit the owner and administrator. DigitalHouses and Backups permit the administrator only. Anonymous access is denied. Avoid exposing SMB on untrusted networks; eight-character secrets require additional network controls.

The user-management web UI is a later project. Filestash passthrough and macOS/Windows functional acceptance remain mandatory before production rollout.
