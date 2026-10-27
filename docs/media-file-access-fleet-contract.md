# DigitalHouses Media Server — authenticated file access contract

This stage is **staged in a feature branch** and intentionally fails closed when private Fleet credentials have not been commissioned. Do not merge/deploy it until site inventory is populated. No live VM or ZFS data must be changed during code review.

## Private Fleet site data (not in this public repository)

Add to `media_vm` in `serzhan-home/media_vm.yml` **only in `dh_ansible_fleet_detail`**:

```yaml
file_access:
  accounts:
    - name: example_reader
      role: reader
      password: "<REPLACE_WITH_UNIQUE_16_PLUS_CHAR_SECRET_IN_PRIVATE_FLEET>"
    - name: example_writer
      role: writer
      password: "<REPLACE_WITH_DIFFERENT_UNIQUE_16_PLUS_CHAR_SECRET_IN_PRIVATE_FLEET>"
```

These are placeholders, not real account names or passwords. Replace before deployment. Usernames must be unique, 3–31 characters, lowercase Latin, digits, underscores or hyphens, starting with a letter. Reserved system names are rejected. Passwords require at least 16 characters. The private Fleet file remains the **only** declarative password source; no sensitive material is committed to `dh_ansible`.

## Access design

- Samba offers a single Media share at the existing ZFS media dataset.
- No guest access; no forced `nobody` identity. Every SMB login is a non-login POSIX account, with Samba password provisioned from its Fleet record.
- `reader` can list/download; `writer` can also upload/create/delete under normal POSIX directory permissions.
- Samba authorization groups: `dh_media_readers` and `dh_media_writers`. The `media_group` POSIX group is shared with Plex/Arr.
- The Media dataset root gains `root:media_group 2775` without recursion. The files/directories and other ZFS datasets are left untouched.
- Reapplying the role reconciles the Samba password from Fleet (including rotations); plaintext values are suppressed in Ansible output with `no_log`.
- Preflight fails with `MEDIA_FILE_ACCESS_FLEET_REQUIRED` before the PVE deploy worker is invoked if the private access contract is absent.

## Remaining acceptance requirements

Filestash must be configured with SMB backend + passthrough authentication (same Samba credentials, no duplicate client password database) and independently tested for upload/download files, folders, and large objects. Do not write guesswork into Filestash `config.json`. Initialize Jellyfin wizard/admin credentials from approved private Fleet parameters. Test Sambee separately. Indexers are user-selected; none are provisioned implicitly. Perform a Windows/macOS SMB and browser-based E2E test, including authenticated reader denial of writes and persistence across restart.

**Important:** A successful Semaphore run is not sufficient evidence of functional acceptance. Continue using `keep + import` until approved destructive E2E.
