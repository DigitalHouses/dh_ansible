# Canonical new guest identity: PVE ID, IPv4 and hostname

**Scope:** newly commissioned infrastructure guests of DigitalHouses Ansible-2.
Site-specific network ranges, addresses and service names live only in private
Fleet Detail inventory; existing legacy guests are **not** automatically renamed.

## Canonical identity contract

* Hostname: `<service>-<site_id>` with lowercase English labels, e.g.
  `media-server-serzhan-home`, `netalertx-serzhan-home` or `truenas-amir`.
* Standard infrastructure host addresses in each site's /24 use last octet
  `.200` through `.254`. Never infer that an IP is available merely because it
  does not respond to a probe. Verify DHCP exclusion/reservation and conflicts.
* **VM (QEMU):** `VMID = 2000 + IPv4_last_octet`.
* **LXC (CT):** `CTID = 1000 + IPv4_last_octet`.
* Examples: VM2232 / 192.168.8.232 (`truenas-amir`);
  VM2238 / 192.168.8.238 (`media-server-amir`);
  CT1239 / 192.168.11.239 (`netalertx-serzhan-home`).
* For the proposed home media VM, the candidate is
  **VM2238 / 192.168.11.238 / media-server-serzhan-home**.
  The address remains unapproved until live reservation/conflict verification.

## Safety and exceptions

* Verify that both PVE guest ID and LAN address are unoccupied **before apply**.
* Preserve PVE guest identities, PCI assignments, machine IDs, DHCP leases and
  data of existing unrelated guests. Never rename or re-number them implicitly.
* Exceptions for existing systems (e.g. legacy VM501, VM700, CT333 or adopted
  historical pilot guests) require an explicit inventory annotation, not a
  rewrite of the canonical rule.
* Plan and apply must reject new guests whose name, address and ID disagree.
* Network acceptance additionally requires the mDNS/Bonjour baseline from
  `docs/standards/guest-network-mdns.md`.
