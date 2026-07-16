# Home Manager adoption contract

TIN-2791's repo-local artifact is `packaging/home-manager.json`. It is a
deterministic, drift-gated consumption contract, not an active Home Manager
module and not a fleet-health claim. `packaging/manifest.json` therefore marks
the lane `contract_ready = true` and `enabled = false` until the separate lab
module consumes it.

## Managed unit

Each provider service is loopback-only, shadow-only, and starts with both
`--require-ptoon` and `--require-split-auth`. The unit must supply four distinct
owner-only secret paths across the two providers: one local client token and
one upstream provider token per service. Files must be regular, owned by the
service user, mode `0600`, and not symlinks. Values never enter Nix, the
contract, process arguments, logs, or generated profiles.

Each service also pins `CHPL_RT_NUM_THREADS_PER_LOCALE=2`, one Qthreads
shepherd, and two Qthreads workers. The Python gateway admits 64 normal streams
but shares one 128 MiB in-flight request-body budget across them; this prevents
the per-request 16 MiB ceiling from multiplying into unbounded process memory.

The local token is not the provider credential, but it is still a sensitive
billed-relay capability. Claude and Codex use it as a bearer credential for
normal requests. Loopback and same-user process isolation remain required.

## Activation

Activation is disabled by default and ordered:

1. Start the service with the reviewed upstream, store-pinned `ptoon`, policy,
   and both credential files.
2. Run `prompt-toon doctor` with the matching client-token file. Require a
   valid HMAC challenge response plus matching service version, provider,
   actual listener endpoint, upstream URL, policy SHA-256, resident-binary
   SHA-256, and readiness fields. The MAC covers the complete canonical
   attestation; the token is not sent to the listener during this proof.
3. Only then expose the Claude environment or select the otherwise inert Codex
   profile. Profile presence is configuration evidence, not proof that a
   client used it.
4. Record post-activation request counters and shadow decisions before calling
   the request path active. Provider reachability requires an authenticated
   request; local readiness alone does not prove it.

`doctor` is local-observation-only and makes no provider request. Codex profile
selection by another process is inherently unobservable, so it reports that
state as `unknown`.

Claude activation is valid only when the local API key matches its owner-only
client-token file, no competing Claude auth/provider variable is set, and both
`NO_PROXY` and `no_proxy` bypass `127.0.0.1`, `localhost`, and `::1`. The
contract lists the exact rejected variables and the rollback fields to restore.
Codex has the same proxy-bypass requirement whenever a proxy variable is
present; a profile with a competing base URL or an incomplete loopback bypass
is an activation conflict.

## Host ledger

After each attended activation, record the contract's required fields: host,
observation time, service health and authenticated ownership, active binary,
request-path evidence, bounded shadow counters/quality, rollback, and policy
digest. A source contract contains no synthetic host observations.

Rollback removes the Claude local route/token or omits the Codex profile and
local token before restoring direct-provider credential custody. Enforcement
and every IO surface remain locked in `policy/io.json` throughout shadow
rollout.

## Platform delivery

The tagged flake is the canonical online package for `x86_64-linux` and
`aarch64-darwin`. The attended release lane realizes and smoke-tests both
platform closures on native remote builders; `--max-jobs 0` forbids local
Darwin compilation. Pull requests that change the native surface run the
path-scoped `Darwin Definition` workflow on the GF-managed Linux control plane.
That lane instantiates `packages.aarch64-darwin.ptoon.drvPath` only: it proves
the flake still defines the target, not that a Darwin artifact compiled or ran.

Durable native CI artifact proof remains blocked on TIN-2542's live
`gloriousflywheel-rbe-darwin-aarch64` executor and signing custody. The remote
Nix realization in `just release` is an attended interim release gate, not the
durable CI substrate. Linux retains exhaustive byte parity and the 64-stream
capacity gate. A release's Darwin derivation additionally runs native `caps`,
normalization, and resident-service round-trip checks.

The implementation is reviewed against current Chapel 2.9 documentation. The
locked source revision is nevertheless older and internally inconsistent: its
Nix package metadata says 2.7.0, while remote `chpl --version` reports 2.8.0
pre-release. The exact lock revision and both observed identities are artifact
provenance; this source does not claim 2.7.0- or 2.9-built binaries.

GitHub release artifacts are complete `nix-store --export` closure archives,
not raw executables. Import one with `nix-store --import`; its platform target
and archive digest are recorded in manifest schema v2, together with the
SHA-256 of its `bin/ptoon` entrypoint. Raw executables are linked to
their Nix-store runtime closure and are not portable standalone assets.
