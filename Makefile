# TIN-2708 C1: ptoon binary build lane — thin wrapper over the remote-build
# path (`just build-ptoon` is the same recipe; this exists for operators/
# tooling that reach for `make` first).
#
# ARCHITECTURE PIVOT (2026-07-09): the Chapel engine is a standalone `ptoon`
# binary (proc main, stdin/stdout subprocess protocol), NOT a ctypes-loaded
# shared library — repeated exported-proc calls in a --library build segfaulted
# on buffer free. So there is no libptoon/.so and no exported C ABI symbols to
# dump; the artifact is a single ELF executable.
#
# DOCTRINE (AGENTS.md, 2026-07-09): Chapel compilation is remote-only. NEVER run
# `chpl` directly on this (darwin) host, and there is intentionally no
# local/native chpl target below. Two remote execution planes are wired:
#   - `make build-ptoon`  -> nix build on the x86_64-linux remote builder
#                            (cache-first; today's default C1 substrate).
#   - `make bazel-ptoon`  -> the same compile driven by Bazel on GF REAPI
#                            (executor-backed; the C2/endgame substrate,
#                            gated on the operator arming BAZEL_REMOTE_EXECUTOR).
# Both produce the same x86_64-linux ELF; see flake.nix `ptoonBinary` and
# //src/ptoon:ptoon (//tools/bazel/chapel:defs.bzl).

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help build-ptoon bazel-ptoon parity clean

help:
	@echo "ptoon binary (TIN-2708 C1) — remote-only build lane. Targets:"
	@echo "  make build-ptoon   nix build the release (--fast) x86_64-linux ptoon ELF — the CI/parity target"
	@echo "  make bazel-ptoon   Bazel-drives-chpl compile on GF REAPI (executor-backed; needs armed executor)"
	@echo "  make parity        run the nix-remote byte-parity gate (chapel vs python oracle, all fixtures)"
	@echo "  make clean         remove the ./result symlink left by nix build"
	@echo ""
	@echo "Local chpl invocation is forbidden on this host. See AGENTS.md / docs/mythos-delivery-design.md §7-8."

# Release build of the ptoon binary, x86_64-linux, the required/CI target. The
# underlying derivation always compiles with `chpl --fast` (release mode).
build-ptoon:
	nix build .#packages.x86_64-linux.ptoon --print-build-logs

# The same compile, driven by Bazel on the GloriousFlywheel REAPI executor. This
# is the endgame execution plane (nix owns the chpl version, Bazel validates/
# caches/executes). Fails closed if the executor endpoint is not armed
# (--remote_local_fallback=false) — it never falls back to a local darwin build.
bazel-ptoon:
	just flywheel-chapel

# Byte-parity gate: builds the ptoon binary and diffs it against the Python
# oracle across the whole fixture corpus. This is the C1 correctness gate.
parity:
	nix build .#packages.x86_64-linux.ptoon-parity --print-build-logs

clean:
	rm -f result
