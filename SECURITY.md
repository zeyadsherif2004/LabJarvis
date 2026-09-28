# Security policy

## Supported version

Security fixes are applied to the current `main` branch. This student project does not promise long-term support for old commits or deployed instances.

## Reporting a vulnerability

Do not open a public issue containing exploit details, laboratory data, participant data, credentials, or private model inputs. The repository is public at <https://github.com/zeyadsherif2004/LabJarvis>; use GitHub's private vulnerability reporting if it is enabled for the repository, otherwise contact the repository owner privately through the university-approved contact method. Include the affected commit, configuration, impact, minimal reproduction, and suggested mitigation if known.

The maintainer should acknowledge a report, reproduce it without real sensitive data, agree on disclosure timing, ship a tested fix, and credit the reporter if requested. No bounty is offered.

## Security boundary

The default service binds to loopback and has no authentication or multi-user authorization. **Do not expose it to a LAN or the internet.** Any non-loopback deployment needs an authenticated reverse proxy, TLS, per-user authorization, request-rate controls, hardened secret management, backups, monitoring, and a fresh threat review.

### Cross-origin state changes

CORS decides what a web page may *read*. It does not stop the request being
made: a `multipart/form-data` POST carrying no custom header is a "simple"
request that browsers send without a preflight. A bounded scan confirmed the
consequence here — a request with `Origin: https://attacker.example` reached the
protocol upload endpoint and was answered with `201 Created`. The hostile page
could not read the reply, but a protocol record existed afterwards.

Every state-changing method (`POST`, `PUT`, `PATCH`, `DELETE`) is now checked
against the configured origin allowlist before routing, so a refused request
never reaches a handler and writes nothing.

Requests that carry **no** `Origin` header are still accepted. Browsers attach
`Origin` to every cross-origin request and to every same-origin request that is
not a `GET` or `HEAD`, so the browser-reachable attack always carries it and is
always caught; `curl`, the repository's own scripts, and integration tests do
not send one and keep working. This trade is sound only because the supported
deployment is loopback-only and single-operator. **Exposing the API to a network
would require authentication and a real CSRF mechanism, which remain out of
scope.**

### Hosted providers and API keys

Hosted model keys are read from `.env` or the environment only (never from
the browser, the database, `settings.json`, logs, readiness or Settings
responses, or exports). The Settings page reports only whether each key is
configured and where it comes from.

### Settings and restart

Saving configuration and restarting LabJarvis are accepted only from the
loopback address, behind the same Origin guard. The restart request carries a
single consent flag; the API only leaves a token-bearing marker for the
launcher that started it (`tasks.py dev`), which restarts its own fixed
command. No path, command or argument from a request is ever executed. The
local Kokoro voice endpoint accepts only bounded text (and a validated voice
name) and stores nothing. `scripts/scan_secrets.py`
runs in CI. Any paid evaluation tooling authorises each request against the
cumulative spend ledger before sending it.

Model output and reagent matches are untrusted proposals. LabJarvis is not a safety system, autonomous controller, or certification tool. See [the threat model](docs/security/threat-model.md) for controls and residual risks.

## Sensitive data

Never attach real protocols, raw recordings, images, notebook exports, `.env` files, database files, or model caches to a report. Use synthetic reproductions. If research data may have been exposed, follow university incident and ethics procedures in addition to this policy.

