# DigitalHouses runner identity standard

A home's topology differs through Fleet Detail inventory, not executable code.

- Standard new runner guest: LXC **CT1231** on the site's PVE.
- Standard /24 address rule: runner host address suffix **.231**, outside site DHCP range.
- Example network (illustrative only): `198.51.100.231/24`; not a real site.
- Runner hostname: `ansible-<site_id>`.
- Default code checkout: `/opt/digitalhouses/dh_ansible`.
- Private site data: explicitly authenticated separate checkout, normally `/var/lib/ansimble/fleet-detail`.
- Local Semaphore UI, Ansible venv, pinned repositories, and read-only Git access.
- Site inventory is the sole source of actual host addresses, DNS, TLS fingerprints and owner-specific configuration.

Commissioning must fail closed on conflicts, existing guests with unknown state, systemd/D-Bus errors, failed credential verification, and nonmatching immutable Git refs.
