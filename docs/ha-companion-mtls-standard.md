# HA Companion · mTLS standard

mTLS protects the site's Home Assistant ingress; the exact hostname, tunnel and Cloudflare identifiers are private Fleet Detail data. Certificate issuance and renewal use universal code. A site-specific certificate, private key or password must never appear in public Git, public CI artifacts or logs.

Site credential packages are stored under the site's TrueNAS `digitalhouses/_cert` dataset with restrictive ownership and permissions, as specified in private inventory. Provision the Apple-compatible and modern PKCS#12 formats where required, with a separate password sidecar for authorized users. The certificate process remains explicit, idempotent and independent of arbitrary Cloudflare policy updates. Reissuance must create a new verified bundle without silently overwriting unrelated files.

**Status:** original pilot-specific publication helpers still require generalization before public release; never publish those helper scripts with embedded site constants.
