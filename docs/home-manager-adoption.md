# Home Manager adoption contract

TIN-2791's repo-local artifact is `packaging/home-manager.json`. It is a
deterministic, drift-gated consumption contract, not an active Home Manager
module and not a fleet-health claim. The separate lab repository now contains
a disabled-by-default consumer of this contract, but no host has activated it.
`packaging/manifest.json` therefore continues to mark the lane
`contract_ready = true` and `enabled = false` until a released v0.3.0-or-newer
package is pinned and an attended host activation produces the required ledger
evidence.

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

TIN-2949 owns the remaining durable native artifact proof: an authorized
physical `gloriousflywheel-rbe-darwin-aarch64` worker and endpoint, forced
remote action evidence, a native Mach-O smoke matrix, and a reviewed bridge to
the closure-stamped release manifest. TIN-2542 is complete and is no longer the
blocking issue. The current remote Nix realization in `just release` remains
an attended interim gate, not a substitute for the durable GF proof. Linux
retains exhaustive byte parity and the 64-stream capacity gate. A release's
Darwin derivation additionally runs native `caps`, normalization, and
resident-service round-trip checks.

The v0.3 Darwin deliverable is a CLI distributed as a complete Nix closure, not
an `.app`, `.pkg`, or disk image. Apple Silicon still requires executable code
to be signed, so the native build gate verifies the final store executable's
valid ad-hoc signature and absence of a Team Identifier or signing authority.
That signature seals the code without a Developer ID identity. The remaining
release gates are the tagged flake, native execution, Mach-O architecture,
closure and entrypoint digests, an OpenPGP-signed Git tag, and a detached
OpenPGP signature over the stamped manifest. Developer ID identity signing,
notarization, and stapling are not v0.3 prerequisites. They become project
release requirements if a future target claims a Gatekeeper-facing signed,
notarized, or stapled application or installer.

This decision is distribution-specific, not a claim that software without a
Developer ID identity is universally exempt from macOS security controls.
Apple's
[Developer ID guidance](https://developer.apple.com/support/developer-id/) scopes that
program to software distributed outside the Mac App Store, especially apps,
plug-ins, and installer packages, while
[Apple Platform Security](https://support.apple.com/guide/security/gatekeeper-and-runtime-protection-sec5599b66df/web)
describes Gatekeeper's broader downloaded-software checks. Apple's
[Apple Silicon release notes](https://developer.apple.com/documentation/macos-release-notes/macos-big-sur-11_0_1-universal-apps-release-notes/)
state that Apple Silicon code requires a signature, that an ad-hoc identity is
sufficient for execution, and that current `clang`/`ld` apply it at link time.
Revisit the Developer ID/notarization gate if prompt-toon moves beyond an
operator-controlled Nix install path.

The implementation is reviewed against current Chapel 2.9 documentation. The
locked source revision is nevertheless older and internally inconsistent: its
Nix package metadata says 2.7.0, while remote `chpl --version` reports 2.8.0
pre-release. The exact lock revision and both observed identities are artifact
provenance; this source does not claim 2.7.0- or 2.9-built binaries.

GitHub release artifacts are complete `nix-store --export` closure archives,
not raw executables. The reviewed OpenPGP trust anchor is
`packaging/release-signers.json`, backed by the public key in
`packaging/release-signing-key.asc`; the release lane refuses any other secret
key. Verify the signed tag and detached manifest signature against that pinned
full fingerprint before trusting artifact digests. GitHub release notes repeat
the fingerprint for convenience but are not the trust root.
Import a closure with `nix-store --import`; its platform target and archive
digest are recorded in manifest schema v2, together with the SHA-256 of its
`bin/ptoon` entrypoint. Raw executables are linked to their Nix-store runtime
closure and are not portable standalone assets.
