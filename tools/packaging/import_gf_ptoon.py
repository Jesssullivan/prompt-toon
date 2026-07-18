#!/usr/bin/env python3
"""Verify a GF Darwin output and import it as a Nix closure.

The evidence directory is an untrusted downloaded artifact.  Its two JSON
records bind a countable GF proof to one exported executable; this script
rechecks the executable before creating a small ``bin/ptoon`` Nix package
tree.  Native inspection and store import deliberately require Darwin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence


DARWIN_PLATFORM = "gloriousflywheel-rbe-darwin-aarch64"
DARWIN_TARGET_PLATFORM = "//tools/bazel/platforms:darwin_aarch64"
DARWIN_BAZEL_TARGET = "//src/ptoon:ptoon"
DARWIN_WORKER_NAME = "darwin-aarch64-pzm-01"
DARWIN_WORKER_HOST = "petting-zoo-mini"
GF_AUTHORITY = "tinyland-inc/GloriousFlywheel"
PROMPT_TOON_REPOSITORY = "Jesssullivan/prompt-toon"
DARWIN_CLOSURE_FILENAME = "ptoon-aarch64-darwin.nar"
DARWIN_ENTRYPOINT_FILENAME = "ptoon-aarch64-darwin.bin"
DARWIN_BRIDGE_RECORD_FILENAME = "ptoon-aarch64-darwin.gf-nix-bridge.json"
DARWIN_GF_PROOF_FILENAME = "ptoon-aarch64-darwin.gf-proof-result.json"
DARWIN_GF_EXPORTS_FILENAME = "ptoon-aarch64-darwin.gf-exported-outputs.json"
DARWIN_GF_ATTESTATION_FILENAME = (
    "ptoon-aarch64-darwin.gf-proof-result.attestation.json"
)
DARWIN_NATIVE_SMOKE_FILENAME = "ptoon-aarch64-darwin.native-smoke.json"
GF_PROOF_FILENAME = "proof-result.json"
GF_EXPORTS_FILENAME = "exported-outputs.json"
GF_ATTESTATION_FILENAME = "proof-result.attestation.json"
GF_SIGNER_WORKFLOW = (
    "tinyland-inc/GloriousFlywheel/.github/workflows/gf-reapi-cell-proof.yml"
)
GF_SOURCE_REF = "refs/heads/main"
SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
NATIVE_SMOKE_SCRIPT = (
    Path(__file__).resolve().parents[2] / "src" / "ptoon" / "native_smoke.sh"
)
NATIVE_SMOKE_SCRIPT_REPO_PATH = "src/ptoon/native_smoke.sh"
NATIVE_SMOKE_EXPECTED = NATIVE_SMOKE_SCRIPT.with_name("native_smoke_expected.sh")
NATIVE_SMOKE_EXPECTED_REPO_PATH = "src/ptoon/native_smoke_expected.sh"
CAPS_SMOKE_SHA256 = (
    "86fc57507a95767dcac360484ccc6b404a44953397369bd46f3e3c131e214c61"
)
NORMALIZE_SMOKE_SHA256 = hashlib.sha256(b"A\n").hexdigest()
REDACTION_SMOKE_SHA256 = (
    "fdd3f37795269b5a82f230e3de904f1905070943399e9abcce454071330a7f5a"
)
ONE_SHOT_SMOKE_SHA256 = (
    "f01bc7a99a231b50273d1308892c3c53872f75f349156bd0b9f3da9f989cfb01"
)
RESIDENT_SMOKE_SHA256 = (
    "1dcec1c1b342c12a7e4724c7d12dfb0329d8bcc576de6cebe7b538232d7b15f7"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_REV_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_INT64 = (1 << 63) - 1
GF_WORKFLOW_URL_RE = re.compile(
    r"^https://github\.com/tinyland-inc/GloriousFlywheel/actions/runs/([1-9][0-9]*)$"
)
STORE_COMPONENT_RE = re.compile(r"^[a-z0-9]{32}-[A-Za-z0-9+._?=-]+$")
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]


class BridgeError(ValueError):
    """Raised when an evidence or import contract fails closed."""


@dataclass(frozen=True)
class ExportedOutput:
    label: str
    claim_path: str
    path: Path
    sha256: str
    size: int
    executable: bool


@dataclass(frozen=True)
class VerifiedEvidence:
    evidence_dir: Path
    proof: dict[str, Any]
    proof_result_sha256: str
    exported_outputs_sha256: str
    attestation_bundle_sha256: str
    gf_source_digest: str
    output: ExportedOutput
    native_smoke_output: ExportedOutput
    native_smoke: dict[str, Any]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _lstat(path: Path, description: str) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise BridgeError(f"cannot inspect {description}: {path}") from exc
    if stat.S_ISLNK(details.st_mode):
        raise BridgeError(f"{description} must not be a symlink: {path}")
    return details


def _require_directory(path: Path, description: str) -> None:
    details = _lstat(path, description)
    if not stat.S_ISDIR(details.st_mode):
        raise BridgeError(f"{description} must be a directory: {path}")


def _require_regular_file(path: Path, description: str) -> os.stat_result:
    details = _lstat(path, description)
    if not stat.S_ISREG(details.st_mode):
        raise BridgeError(f"{description} must be a regular file: {path}")
    return details


def _read_json(path: Path, description: str) -> dict[str, Any]:
    _require_regular_file(path, description)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"invalid {description} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BridgeError(f"{description} must be a JSON object")
    return value


def parse_output_spec(value: str) -> tuple[str, str]:
    label, separator, claim_path = value.partition("=")
    if not separator or re.fullmatch(r"//\S+", label) is None or not claim_path:
        raise BridgeError("--output must be exact LABEL=PATH")
    if "=" in claim_path:
        raise BridgeError("--output path must not contain '='")
    return label, claim_path


def _validated_claim_path(claim_path: Any, field: str) -> PurePosixPath:
    if (
        not isinstance(claim_path, str)
        or "\\" in claim_path
        or re.fullmatch(r"exported-outputs/\S+", claim_path) is None
    ):
        raise BridgeError(f"{field} must be a POSIX relative path")
    relative = PurePosixPath(claim_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "exported-outputs"
        or claim_path != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise BridgeError(f"{field} must stay below exported-outputs/")
    return relative


def _safe_evidence_path(evidence_dir: Path, claim_path: str) -> Path:
    relative = _validated_claim_path(claim_path, "exported output path")

    current = evidence_dir
    for part in relative.parts:
        current = current / part
        _lstat(current, "exported output path component")
    return current


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BridgeError(f"{field} must be a lowercase sha256 hex digest")
    return value


def _require_sha256_claim(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise BridgeError(f"{field} must be sha256:<64 lowercase hex>")
    return _require_sha256(value.removeprefix("sha256:"), field)


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BridgeError(f"{field} must be a non-empty string")
    return value


def _require_positive_decimal(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise BridgeError(f"{field} must be a positive decimal string")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0 or value > MAX_INT64:
        raise BridgeError(f"{field} must be a positive int64")
    return value


def _verify_worker_execution_binding(
    proof: dict[str, Any],
    *,
    expected_exported_outputs_sha256: str,
) -> None:
    binding = proof.get("worker_execution_evidence")
    expected_keys = {
        "schema_version",
        "evidence_sha256",
        "remote_grpc_log_sha256",
        "execution_log_sha256",
        "bep_sha256",
        "eligibility_manifest_sha256",
        "exported_outputs_manifest_sha256",
        "target",
        "tool_invocation_id",
        "remote_execution_count",
    }
    if not isinstance(binding, dict) or set(binding) != expected_keys:
        raise BridgeError("proof-result worker_execution_evidence key set mismatch")
    if type(binding["schema_version"]) is not int or binding["schema_version"] != 3:
        raise BridgeError("proof-result worker_execution_evidence schema must be 3")
    for field in (
        "evidence_sha256",
        "remote_grpc_log_sha256",
        "execution_log_sha256",
        "bep_sha256",
        "eligibility_manifest_sha256",
    ):
        _require_sha256_claim(
            binding[field],
            f"proof-result worker_execution_evidence.{field}",
        )
    expected_exports_digest = f"sha256:{expected_exported_outputs_sha256}"
    if binding["exported_outputs_manifest_sha256"] != expected_exports_digest:
        raise BridgeError(
            "proof-result worker execution binding does not match "
            "exported-outputs.json"
        )
    request = proof.get("request")
    if not isinstance(request, dict) or binding["target"] != request.get("target"):
        raise BridgeError("proof-result worker execution binding target mismatch")
    _require_nonempty_string(
        binding["tool_invocation_id"],
        "proof-result worker_execution_evidence.tool_invocation_id",
    )
    _require_positive_int(
        binding["remote_execution_count"],
        "proof-result worker_execution_evidence.remote_execution_count",
    )


def _verify_gf_attestation(
    proof_path: Path,
    bundle_path: Path,
    expected_gf_revision: str,
    runner: CommandRunner,
) -> str:
    """Verify the proof record's Sigstore provenance and return the GF commit."""

    if GIT_REV_RE.fullmatch(expected_gf_revision) is None:
        raise BridgeError(
            "--expected-gf-revision must be exactly 40 lowercase hex"
        )
    _require_regular_file(bundle_path, "GF proof attestation bundle")
    result = _run_result(
        runner,
        [
            "gh",
            "attestation",
            "verify",
            str(proof_path),
            "--repo",
            GF_AUTHORITY,
            "--bundle",
            str(bundle_path),
            "--signer-workflow",
            GF_SIGNER_WORKFLOW,
            "--source-ref",
            GF_SOURCE_REF,
            "--source-digest",
            expected_gf_revision,
            "--format",
            "json",
        ],
    )
    try:
        verified = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BridgeError("gh attestation verify returned invalid JSON") from exc
    if not isinstance(verified, list) or len(verified) != 1:
        raise BridgeError("GF proof must have exactly one verified attestation")
    verification = verified[0]
    if not isinstance(verification, dict):
        raise BridgeError("GF attestation verification result must be an object")
    verification_result = verification.get("verificationResult")
    if not isinstance(verification_result, dict):
        raise BridgeError("GF attestation verificationResult is missing")
    statement = verification_result.get("statement")
    if not isinstance(statement, dict):
        raise BridgeError("GF attestation statement is missing")
    if statement.get("predicateType") != SLSA_PROVENANCE_TYPE:
        raise BridgeError("GF attestation predicate type mismatch")

    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise BridgeError("GF proof attestation must name exactly one subject")
    subject = subjects[0]
    digest = subject.get("digest") if isinstance(subject, dict) else None
    if (
        not isinstance(subject, dict)
        or subject.get("name") != proof_path.name
        or not isinstance(digest, dict)
        or digest.get("sha256") != sha256_file(proof_path)
    ):
        raise BridgeError("GF proof attestation subject does not bind proof-result.json")

    predicate = statement.get("predicate")
    build_definition = (
        predicate.get("buildDefinition") if isinstance(predicate, dict) else None
    )
    external = (
        build_definition.get("externalParameters")
        if isinstance(build_definition, dict)
        else None
    )
    workflow = external.get("workflow") if isinstance(external, dict) else None
    if (
        not isinstance(workflow, dict)
        or workflow.get("repository")
        != "https://github.com/tinyland-inc/GloriousFlywheel"
        or workflow.get("ref") != GF_SOURCE_REF
        or str(workflow.get("path", "")).lstrip("/")
        != ".github/workflows/gf-reapi-cell-proof.yml"
    ):
        raise BridgeError("GF proof attestation workflow identity mismatch")

    dependencies = (
        build_definition.get("resolvedDependencies")
        if isinstance(build_definition, dict)
        else None
    )
    if not isinstance(dependencies, list):
        raise BridgeError("GF proof attestation has no resolved source dependency")
    expected_uris = {
        f"git+https://github.com/{GF_AUTHORITY}@{GF_SOURCE_REF}",
        f"git+https://github.com/{GF_AUTHORITY}.git@{GF_SOURCE_REF}",
    }
    source_digests: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or dependency.get("uri") not in expected_uris:
            continue
        digests = dependency.get("digest")
        source_digest = (
            digests.get("gitCommit") if isinstance(digests, dict) else None
        )
        if isinstance(source_digest, str) and GIT_REV_RE.fullmatch(source_digest):
            source_digests.append(source_digest)
    if len(set(source_digests)) != 1:
        raise BridgeError("GF proof attestation does not bind one GF source commit")
    source_digest = source_digests[0]
    if source_digest != expected_gf_revision:
        raise BridgeError("GF proof attestation source commit mismatch")
    return source_digest


def _verify_proof(
    proof: dict[str, Any],
    *,
    expected_revision: str,
    expected_label: str,
    expected_exported_outputs_sha256: str,
) -> None:
    if GIT_REV_RE.fullmatch(expected_revision) is None:
        raise BridgeError("--expected-revision must be exactly 40 lowercase hex")
    if type(proof.get("schema_version")) is not int or proof["schema_version"] != 2:
        raise BridgeError("proof-result schema_version must be 2")
    if proof.get("kind") != "gf-reapi-proof-result":
        raise BridgeError("proof-result kind mismatch")
    if proof.get("authority") != GF_AUTHORITY:
        raise BridgeError("proof-result authority mismatch")
    if proof.get("platform") != DARWIN_PLATFORM:
        raise BridgeError("proof-result platform must be the Darwin GF platform")
    for field in (
        "countable_remote_execution",
        "force_execution",
        "build_success",
        "executor_attached",
        "cache_attached",
    ):
        if proof.get(field) is not True:
            raise BridgeError(f"proof-result {field} must be true")
    if type(proof.get("proof_exit_status")) is not int or proof["proof_exit_status"] != 0:
        raise BridgeError("proof-result proof_exit_status must be 0")
    if proof.get("cache_hits_only") is not False:
        raise BridgeError("proof-result cache_hits_only must be false")
    remote_processes = _require_positive_int(
        proof.get("remote_processes"),
        "proof-result remote_processes",
    )
    if proof.get("worker_remote_execution_log") is not True:
        raise BridgeError(
            "Darwin proof-result must cite a real remote worker execution log"
        )
    dispatch_cell_digest = _require_sha256_claim(
        proof.get("dispatch_cell_image_digest"),
        "proof-result dispatch_cell_image_digest",
    )
    worker_image_digest = _require_sha256_claim(
        proof.get("worker_image_digest"),
        "proof-result worker_image_digest",
    )
    if dispatch_cell_digest != worker_image_digest:
        raise BridgeError("proof-result dispatch-cell digest alias mismatch")
    _require_nonempty_string(proof.get("executor"), "proof-result executor")
    _require_nonempty_string(proof.get("remote_cache"), "proof-result remote_cache")
    worker = proof.get("worker_identity")
    expected_worker = {
        "kind": "physical-darwin-worker",
        "name": DARWIN_WORKER_NAME,
        "host": DARWIN_WORKER_HOST,
        "architecture": "arm64",
        "os": "darwin",
    }
    if not isinstance(worker, dict) or set(worker) != {
        *expected_worker,
        "closure_digest",
    }:
        raise BridgeError("proof-result worker_identity key set mismatch")
    for field, expected in expected_worker.items():
        if worker.get(field) != expected:
            raise BridgeError(f"proof-result worker_identity.{field} mismatch")
    worker_closure_digest = _require_sha256_claim(
        worker.get("closure_digest"), "proof-result worker_identity.closure_digest"
    )

    request = proof.get("request")
    if not isinstance(request, dict):
        raise BridgeError("proof-result request must be an object")
    for field in (
        "workflow_run_id",
        "workflow_run_attempt",
        "workflow_run_url",
        "consumer_repository",
        "consumer_ref",
        "target",
        "target_platform",
        "worker_closure_digest",
        "bazel_command",
    ):
        _require_nonempty_string(request.get(field), f"proof-result request.{field}")
    run_id = _require_positive_decimal(
        request["workflow_run_id"], "proof-result request.workflow_run_id"
    )
    _require_positive_decimal(
        request["workflow_run_attempt"],
        "proof-result request.workflow_run_attempt",
    )
    workflow_match = GF_WORKFLOW_URL_RE.fullmatch(request["workflow_run_url"])
    if workflow_match is None or workflow_match.group(1) != run_id:
        raise BridgeError(
            "proof-result request.workflow_run_url must identify its GF workflow run"
        )
    if request["consumer_repository"] != PROMPT_TOON_REPOSITORY:
        raise BridgeError("proof-result request.consumer_repository mismatch")
    if request["consumer_ref"] != expected_revision:
        raise BridgeError("proof-result request.consumer_ref is stale")
    if expected_label != DARWIN_BAZEL_TARGET:
        raise BridgeError("GF Darwin bridge output label is not the release target")
    if request["target"] != DARWIN_BAZEL_TARGET:
        raise BridgeError("proof-result request.target does not match --output label")
    if request["target_platform"] != DARWIN_TARGET_PLATFORM:
        raise BridgeError("proof-result request.target_platform mismatch")
    requested_worker_closure = _require_sha256_claim(
        request["worker_closure_digest"],
        "proof-result request.worker_closure_digest",
    )
    if requested_worker_closure != worker_closure_digest:
        raise BridgeError("proof-result requested worker closure mismatch")
    if request["bazel_command"] != "build":
        raise BridgeError("proof-result request.bazel_command must be build")
    _verify_worker_execution_binding(
        proof,
        expected_exported_outputs_sha256=expected_exported_outputs_sha256,
    )
    if proof["worker_execution_evidence"]["remote_execution_count"] > remote_processes:
        raise BridgeError(
            "proof-result worker execution count exceeds Bazel remote processes"
        )


def _validated_output_claims(exports: dict[str, Any]) -> list[dict[str, Any]]:
    if set(exports) != {"schema", "kind", "outputs"}:
        raise BridgeError("exported-outputs key set mismatch")
    if exports.get("schema") != 1 or exports.get("kind") != "gf-reapi-exported-outputs":
        raise BridgeError("exported-outputs schema or kind mismatch")
    outputs = exports.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise BridgeError("exported-outputs outputs must be a non-empty list")

    expected_keys = {"label", "path", "sha256", "size_bytes", "executable"}
    label_paths: set[tuple[str, str]] = set()
    paths: set[str] = set()
    sort_keys: list[tuple[str, str]] = []
    for index, entry in enumerate(outputs):
        context = f"exported-outputs outputs[{index}]"
        if not isinstance(entry, dict) or set(entry) != expected_keys:
            raise BridgeError(f"{context} key set mismatch")
        label = entry["label"]
        claim_path = entry["path"]
        if not isinstance(label, str) or re.fullmatch(r"//\S+", label) is None:
            raise BridgeError(f"{context}.label must be a Bazel label")
        _validated_claim_path(claim_path, f"{context}.path")
        _require_sha256_claim(entry["sha256"], f"{context}.sha256")
        size = entry["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise BridgeError(f"{context}.size_bytes must be a positive integer")
        if not isinstance(entry["executable"], bool):
            raise BridgeError(f"{context}.executable must be boolean")
        if (label, claim_path) in label_paths:
            raise BridgeError(
                f"duplicate exported output label/path: {label}={claim_path}"
            )
        if claim_path in paths:
            raise BridgeError(f"duplicate exported output path: {claim_path}")
        label_paths.add((label, claim_path))
        paths.add(claim_path)
        sort_keys.append((label, claim_path))

    if sort_keys != sorted(sort_keys):
        raise BridgeError("exported-outputs outputs must be sorted by label and path")
    return outputs


def _selected_output(
    outputs: list[dict[str, Any]],
    *,
    evidence_dir: Path,
    label: str,
    claim_path: str,
    executable: bool = True,
) -> ExportedOutput:
    matches: list[dict[str, Any]] = []
    for entry in outputs:
        if entry["label"] == label and entry["path"] == claim_path:
            matches.append(entry)
    if len(matches) != 1:
        raise BridgeError(
            "exported-outputs must contain exactly the requested LABEL=PATH"
        )

    entry = matches[0]
    expected_sha256 = _require_sha256_claim(entry["sha256"], "exported output sha256")
    size = entry["size_bytes"]
    claimed_executable = entry["executable"]
    if claimed_executable is not executable:
        raise BridgeError(
            f"exported output executable must be {str(executable).lower()}"
        )
    path = _safe_evidence_path(evidence_dir, claim_path)
    details = _require_regular_file(path, "exported output")
    actual_sha256 = sha256_file(path)
    actual_executable = bool(
        details.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    )
    if actual_sha256 != expected_sha256:
        raise BridgeError("exported output sha256 does not match its claim")
    if details.st_size != size:
        raise BridgeError("exported output size does not match its claim")
    if actual_executable is not executable:
        raise BridgeError("exported output executable mode does not match its claim")
    return ExportedOutput(
        label, claim_path, path, actual_sha256, details.st_size, claimed_executable
    )


def _validated_native_smoke(
    smoke_output: ExportedOutput, binary_output: ExportedOutput
) -> dict[str, Any]:
    smoke = _read_json(smoke_output.path, "GF Darwin native smoke")
    expected_keys = {
        "schema_version",
        "kind",
        "artifact_sha256",
        "caps_engine",
        "caps_serve_protocol",
        "caps_sha256",
        "normalize_sha256",
        "one_shot_sha256",
        "redaction_canary_absent",
        "redaction_sha256",
        "resident_round_trip_sha256",
    }
    if set(smoke) != expected_keys:
        raise BridgeError("GF Darwin native smoke key set mismatch")
    if (
        smoke.get("schema_version") != 1
        or smoke.get("kind") != "prompt-toon-darwin-native-smoke"
        or smoke.get("artifact_sha256") != f"sha256:{binary_output.sha256}"
        or smoke.get("caps_engine") != "chapel"
        or smoke.get("caps_serve_protocol") != 1
        or smoke.get("caps_sha256") != CAPS_SMOKE_SHA256
        or smoke.get("normalize_sha256") != NORMALIZE_SMOKE_SHA256
        or smoke.get("one_shot_sha256") != ONE_SHOT_SMOKE_SHA256
        or smoke.get("redaction_canary_absent") is not True
        or smoke.get("redaction_sha256") != REDACTION_SMOKE_SHA256
        or smoke.get("resident_round_trip_sha256") != RESIDENT_SMOKE_SHA256
    ):
        raise BridgeError("GF Darwin native smoke does not bind a passing ptoon run")
    return smoke


def verify_evidence(
    evidence_dir: Path,
    output_spec: str,
    native_smoke_spec: str,
    *,
    expected_revision: str,
    expected_gf_revision: str,
    runner: CommandRunner | None = None,
) -> VerifiedEvidence:
    """Verify evidence metadata and the requested executable without Darwin tools."""

    _require_directory(evidence_dir, "evidence directory")
    _require_directory(evidence_dir / "exported-outputs", "exported-outputs directory")
    proof_path = evidence_dir / GF_PROOF_FILENAME
    exports_path = evidence_dir / GF_EXPORTS_FILENAME
    attestation_path = evidence_dir / GF_ATTESTATION_FILENAME
    gf_source_digest = _verify_gf_attestation(
        proof_path,
        attestation_path,
        expected_gf_revision,
        runner or _default_runner,
    )
    proof = _read_json(proof_path, "proof-result")
    label, claim_path = parse_output_spec(output_spec)
    smoke_label, smoke_claim_path = parse_output_spec(native_smoke_spec)
    if smoke_label != label:
        raise BridgeError("native smoke and binary outputs must share one Bazel label")
    if smoke_claim_path != claim_path + ".native-smoke.json":
        raise BridgeError("native smoke output must be the release target sidecar")
    exports = _read_json(exports_path, "exported-outputs")
    outputs = _validated_output_claims(exports)
    exported_outputs_sha256 = sha256_file(exports_path)
    _verify_proof(
        proof,
        expected_revision=expected_revision,
        expected_label=label,
        expected_exported_outputs_sha256=exported_outputs_sha256,
    )
    if proof.get("exported_outputs") != outputs:
        raise BridgeError(
            "proof-result exported_outputs does not match exported-outputs.json"
        )
    proof_sha256 = sha256_file(proof_path)
    output = _selected_output(
        outputs,
        evidence_dir=evidence_dir,
        label=label,
        claim_path=claim_path,
    )
    smoke_output = _selected_output(
        outputs,
        evidence_dir=evidence_dir,
        label=smoke_label,
        claim_path=smoke_claim_path,
        executable=False,
    )
    return VerifiedEvidence(
        evidence_dir=evidence_dir,
        proof=proof,
        proof_result_sha256=proof_sha256,
        exported_outputs_sha256=exported_outputs_sha256,
        attestation_bundle_sha256=sha256_file(attestation_path),
        gf_source_digest=gf_source_digest,
        output=output,
        native_smoke_output=smoke_output,
        native_smoke=_validated_native_smoke(smoke_output, output),
    )


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def _run_result(
    runner: CommandRunner, command: Sequence[str]
) -> subprocess.CompletedProcess[bytes]:
    result = runner(command)
    if result.returncode != 0:
        stderr = (
            result.stderr.decode("utf-8", "replace")
            if isinstance(result.stderr, bytes)
            else str(result.stderr)
        )
        raise BridgeError(f"command failed ({' '.join(command)}): {stderr.strip()}")
    return result


def _run(runner: CommandRunner, command: Sequence[str]) -> bytes:
    result = _run_result(runner, command)
    stdout = result.stdout
    return stdout if isinstance(stdout, bytes) else str(stdout).encode("utf-8")


def _canonical_absolute_posix_path(value: str) -> PurePosixPath | None:
    if not value or "\x00" in value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or path.as_posix() != value
        or posixpath.normpath(value) != value
    ):
        return None
    return path


def _safe_macho_dependency(path: str, store_root: Path) -> bool:
    canonical = _canonical_absolute_posix_path(path)
    if canonical is None:
        return False
    system_roots = (PurePosixPath("/usr/lib"), PurePosixPath("/System/Library"))
    if any(canonical.is_relative_to(root) and canonical != root for root in system_roots):
        return True
    store = PurePosixPath(store_root.as_posix())
    return canonical.is_relative_to(store) and canonical != store


def _safe_rpath(path: str, store_root: Path) -> bool:
    canonical = _canonical_absolute_posix_path(path)
    if canonical is not None:
        store = PurePosixPath(store_root.as_posix())
        return canonical.is_relative_to(store) and canonical != store

    for prefix in ("@loader_path/", "@executable_path/"):
        if not path.startswith(prefix):
            continue
        suffix = path.removeprefix(prefix)
        if not suffix or "\x00" in suffix or "\\" in suffix:
            return False
        # Validate relative loader paths against a conventional package
        # layout. One `..` from bin/ to lib/ is valid; escaping the package
        # root is not.
        package_root = PurePosixPath("/package")
        resolved = PurePosixPath(posixpath.normpath(f"/package/bin/{suffix}"))
        return resolved.is_relative_to(package_root) and resolved != package_root
    return False


def _macho_dependencies(output: bytes, store_root: Path) -> list[str]:
    lines = output.decode("utf-8", "replace").splitlines()
    if len(lines) < 2 or not lines[0].endswith(":"):
        raise BridgeError("otool -L did not return Mach-O dependency output")
    dependencies: list[str] = []
    for line in lines[1:]:
        value = line.strip().split(" (", 1)[0]
        if not value or not _safe_macho_dependency(value, store_root):
            raise BridgeError(f"unsafe Mach-O dependency: {value!r}")
        dependencies.append(value)
    return dependencies


def _validate_rpaths(output: bytes, store_root: Path) -> None:
    lines = output.decode("utf-8", "replace").splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "cmd LC_RPATH":
            continue
        for candidate in lines[index + 1 :]:
            match = re.match(r"\s*path\s+(.+?)\s+\(offset \d+\)\s*$", candidate)
            if match:
                path = match.group(1)
                if not _safe_rpath(path, store_root):
                    raise BridgeError(f"unsafe Mach-O RPATH: {path!r}")
                break
        else:
            raise BridgeError("LC_RPATH entry has no path")


def validate_darwin_binary(
    output: Path, runner: CommandRunner, *, store_root: Path = Path("/nix/store")
) -> list[str]:
    """Require a native arm64, ad-hoc-signed executable with safe linkage."""

    architectures = (
        _run(runner, ["/usr/bin/lipo", "-archs", str(output)])
        .decode("utf-8", "replace")
        .split()
    )
    if architectures != ["arm64"]:
        raise BridgeError(f"ptoon must be a single arm64 Mach-O, got {architectures!r}")
    _run(
        runner,
        ["/usr/bin/codesign", "--verify", "--strict", "--verbose=4", str(output)],
    )
    display = _run_result(
        runner, ["/usr/bin/codesign", "--display", "--verbose=4", str(output)]
    )
    signature = (display.stdout + display.stderr).decode("utf-8", "replace")
    signature_lines = set(signature.splitlines())
    if (
        "Signature=adhoc" not in signature_lines
        or "TeamIdentifier=not set" not in signature_lines
    ):
        raise BridgeError(
            "ptoon must have an ad-hoc signature without a TeamIdentifier"
        )
    if any(line.startswith("Authority=") for line in signature_lines):
        raise BridgeError("ptoon must not carry an identity-signing Authority")
    dependencies = _macho_dependencies(
        _run(runner, ["/usr/bin/otool", "-L", str(output)]), store_root
    )
    _validate_rpaths(_run(runner, ["/usr/bin/otool", "-l", str(output)]), store_root)
    return dependencies


def _store_path(value: str, store_root: Path) -> Path:
    path = Path(value)
    if path.parent != store_root or STORE_COMPONENT_RE.fullmatch(path.name) is None:
        raise BridgeError(f"nix-store returned unsafe store path: {value!r}")
    return path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _run_post_import_smoke(
    binary: Path,
    evidence: VerifiedEvidence,
    runner: CommandRunner,
) -> str:
    """Execute the assembled store entrypoint and require remote-byte parity."""

    _require_regular_file(NATIVE_SMOKE_SCRIPT, "native smoke script")
    with tempfile.TemporaryDirectory(
        prefix="prompt-toon-post-import-smoke-"
    ) as temporary:
        result_path = Path(temporary) / "native-smoke.json"
        _run_result(
            runner,
            [str(NATIVE_SMOKE_SCRIPT), str(binary), str(result_path)],
        )
        _require_regular_file(result_path, "post-import native smoke result")
        result = _read_json(result_path, "post-import native smoke result")
        if result != evidence.native_smoke:
            raise BridgeError("post-import native smoke differs from remote result")
        if result_path.read_bytes() != evidence.native_smoke_output.path.read_bytes():
            raise BridgeError(
                "post-import native smoke bytes differ from remote result"
            )
        return sha256_file(result_path)


def validate_bridge_record(
    record_path: Path,
    *,
    expected_revision: str,
    closure_export: Path,
    entrypoint_export: Path,
    store_root: Path = Path("/nix/store"),
) -> dict[str, Any]:
    """Bind a canonical bridge record to its release inputs."""

    if GIT_REV_RE.fullmatch(expected_revision) is None:
        raise BridgeError("expected bridge revision must be exactly 40 lowercase hex")
    expected_names = (
        (record_path.name, DARWIN_BRIDGE_RECORD_FILENAME),
        (closure_export.name, DARWIN_CLOSURE_FILENAME),
        (entrypoint_export.name, DARWIN_ENTRYPOINT_FILENAME),
    )
    for actual, expected in expected_names:
        if actual != expected:
            raise BridgeError(
                f"GF Darwin bridge bundle filename must be {expected}, got {actual}"
            )
    record = _read_json(record_path, "GF Darwin bridge record")
    if set(record) != {
        "schema_version",
        "kind",
        "gf",
        "exported_output",
        "native_smoke",
        "nix",
    }:
        raise BridgeError("GF Darwin bridge record key set mismatch")
    if record.get("schema_version") != 1:
        raise BridgeError("GF Darwin bridge record schema_version must be 1")
    if record.get("kind") != "prompt-toon-gf-darwin-nix-bridge":
        raise BridgeError("GF Darwin bridge record kind mismatch")

    gf = record.get("gf")
    if not isinstance(gf, dict) or set(gf) != {
        "authority",
        "proof_result_filename",
        "proof_result_sha256",
        "exported_outputs_filename",
        "exported_outputs_sha256",
        "attestation",
        "request",
        "platform",
        "dispatch_cell_image_digest",
        "worker_identity",
        "worker_execution_evidence",
    }:
        raise BridgeError("GF Darwin bridge record gf key set mismatch")
    if gf["authority"] != GF_AUTHORITY or gf["platform"] != DARWIN_PLATFORM:
        raise BridgeError("GF Darwin bridge authority or platform mismatch")
    _require_sha256_claim(
        gf["dispatch_cell_image_digest"],
        "GF Darwin bridge dispatch_cell_image_digest",
    )
    if not isinstance(gf["worker_execution_evidence"], dict):
        raise BridgeError("GF Darwin bridge worker_execution_evidence must be an object")
    if (
        gf["proof_result_filename"] != DARWIN_GF_PROOF_FILENAME
        or gf["exported_outputs_filename"] != DARWIN_GF_EXPORTS_FILENAME
    ):
        raise BridgeError("GF Darwin bridge replay filename mismatch")
    proof_result_sha256 = _require_sha256(
        gf["proof_result_sha256"], "bridge proof_result_sha256"
    )
    exported_outputs_sha256 = _require_sha256(
        gf["exported_outputs_sha256"], "bridge exported_outputs_sha256"
    )
    replay_paths = (
        (
            record_path.parent / DARWIN_GF_PROOF_FILENAME,
            proof_result_sha256,
            "replayed GF proof result",
        ),
        (
            record_path.parent / DARWIN_GF_EXPORTS_FILENAME,
            exported_outputs_sha256,
            "replayed GF exported-output index",
        ),
    )
    for replay_path, expected_sha256, description in replay_paths:
        _require_regular_file(replay_path, description)
        if sha256_file(replay_path) != expected_sha256:
            raise BridgeError(f"{description} digest mismatch")

    attestation = gf.get("attestation")
    if not isinstance(attestation, dict) or set(attestation) != {
        "bundle_filename",
        "bundle_sha256",
        "predicate_type",
        "repository",
        "signer_workflow",
        "source_ref",
        "source_digest",
    }:
        raise BridgeError("GF Darwin bridge attestation key set mismatch")
    if (
        attestation["bundle_filename"] != DARWIN_GF_ATTESTATION_FILENAME
        or attestation["predicate_type"] != SLSA_PROVENANCE_TYPE
        or attestation["repository"] != GF_AUTHORITY
        or attestation["signer_workflow"] != GF_SIGNER_WORKFLOW
        or attestation["source_ref"] != GF_SOURCE_REF
    ):
        raise BridgeError("GF Darwin bridge attestation identity mismatch")
    attestation_sha256 = _require_sha256(
        attestation["bundle_sha256"], "bridge attestation bundle_sha256"
    )
    attestation_bundle = record_path.parent / DARWIN_GF_ATTESTATION_FILENAME
    _require_regular_file(attestation_bundle, "replayed GF attestation bundle")
    if sha256_file(attestation_bundle) != attestation_sha256:
        raise BridgeError("replayed GF attestation bundle digest mismatch")
    if (
        not isinstance(attestation["source_digest"], str)
        or GIT_REV_RE.fullmatch(attestation["source_digest"]) is None
    ):
        raise BridgeError("GF Darwin bridge attestation source_digest is invalid")

    worker = gf.get("worker_identity")
    if not isinstance(worker, dict) or set(worker) != {
        "kind",
        "name",
        "host",
        "architecture",
        "os",
        "closure_digest",
    }:
        raise BridgeError("GF Darwin bridge worker_identity key set mismatch")
    if (
        worker["kind"] != "physical-darwin-worker"
        or worker["name"] != DARWIN_WORKER_NAME
        or worker["host"] != DARWIN_WORKER_HOST
        or worker["architecture"] != "arm64"
        or worker["os"] != "darwin"
    ):
        raise BridgeError("GF Darwin bridge worker identity mismatch")
    _require_sha256_claim(
        worker["closure_digest"], "bridge worker_identity.closure_digest"
    )

    request = gf.get("request")
    request_keys = {
        "workflow_run_id",
        "workflow_run_attempt",
        "workflow_run_url",
        "consumer_repository",
        "consumer_ref",
        "target",
        "target_platform",
        "worker_closure_digest",
        "bazel_command",
    }
    if not isinstance(request, dict) or set(request) != request_keys:
        raise BridgeError("GF Darwin bridge request key set mismatch")
    run_id = _require_positive_decimal(
        request["workflow_run_id"], "bridge workflow_run_id"
    )
    _require_positive_decimal(
        request["workflow_run_attempt"], "bridge workflow_run_attempt"
    )
    workflow_match = GF_WORKFLOW_URL_RE.fullmatch(request["workflow_run_url"])
    if workflow_match is None or workflow_match.group(1) != run_id:
        raise BridgeError("GF Darwin bridge workflow URL mismatch")
    if request["consumer_repository"] != PROMPT_TOON_REPOSITORY:
        raise BridgeError("GF Darwin bridge consumer repository mismatch")
    if request["consumer_ref"] != expected_revision:
        raise BridgeError("GF Darwin bridge consumer revision is stale")
    if request["target_platform"] != DARWIN_TARGET_PLATFORM:
        raise BridgeError("GF Darwin bridge target platform mismatch")
    requested_worker_closure = _require_sha256_claim(
        request["worker_closure_digest"],
        "GF Darwin bridge request.worker_closure_digest",
    )
    if requested_worker_closure != worker["closure_digest"].removeprefix("sha256:"):
        raise BridgeError("GF Darwin bridge requested worker closure mismatch")
    if request["bazel_command"] != "build":
        raise BridgeError("GF Darwin bridge must come from bazel build")
    _verify_worker_execution_binding(
        {
            "request": request,
            "worker_execution_evidence": gf["worker_execution_evidence"],
        },
        expected_exported_outputs_sha256=exported_outputs_sha256,
    )

    exported = record.get("exported_output")
    if not isinstance(exported, dict) or set(exported) != {
        "label",
        "path",
        "sha256",
        "size_bytes",
        "executable",
        "transfer_filename",
    }:
        raise BridgeError("GF Darwin bridge exported_output key set mismatch")
    if (
        exported["label"] != DARWIN_BAZEL_TARGET
        or request["target"] != DARWIN_BAZEL_TARGET
        or request["target"] != exported["label"]
    ):
        raise BridgeError("GF Darwin bridge target/output label mismatch")
    _validated_claim_path(exported["path"], "bridge exported output path")
    exported_sha256 = _require_sha256_claim(
        exported["sha256"], "bridge exported output sha256"
    )
    if (
        isinstance(exported["size_bytes"], bool)
        or not isinstance(exported["size_bytes"], int)
        or exported["size_bytes"] <= 0
        or exported["executable"] is not True
    ):
        raise BridgeError("GF Darwin bridge exported output metadata is invalid")

    closure_details = _require_regular_file(closure_export, "Darwin closure export")
    entrypoint_details = _require_regular_file(
        entrypoint_export, "Darwin transferred entrypoint"
    )
    if closure_details.st_size <= 0 or entrypoint_details.st_size <= 0:
        raise BridgeError("Darwin bridge release inputs must be non-empty")
    if not entrypoint_details.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise BridgeError("Darwin transferred entrypoint must be executable")
    if exported["transfer_filename"] != entrypoint_export.name:
        raise BridgeError("GF Darwin bridge transfer filename mismatch")
    if exported["size_bytes"] != entrypoint_details.st_size:
        raise BridgeError("GF Darwin bridge transferred entrypoint size mismatch")
    if sha256_file(entrypoint_export) != exported_sha256:
        raise BridgeError("GF Darwin bridge transferred entrypoint digest mismatch")

    native_smoke_evidence = record.get("native_smoke")
    if not isinstance(native_smoke_evidence, dict) or set(native_smoke_evidence) != {
        "evidence_filename",
        "evidence_sha256",
        "result",
    }:
        raise BridgeError("GF Darwin bridge native_smoke evidence key set mismatch")
    if native_smoke_evidence["evidence_filename"] != DARWIN_NATIVE_SMOKE_FILENAME:
        raise BridgeError("GF Darwin bridge native smoke replay filename mismatch")
    native_smoke_sha256 = _require_sha256(
        native_smoke_evidence["evidence_sha256"],
        "bridge native smoke evidence_sha256",
    )
    native_smoke_path = record_path.parent / DARWIN_NATIVE_SMOKE_FILENAME
    _require_regular_file(native_smoke_path, "replayed GF Darwin native smoke")
    if sha256_file(native_smoke_path) != native_smoke_sha256:
        raise BridgeError("replayed GF Darwin native smoke digest mismatch")
    native_smoke = native_smoke_evidence.get("result")
    if (
        not isinstance(native_smoke, dict)
        or _read_json(native_smoke_path, "replayed GF Darwin native smoke")
        != native_smoke
    ):
        raise BridgeError("GF Darwin bridge native smoke replay/result mismatch")
    smoke_keys = {
        "schema_version",
        "kind",
        "artifact_sha256",
        "caps_engine",
        "caps_serve_protocol",
        "caps_sha256",
        "normalize_sha256",
        "one_shot_sha256",
        "redaction_canary_absent",
        "redaction_sha256",
        "resident_round_trip_sha256",
    }
    if set(native_smoke) != smoke_keys:
        raise BridgeError("GF Darwin bridge native_smoke key set mismatch")
    if (
        native_smoke["schema_version"] != 1
        or native_smoke["kind"] != "prompt-toon-darwin-native-smoke"
        or native_smoke["artifact_sha256"] != f"sha256:{exported_sha256}"
        or native_smoke["caps_engine"] != "chapel"
        or native_smoke["caps_serve_protocol"] != 1
        or native_smoke["caps_sha256"] != CAPS_SMOKE_SHA256
        or native_smoke["normalize_sha256"] != NORMALIZE_SMOKE_SHA256
        or native_smoke["one_shot_sha256"] != ONE_SHOT_SMOKE_SHA256
        or native_smoke["redaction_canary_absent"] is not True
        or native_smoke["redaction_sha256"] != REDACTION_SMOKE_SHA256
        or native_smoke["resident_round_trip_sha256"] != RESIDENT_SMOKE_SHA256
    ):
        raise BridgeError("GF Darwin bridge native smoke result is invalid")

    nix = record.get("nix")
    if not isinstance(nix, dict) or set(nix) != {
        "store_path",
        "entrypoint",
        "closure_export",
        "store_requisites",
        "post_import_smoke",
    }:
        raise BridgeError("GF Darwin bridge nix key set mismatch")
    store_path = nix["store_path"]
    if (
        not isinstance(store_path, str)
        or not store_path.startswith(str(store_root) + "/")
        or STORE_COMPONENT_RE.fullmatch(Path(store_path).name) is None
        or Path(store_path).parent != store_root
    ):
        raise BridgeError("GF Darwin bridge Nix store path is invalid")

    entrypoint = nix.get("entrypoint")
    if not isinstance(entrypoint, dict) or set(entrypoint) != {
        "path",
        "store_path",
        "sha256",
    }:
        raise BridgeError("GF Darwin bridge entrypoint key set mismatch")
    if (
        entrypoint["path"] != "bin/ptoon"
        or entrypoint["store_path"] != f"{store_path}/bin/ptoon"
    ):
        raise BridgeError("GF Darwin bridge entrypoint path mismatch")
    entrypoint_sha256 = _require_sha256(
        entrypoint["sha256"], "bridge Nix entrypoint sha256"
    )
    if entrypoint_sha256 != exported_sha256:
        raise BridgeError("GF Darwin bridge changed ptoon bytes during Nix import")

    closure = nix.get("closure_export")
    if not isinstance(closure, dict) or set(closure) != {
        "filename",
        "sha256",
        "size_bytes",
    }:
        raise BridgeError("GF Darwin bridge closure_export key set mismatch")
    if closure["filename"] != closure_export.name:
        raise BridgeError("GF Darwin bridge closure filename mismatch")
    closure_sha256 = _require_sha256(closure["sha256"], "bridge closure export sha256")
    if closure[
        "size_bytes"
    ] != closure_details.st_size or closure_sha256 != sha256_file(closure_export):
        raise BridgeError("GF Darwin bridge closure export metadata mismatch")

    requisites = nix.get("store_requisites")
    if (
        not isinstance(requisites, list)
        or not requisites
        or any(not isinstance(path, str) for path in requisites)
        or requisites != sorted(set(requisites))
        or store_path not in requisites
    ):
        raise BridgeError("GF Darwin bridge store requisites are invalid")
    for requisite in requisites:
        if (
            not requisite.startswith(str(store_root) + "/")
            or Path(requisite).parent != store_root
            or STORE_COMPONENT_RE.fullmatch(Path(requisite).name) is None
        ):
            raise BridgeError("GF Darwin bridge store requisite is invalid")

    post_import_smoke = nix.get("post_import_smoke")
    if not isinstance(post_import_smoke, dict) or set(post_import_smoke) != {
        "script",
        "script_sha256",
        "expected",
        "expected_sha256",
        "result_sha256",
        "byte_identical_to_remote",
    }:
        raise BridgeError("GF Darwin bridge post_import_smoke key set mismatch")
    if (
        post_import_smoke["script"] != NATIVE_SMOKE_SCRIPT_REPO_PATH
        or post_import_smoke["script_sha256"] != sha256_file(NATIVE_SMOKE_SCRIPT)
        or post_import_smoke["expected"] != NATIVE_SMOKE_EXPECTED_REPO_PATH
        or post_import_smoke["expected_sha256"]
        != sha256_file(NATIVE_SMOKE_EXPECTED)
        or post_import_smoke["result_sha256"] != native_smoke_sha256
        or post_import_smoke["byte_identical_to_remote"] is not True
    ):
        raise BridgeError("GF Darwin bridge post-import smoke is invalid")

    return {
        "kind": "gf-reapi-nix-bridge",
        "record_filename": record_path.name,
        "record_sha256": sha256_file(record_path),
        "authority": gf["authority"],
        "proof_result_filename": gf["proof_result_filename"],
        "proof_result_sha256": gf["proof_result_sha256"],
        "exported_outputs_filename": gf["exported_outputs_filename"],
        "exported_outputs_sha256": gf["exported_outputs_sha256"],
        "attestation": dict(attestation),
        "workflow_run_id": request["workflow_run_id"],
        "workflow_run_attempt": request["workflow_run_attempt"],
        "workflow_run_url": request["workflow_run_url"],
        "consumer_repository": request["consumer_repository"],
        "consumer_ref": request["consumer_ref"],
        "target": request["target"],
        "bazel_command": request["bazel_command"],
        "worker_closure_digest": request["worker_closure_digest"],
        "platform": gf["platform"],
        "dispatch_cell_image_digest": gf["dispatch_cell_image_digest"],
        "worker_identity": dict(worker),
        "worker_execution_evidence": dict(gf["worker_execution_evidence"]),
        "exported_output": dict(exported),
        "native_smoke": {
            "evidence_filename": native_smoke_evidence["evidence_filename"],
            "evidence_sha256": native_smoke_evidence["evidence_sha256"],
            "result": dict(native_smoke),
        },
        "nix_store_path": store_path,
    }


def _copy_verified_file(
    source: Path,
    destination: Path,
    expected_sha256: str,
    *,
    mode: int,
    description: str,
) -> None:
    _require_regular_file(source, description)
    if os.path.lexists(destination):
        raise BridgeError(f"{description} export already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        shutil.copyfile(source, temporary)
        os.chmod(temporary, mode)
        if sha256_file(temporary) != expected_sha256:
            raise BridgeError(f"transferred {description} bytes changed")
        temporary.replace(destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _copy_replay_evidence(evidence: VerifiedEvidence, destination: Path) -> None:
    replay_files = (
        (
            evidence.evidence_dir / GF_PROOF_FILENAME,
            destination / DARWIN_GF_PROOF_FILENAME,
            evidence.proof_result_sha256,
            "GF proof result",
        ),
        (
            evidence.evidence_dir / GF_EXPORTS_FILENAME,
            destination / DARWIN_GF_EXPORTS_FILENAME,
            evidence.exported_outputs_sha256,
            "GF exported-output index",
        ),
        (
            evidence.evidence_dir / GF_ATTESTATION_FILENAME,
            destination / DARWIN_GF_ATTESTATION_FILENAME,
            evidence.attestation_bundle_sha256,
            "GF proof attestation bundle",
        ),
        (
            evidence.native_smoke_output.path,
            destination / DARWIN_NATIVE_SMOKE_FILENAME,
            evidence.native_smoke_output.sha256,
            "GF Darwin native smoke",
        ),
    )
    for source, target, digest, description in replay_files:
        _copy_verified_file(
            source,
            target,
            digest,
            mode=0o644,
            description=description,
        )


def _write_closure_export(
    runner: CommandRunner, command: Sequence[str], destination: Path
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            if runner is _default_runner:
                result = subprocess.run(
                    command,
                    check=False,
                    stdout=handle,
                    stderr=subprocess.PIPE,
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode("utf-8", "replace")
                    raise BridgeError(
                        f"command failed ({' '.join(command)}): {stderr.strip()}"
                    )
            else:
                handle.write(_run(runner, command))
        if temporary.stat().st_size <= 0:
            raise BridgeError("nix-store --export produced an empty closure")
        temporary.replace(destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _import_closure_archive(
    runner: CommandRunner,
    archive: Path,
    replay_store_root: Path,
) -> list[str]:
    """Import one archive into a fresh logical /nix/store and return its paths."""

    _require_regular_file(archive, "Darwin closure export")
    # Darwin's default TMPDIR is commonly spelled through /var, a symlink to
    # /private/var. Nix rejects local-store roots with symlinked ancestors.
    replay_store_root = replay_store_root.resolve()
    command = [
        "nix-store",
        "--store",
        str(replay_store_root),
        "--option",
        "require-sigs",
        "false",
        "--import",
    ]
    if runner is _default_runner:
        with archive.open("rb") as handle:
            result = subprocess.run(
                command,
                check=False,
                stdin=handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            raise BridgeError(
                "command failed "
                f"({' '.join(command)}): "
                f"{result.stderr.decode('utf-8', 'replace').strip()}"
            )
    else:
        # Test runners receive the archive path explicitly because the
        # CommandRunner protocol intentionally has no stdin side channel.
        result = _run_result(runner, [*command, "--archive", str(archive)])
    output = result.stdout
    rendered = output if isinstance(output, bytes) else str(output).encode("utf-8")
    paths = [line for line in rendered.decode("utf-8", "replace").splitlines() if line]
    if not paths:
        raise BridgeError("nix-store --import returned no paths from the fresh store")
    return paths


def _canonical_replay_store_root(replay_store_root: Path) -> Path:
    """Resolve symlinked ancestors before any local-store operation."""

    return replay_store_root.resolve()


def _physical_replay_store_root(
    replay_store_root: Path,
    store_root: Path,
) -> Path:
    return _canonical_replay_store_root(replay_store_root).joinpath(
        *store_root.parts[1:],
    )


def _physical_replay_store_path(
    logical_path: Path,
    *,
    replay_store_root: Path,
    store_root: Path,
) -> Path:
    _store_path(str(logical_path), store_root)
    physical_store_root = _physical_replay_store_root(
        replay_store_root,
        store_root,
    )
    return physical_store_root / logical_path.name


def _require_safe_replay_file(
    path: Path,
    *,
    replay_store_root: Path,
    store_root: Path,
) -> None:
    try:
        resolved = path.resolve(strict=True)
        details = resolved.stat()
    except OSError as exc:
        raise BridgeError(
            f"cannot inspect clean-store Mach-O dependency: {path}"
        ) from exc
    physical_store_root = _physical_replay_store_root(
        replay_store_root,
        store_root,
    )
    if not resolved.is_relative_to(physical_store_root):
        raise BridgeError(
            f"clean-store Mach-O dependency escapes imported store: {path}"
        )
    if not stat.S_ISREG(details.st_mode):
        raise BridgeError(
            f"clean-store Mach-O dependency must resolve to a regular file: {path}"
        )


def _physical_replay_dependency(
    dependency: str,
    *,
    recorded_requisites: Sequence[str],
    replay_store_root: Path,
    store_root: Path,
) -> Path:
    logical_dependency = next(
        (
            Path(requisite)
            for requisite in recorded_requisites
            if dependency == requisite or dependency.startswith(requisite + "/")
        ),
        None,
    )
    if logical_dependency is None:
        raise BridgeError(
            f"clean-store Mach-O dependency is absent from closure: {dependency}"
        )
    physical_dependency = _physical_replay_store_path(
        logical_dependency,
        replay_store_root=replay_store_root,
        store_root=store_root,
    ) / Path(dependency).relative_to(logical_dependency)
    _require_safe_replay_file(
        physical_dependency,
        replay_store_root=replay_store_root,
        store_root=store_root,
    )
    return physical_dependency


def _replay_library_directories(
    dependencies: Sequence[str],
    *,
    recorded_requisites: Sequence[str],
    replay_store_root: Path,
    store_root: Path,
    runner: CommandRunner,
) -> list[Path]:
    """Map the complete Mach-O dependency graph to imported physical bytes."""

    pending = list(dependencies)
    inspected: set[str] = set()
    library_by_leaf: dict[str, Path] = {}
    system_leaves: set[str] = set()
    while pending:
        dependency = pending.pop()
        if dependency in inspected:
            continue
        inspected.add(dependency)
        if not dependency.startswith(str(store_root) + "/"):
            leaf = Path(dependency).name
            if leaf in library_by_leaf:
                raise BridgeError(
                    f"clean-store replay would override system library: {leaf}"
                )
            system_leaves.add(leaf)
            continue
        if any(part.endswith(".framework") for part in Path(dependency).parts):
            raise BridgeError(
                "clean-store replay cannot prove framework dependency remapping"
            )
        physical_dependency = _physical_replay_dependency(
            dependency,
            recorded_requisites=recorded_requisites,
            replay_store_root=replay_store_root,
            store_root=store_root,
        )
        leaf = Path(dependency).name
        if leaf in system_leaves:
            raise BridgeError(
                f"clean-store replay would override system library: {leaf}"
            )
        previous = library_by_leaf.setdefault(leaf, physical_dependency)
        if previous != physical_dependency:
            raise BridgeError(
                f"clean-store replay has ambiguous Mach-O library leaf: {leaf}"
            )
        transitive = _macho_dependencies(
            _run(runner, ["/usr/bin/otool", "-L", str(physical_dependency)]),
            store_root,
        )
        _validate_rpaths(
            _run(runner, ["/usr/bin/otool", "-l", str(physical_dependency)]),
            store_root,
        )
        pending.extend(transitive)

    directories = sorted({path.parent for path in library_by_leaf.values()})
    if any(":" in str(path) or "\n" in str(path) for path in directories):
        raise BridgeError("clean-store replay library path is not DYLD-safe")
    return directories


def _run_clean_store_smoke(
    binary: Path,
    evidence: VerifiedEvidence,
    runner: CommandRunner,
    *,
    dependencies: Sequence[str],
    recorded_requisites: Sequence[str],
    replay_store_root: Path,
    store_root: Path,
) -> str:
    """Run the smoke while the publisher's live Nix store is unreadable."""

    replay_store_root = _canonical_replay_store_root(replay_store_root)
    library_directories = _replay_library_directories(
        dependencies,
        recorded_requisites=recorded_requisites,
        replay_store_root=replay_store_root,
        store_root=store_root,
        runner=runner,
    )
    if runner is _default_runner:
        _require_regular_file(Path("/usr/bin/sandbox-exec"), "sandbox-exec")
        _require_regular_file(Path("/bin/sh"), "system shell")

    profile = (
        "(version 1) "
        "(allow default) "
        f"(deny file-read* (subpath {json.dumps(str(store_root))}))"
    )
    with tempfile.TemporaryDirectory(
        prefix="prompt-toon-clean-store-smoke-"
    ) as temporary:
        smoke_root = Path(temporary)
        smoke_script = smoke_root / NATIVE_SMOKE_SCRIPT.name
        expected = smoke_root / NATIVE_SMOKE_EXPECTED.name
        shutil.copyfile(NATIVE_SMOKE_SCRIPT, smoke_script)
        shutil.copyfile(NATIVE_SMOKE_EXPECTED, expected)
        if sha256_file(smoke_script) != sha256_file(NATIVE_SMOKE_SCRIPT):
            raise BridgeError("clean-store smoke script bytes changed")
        if sha256_file(expected) != sha256_file(NATIVE_SMOKE_EXPECTED):
            raise BridgeError("clean-store smoke expected bytes changed")

        wrapper = smoke_root / "run-smoke.sh"
        dyld_library_path = ":".join(map(str, library_directories))
        wrapper.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "unset DYLD_FALLBACK_FRAMEWORK_PATH DYLD_FALLBACK_LIBRARY_PATH "
            "DYLD_FRAMEWORK_PATH DYLD_IMAGE_SUFFIX DYLD_INSERT_LIBRARIES "
            "DYLD_VERSIONED_FRAMEWORK_PATH DYLD_VERSIONED_LIBRARY_PATH\n"
            f"export DYLD_LIBRARY_PATH={shlex.quote(dyld_library_path)}\n"
            f". {shlex.quote(str(smoke_script))}\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        result_path = smoke_root / "native-smoke.json"
        _run_result(
            runner,
            [
                "/usr/bin/sandbox-exec",
                "-p",
                profile,
                "/bin/sh",
                str(wrapper),
                str(binary),
                str(result_path),
            ],
        )
        _require_regular_file(result_path, "clean-store native smoke result")
        result = _read_json(result_path, "clean-store native smoke result")
        if result != evidence.native_smoke:
            raise BridgeError("clean-store native smoke differs from remote result")
        if result_path.read_bytes() != evidence.native_smoke_output.path.read_bytes():
            raise BridgeError(
                "clean-store native smoke bytes differ from remote result"
            )
        return sha256_file(result_path)


def import_verified_output(
    evidence: VerifiedEvidence,
    *,
    closure_export: Path,
    entrypoint_export: Path,
    record_path: Path,
    runner: CommandRunner = _default_runner,
    store_root: Path = Path("/nix/store"),
) -> dict[str, Any]:
    """Inspect, import, and run the fixed offline smoke on authenticated bytes."""

    validate_darwin_binary(evidence.output.path, runner, store_root=store_root)
    _copy_verified_file(
        evidence.output.path,
        entrypoint_export,
        evidence.output.sha256,
        mode=0o755,
        description="verified ptoon entrypoint",
    )
    with tempfile.TemporaryDirectory(prefix="prompt-toon-gf-import-") as temporary:
        package_tree = Path(temporary) / "ptoon"
        entrypoint = package_tree / "bin" / "ptoon"
        entrypoint.parent.mkdir(parents=True)
        shutil.copyfile(evidence.output.path, entrypoint)
        os.chmod(
            entrypoint,
            _require_regular_file(evidence.output.path, "exported output").st_mode
            & 0o777,
        )
        if sha256_file(entrypoint) != evidence.output.sha256:
            raise BridgeError("staged bin/ptoon bytes changed before Nix import")
        # `nix store add` does not create a GC root. Keep its returned path live
        # by querying and exporting the closure immediately in this scope.
        imported_output = (
            _run(runner, ["nix", "store", "add", str(package_tree)])
            .decode("utf-8", "replace")
            .strip()
            .splitlines()
        )
        if len(imported_output) != 1:
            raise BridgeError("nix store add must return exactly one store path")
        imported_store_path = _store_path(imported_output[0], store_root)
        imported_entrypoint = imported_store_path / "bin" / "ptoon"
        _require_regular_file(imported_entrypoint, "imported bin/ptoon")
        imported_sha256 = sha256_file(imported_entrypoint)
        if imported_sha256 != evidence.output.sha256:
            raise BridgeError(
                "imported bin/ptoon bytes differ from the verified output"
            )

        requisites_output = _run(
            runner, ["nix-store", "--query", "--requisites", str(imported_store_path)]
        )
        requisites = sorted(
            {
                _store_path(line, store_root)
                for line in requisites_output.decode("utf-8", "replace").splitlines()
                if line
            }
        )
        if not requisites or imported_store_path not in requisites:
            raise BridgeError(
                "nix-store requisites must include the imported store path"
            )
        for requisite in requisites:
            _lstat(requisite, "store requisite")

        for dependency in validate_darwin_binary(
            imported_entrypoint, runner, store_root=store_root
        ):
            if dependency.startswith(str(store_root) + "/"):
                dependency_path = Path(dependency)
                if not dependency_path.exists() or not any(
                    str(dependency_path).startswith(str(item) + "/")
                    or dependency_path == item
                    for item in requisites
                ):
                    raise BridgeError(
                        f"referenced Mach-O store path is absent from closure: {dependency}"
                    )
                _require_regular_file(dependency_path, "referenced Mach-O store path")

        post_import_smoke_sha256 = _run_post_import_smoke(
            imported_entrypoint,
            evidence,
            runner,
        )

        _write_closure_export(
            runner,
            ["nix-store", "--export", *map(str, requisites)],
            closure_export,
        )

    _copy_replay_evidence(evidence, record_path.parent)
    record = {
        "schema_version": 1,
        "kind": "prompt-toon-gf-darwin-nix-bridge",
        "gf": {
            "authority": evidence.proof["authority"],
            "proof_result_filename": DARWIN_GF_PROOF_FILENAME,
            "proof_result_sha256": evidence.proof_result_sha256,
            "exported_outputs_filename": DARWIN_GF_EXPORTS_FILENAME,
            "exported_outputs_sha256": evidence.exported_outputs_sha256,
            "attestation": {
                "bundle_filename": DARWIN_GF_ATTESTATION_FILENAME,
                "bundle_sha256": evidence.attestation_bundle_sha256,
                "predicate_type": SLSA_PROVENANCE_TYPE,
                "repository": GF_AUTHORITY,
                "signer_workflow": GF_SIGNER_WORKFLOW,
                "source_ref": GF_SOURCE_REF,
                "source_digest": evidence.gf_source_digest,
            },
            "request": {
                key: evidence.proof["request"][key]
                for key in (
                    "workflow_run_id",
                    "workflow_run_attempt",
                    "workflow_run_url",
                    "consumer_repository",
                    "consumer_ref",
                    "target",
                    "target_platform",
                    "worker_closure_digest",
                    "bazel_command",
                )
            },
            "platform": evidence.proof["platform"],
            "dispatch_cell_image_digest": evidence.proof[
                "dispatch_cell_image_digest"
            ],
            "worker_identity": evidence.proof["worker_identity"],
            "worker_execution_evidence": evidence.proof[
                "worker_execution_evidence"
            ],
        },
        "exported_output": {
            "label": evidence.output.label,
            "path": evidence.output.claim_path,
            "sha256": f"sha256:{evidence.output.sha256}",
            "size_bytes": evidence.output.size,
            "executable": evidence.output.executable,
            "transfer_filename": entrypoint_export.name,
        },
        "native_smoke": {
            "evidence_filename": DARWIN_NATIVE_SMOKE_FILENAME,
            "evidence_sha256": evidence.native_smoke_output.sha256,
            "result": evidence.native_smoke,
        },
        "nix": {
            "store_path": str(imported_store_path),
            "entrypoint": {
                "path": "bin/ptoon",
                "store_path": str(imported_entrypoint),
                "sha256": imported_sha256,
            },
            "closure_export": {
                "filename": closure_export.name,
                "sha256": sha256_file(closure_export),
                "size_bytes": closure_export.stat().st_size,
            },
            "store_requisites": [str(path) for path in requisites],
            "post_import_smoke": {
                "script": NATIVE_SMOKE_SCRIPT_REPO_PATH,
                "script_sha256": sha256_file(NATIVE_SMOKE_SCRIPT),
                "expected": NATIVE_SMOKE_EXPECTED_REPO_PATH,
                "expected_sha256": sha256_file(NATIVE_SMOKE_EXPECTED),
                "result_sha256": post_import_smoke_sha256,
                "byte_identical_to_remote": True,
            },
        },
    }
    _write_json(record_path, record)
    return record


def replay_bridge_bundle(
    *,
    record_path: Path,
    closure_export: Path,
    entrypoint_export: Path,
    expected_revision: str,
    expected_gf_revision: str,
    runner: CommandRunner = _default_runner,
    store_root: Path = Path("/nix/store"),
) -> dict[str, Any]:
    """Replay provenance, native inspection, and semantics before release."""

    validate_bridge_record(
        record_path,
        expected_revision=expected_revision,
        closure_export=closure_export,
        entrypoint_export=entrypoint_export,
        store_root=store_root,
    )
    record = _read_json(record_path, "GF Darwin bridge record")
    gf = record["gf"]
    if gf["attestation"]["source_digest"] != expected_gf_revision:
        raise BridgeError("GF Darwin bridge source commit mismatch")

    replay_exports_path = record_path.parent / DARWIN_GF_EXPORTS_FILENAME
    replay_exports = _read_json(
        replay_exports_path, "replayed GF exported-output index"
    )
    claims = _validated_output_claims(replay_exports)
    smoke_claims = [
        claim
        for claim in claims
        if claim["label"] == record["exported_output"]["label"]
        and claim["sha256"]
        == f"sha256:{record['native_smoke']['evidence_sha256']}"
        and claim["executable"] is False
    ]
    if len(smoke_claims) != 1:
        raise BridgeError(
            "replayed exports do not identify exactly one native smoke output"
        )
    smoke_claim = smoke_claims[0]

    with tempfile.TemporaryDirectory(
        prefix="prompt-toon-gf-replay-"
    ) as temporary:
        evidence_dir = Path(temporary)
        (evidence_dir / "exported-outputs").mkdir()
        shutil.copyfile(
            record_path.parent / DARWIN_GF_PROOF_FILENAME,
            evidence_dir / GF_PROOF_FILENAME,
        )
        shutil.copyfile(replay_exports_path, evidence_dir / GF_EXPORTS_FILENAME)
        shutil.copyfile(
            record_path.parent / DARWIN_GF_ATTESTATION_FILENAME,
            evidence_dir / GF_ATTESTATION_FILENAME,
        )
        binary_claim_path = _validated_claim_path(
            record["exported_output"]["path"], "bridge exported output path"
        )
        binary_path = evidence_dir.joinpath(*binary_claim_path.parts)
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entrypoint_export, binary_path)
        os.chmod(binary_path, 0o755)
        smoke_claim_path = _validated_claim_path(
            smoke_claim["path"], "bridge native smoke output path"
        )
        smoke_path = evidence_dir.joinpath(*smoke_claim_path.parts)
        smoke_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            record_path.parent / DARWIN_NATIVE_SMOKE_FILENAME,
            smoke_path,
        )
        os.chmod(smoke_path, 0o644)

        evidence = verify_evidence(
            evidence_dir,
            f"{record['exported_output']['label']}={binary_claim_path.as_posix()}",
            f"{smoke_claim['label']}={smoke_claim_path.as_posix()}",
            expected_revision=expected_revision,
            expected_gf_revision=expected_gf_revision,
            runner=runner,
        )
        proof_request = evidence.proof["request"]
        if (
            gf["dispatch_cell_image_digest"]
            != evidence.proof["dispatch_cell_image_digest"]
            or gf["worker_identity"] != evidence.proof["worker_identity"]
            or gf["worker_execution_evidence"]
            != evidence.proof["worker_execution_evidence"]
            or gf["request"]
            != {
                key: proof_request[key]
                for key in (
                    "workflow_run_id",
                    "workflow_run_attempt",
                    "workflow_run_url",
                    "consumer_repository",
                    "consumer_ref",
                    "target",
                    "target_platform",
                    "worker_closure_digest",
                    "bazel_command",
                )
            }
        ):
            raise BridgeError("GF Darwin bridge record differs from signed proof")

        validate_darwin_binary(entrypoint_export, runner, store_root=store_root)
        transfer_smoke_sha256 = _run_post_import_smoke(
            entrypoint_export,
            evidence,
            runner,
        )

        replay_store_root = _canonical_replay_store_root(
            evidence_dir / "nix-replay-root"
        )
        imported_paths = sorted(
            {
                str(_store_path(path, store_root))
                for path in _import_closure_archive(
                    runner,
                    closure_export,
                    replay_store_root,
                )
            }
        )
        recorded_requisites = record["nix"]["store_requisites"]
        if imported_paths != recorded_requisites:
            raise BridgeError(
                "clean-store Nix import path set differs from recorded requisites"
            )
        queried_paths = sorted(
            {
                str(_store_path(path, store_root))
                for path in _run(
                    runner,
                    [
                        "nix-store",
                        "--store",
                        str(replay_store_root),
                        "--query",
                        "--requisites",
                        record["nix"]["store_path"],
                    ],
                )
                .decode("utf-8", "replace")
                .splitlines()
                if path
            }
        )
        if queried_paths != recorded_requisites:
            raise BridgeError(
                "clean-store Nix closure differs from recorded requisites"
            )

        imported_entrypoint = _physical_replay_store_path(
            Path(record["nix"]["store_path"]),
            replay_store_root=replay_store_root,
            store_root=store_root,
        ) / "bin" / "ptoon"
        _require_regular_file(
            imported_entrypoint,
            "clean-store imported Nix entrypoint",
        )
        if sha256_file(imported_entrypoint) != evidence.output.sha256:
            raise BridgeError("clean-store imported Nix entrypoint digest mismatch")
        dependencies = validate_darwin_binary(
            imported_entrypoint,
            runner,
            store_root=store_root,
        )
        for dependency in dependencies:
            if not dependency.startswith(str(store_root) + "/"):
                continue
            _physical_replay_dependency(
                dependency,
                recorded_requisites=recorded_requisites,
                replay_store_root=replay_store_root,
                store_root=store_root,
            )
        store_smoke_sha256 = _run_clean_store_smoke(
            imported_entrypoint,
            evidence,
            runner,
            dependencies=dependencies,
            recorded_requisites=recorded_requisites,
            replay_store_root=replay_store_root,
            store_root=store_root,
        )

    return {
        "schema_version": 1,
        "kind": "prompt-toon-gf-darwin-release-replay",
        "consumer_revision": expected_revision,
        "gf_revision": expected_gf_revision,
        "artifact_sha256": evidence.output.sha256,
        "attestation_bundle_sha256": evidence.attestation_bundle_sha256,
        "transfer_smoke_sha256": transfer_smoke_sha256,
        "store_smoke_sha256": store_smoke_sha256,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser("import", help="verify and import GF evidence")
    importer.add_argument("--evidence-dir", type=Path, required=True)
    importer.add_argument("--output", required=True, metavar="LABEL=PATH")
    importer.add_argument("--native-smoke", required=True, metavar="LABEL=PATH")
    importer.add_argument("--expected-revision", required=True, metavar="GIT_SHA")
    importer.add_argument(
        "--expected-gf-revision",
        required=True,
        metavar="GF_GIT_SHA",
    )
    importer.add_argument("--verify-only", action="store_true")
    importer.add_argument("--closure-export", type=Path)
    importer.add_argument("--entrypoint-export", type=Path)
    importer.add_argument("--record", type=Path)

    replay = commands.add_parser("replay", help="replay a complete bridge bundle")
    replay.add_argument("--record", type=Path, required=True)
    replay.add_argument("--closure-export", type=Path, required=True)
    replay.add_argument("--entrypoint-export", type=Path, required=True)
    replay.add_argument("--expected-revision", required=True, metavar="GIT_SHA")
    replay.add_argument(
        "--expected-gf-revision",
        required=True,
        metavar="GF_GIT_SHA",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "replay":
            if sys.platform != "darwin":
                raise BridgeError("Darwin is required for bridge replay")
            replay = replay_bridge_bundle(
                record_path=args.record,
                closure_export=args.closure_export,
                entrypoint_export=args.entrypoint_export,
                expected_revision=args.expected_revision,
                expected_gf_revision=args.expected_gf_revision,
            )
            print(json.dumps(replay, sort_keys=True, separators=(",", ":")))
            return 0

        evidence = verify_evidence(
            args.evidence_dir,
            args.output,
            args.native_smoke,
            expected_revision=args.expected_revision,
            expected_gf_revision=args.expected_gf_revision,
        )
        if args.verify_only:
            if args.closure_export or args.entrypoint_export or args.record:
                raise BridgeError(
                    "--verify-only cannot be combined with import outputs"
                )
            print(
                json.dumps(
                    {
                        "output_sha256": evidence.output.sha256,
                        "proof_result_sha256": evidence.proof_result_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if sys.platform != "darwin":
            raise BridgeError(
                "Darwin is required for native validation and Nix import; use --verify-only elsewhere"
            )
        if (
            args.closure_export is None
            or args.entrypoint_export is None
            or args.record is None
        ):
            raise BridgeError(
                "--closure-export, --entrypoint-export, and --record are required "
                "for import"
            )
        import_verified_output(
            evidence,
            closure_export=args.closure_export,
            entrypoint_export=args.entrypoint_export,
            record_path=args.record,
        )
    except BridgeError as exc:
        raise SystemExit(f"gf Darwin bridge failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
