# Media VM: PCI preflight and commissioning sequence

This is an operator-visible, read-only entrypoint for the canonical
`serzhan-mediaserver` guest. It is **not** an installation or cutover.

1. Run Semaphore task `80-Медиасервер · Аудит PCI/VM`.
2. Confirm the target VMID/IP and pinned Debian 13 cloud image checksum in
   private Fleet inventory `media_vm.yml`. Verify static IP/DHCP reservation.
3. Existing VM 501 (Plex) currently owns Intel GPU 0000:00:02.0 and is running.
   VM 700 (TrueNAS) owns SATA 0000:00:17.0 and is stopped, but onboot=1.
   **Do not silently shut down either guest or remove any PCI attachment.**
4. The enabled `dh-backup-critical-daily` job targets currently offline
   `truenas_backup`. Verify an independent backup/recovery route before
   migrating disks or workloads.
5. Commissioning phase must create the new VM **stopped**, with no PCI use
   until an exact cutover has been explicitly approved. Install/verify the
   guest OS first, then transfer ownership and start it.
6. SATA disks contain existing data of unknown layout/health; never
   initialize, format, mount read-write, import or change ownership as a
   provisioning side effect. Require a separate data adoption plan.
7. GPU passthrough requires an actual E2E decode/transcode acceptance, not
   merely a successful QEMU boot or `lspci` output.
8. Both device handoffs must be planned against the current live VM configs,
   protected by exact VMID/PCI identities, independently verified after
   changes, and tested for idempotence.

## Engineering boundary

Implementation is generic to any site. Site-specific BDFs, VMID, image
SHA256, network and approvals belong only in private Fleet inventory.
Existing running guests, PCI owners, backups and SATA contents are protected.
The read-only plan never performs guest lifecycle writes.
