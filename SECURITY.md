# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub:
**Security → Report a vulnerability** on the repository page
(<https://github.com/ZdenekOndra/home-island/security/advisories/new>).

Include what is affected, how to reproduce it, and the impact you expect.
You should receive an answer within 14 days. Fixes are released as soon as
practical, and reporters are credited in the changelog unless they prefer not
to be.

## Supported versions

Only the latest release and the `main` branch receive security fixes.

## Scope

HomeIsland's own code (installer, CLI, collector, dashboard, library app,
compose configuration) is in scope. Vulnerabilities in bundled upstream
software (Pi-hole, Caddy, Kiwix, Home Assistant, Jellyfin, Samba, MapLibre)
should be reported to those projects; please let us know as well if
HomeIsland's configuration makes them worse or needs a version bump.

The threat model and hardening guidance are in
[docs/SECURITY.md](docs/SECURITY.md).
