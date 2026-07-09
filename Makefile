# TIN-2708 C1: libptoon build lane — thin wrapper over the nix remote-build
# path (`just build-lib` is the same recipe; this exists for operators/
# tooling that reach for `make` first).
#
# DOCTRINE (AGENTS.md, 2026-07-09): Chapel compilation is remote-only.
# NEVER run `chpl` directly on this (darwin) host, and there is
# intentionally no local/native chpl target below. Every target shells
# out to `nix build`, which offloads the real `chpl --fast --library
# --dynamic` compile of src/ptoon/Ptoon.chpl to the x86_64-linux remote
# builder (nix cache-first). See flake.nix's `libptoon` derivation for
# the actual build/check/install phases.

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help build-lib build-lib-darwin symbols clean

help:
	@echo "libptoon (TIN-2708 C1 skeleton) — remote-only build lane. Targets:"
	@echo "  make build-lib          nix build the release (--fast) x86_64-linux .so — the CI/parity target"
	@echo "  make build-lib-darwin   nix build the darwin variant — NOT required green (pzm builder in burn-in)"
	@echo "  make symbols            print the six ptoon_* C ABI symbols from the last build-lib result"
	@echo "  make clean              remove the ./result symlink left by nix build"
	@echo ""
	@echo "Local chpl invocation is forbidden on this host. See AGENTS.md / docs/mythos-delivery-design.md §7."

# Release build of the shared library, x86_64-linux, the required/CI
# target. The underlying derivation always compiles with `chpl --fast`
# (release mode) — there is no separate non-fast target here because
# nothing in this repo consumes an unoptimized libptoon build yet.
build-lib:
	nix build .#packages.x86_64-linux.libptoon --print-build-logs

# Structurally defined (flake.nix exposes packages.<system>.libptoon for
# every eachDefaultSystem system) but NOT required green: the pzm darwin
# builder is still in burn-in, and `chpl --library --dynamic` emits a
# .dylib whose nm semantics this lane's check phase doesn't fully cover.
build-lib-darwin:
	nix build .#packages.aarch64-darwin.libptoon --print-build-logs

# Requires a prior successful `make build-lib` (or run it first via the
# dependency below); reads the symbol list the derivation's check phase
# wrote into $out/share/ptoon/.
symbols: build-lib
	@cat result/share/ptoon/exported-symbols.txt 2>/dev/null || echo "no exported-symbols.txt in ./result — build may have failed"

clean:
	rm -f result
