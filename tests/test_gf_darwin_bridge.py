from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.packaging.import_gf_ptoon import (
    BridgeError,
    CAPS_SMOKE_SHA256,
    DARWIN_PLATFORM,
    DARWIN_TARGET_PLATFORM,
    DARWIN_GF_ATTESTATION_FILENAME,
    DARWIN_GF_EXPORTS_FILENAME,
    DARWIN_GF_PROOF_FILENAME,
    DARWIN_NATIVE_SMOKE_FILENAME,
    DARWIN_WORKER_HOST,
    DARWIN_WORKER_NAME,
    GF_ATTESTATION_FILENAME,
    GF_AUTHORITY,
    GF_SIGNER_WORKFLOW,
    GF_SOURCE_REF,
    NATIVE_SMOKE_EXPECTED_REPO_PATH,
    NATIVE_SMOKE_SCRIPT,
    NATIVE_SMOKE_SCRIPT_REPO_PATH,
    NORMALIZE_SMOKE_SHA256,
    ONE_SHOT_SMOKE_SHA256,
    REDACTION_SMOKE_SHA256,
    RESIDENT_SMOKE_SHA256,
    _import_closure_archive,
    import_verified_output,
    replay_bridge_bundle,
    sha256_file,
    verify_evidence,
)


LABEL = "//src/ptoon:ptoon"
CLAIM_PATH = "exported-outputs/darwin_arm64-fastbuild/bin/src/ptoon/ptoon"
SMOKE_CLAIM_PATH = (
    "exported-outputs/darwin_arm64-fastbuild/bin/src/ptoon/ptoon.native-smoke.json"
)
REVISION = "a" * 40
GF_REVISION = "f" * 40
DISPATCH_CELL_DIGEST = "sha256:" + "9" * 64
WORKER_CLOSURE_DIGEST = "sha256:" + "a" * 64


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def proof(
    outputs: list[dict[str, object]],
    exported_outputs_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "kind": "gf-reapi-proof-result",
        "authority": GF_AUTHORITY,
        "dispatch_cell_image_digest": DISPATCH_CELL_DIGEST,
        "worker_image_digest": DISPATCH_CELL_DIGEST,
        "platform": DARWIN_PLATFORM,
        "countable_remote_execution": True,
        "force_execution": True,
        "build_success": True,
        "proof_exit_status": 0,
        "cache_hits_only": False,
        "remote_processes": 1,
        "executor_attached": True,
        "cache_attached": True,
        "executor": "grpcs://darwin-rbe.example.internal:8980",
        "remote_cache": "grpcs://darwin-rbe.example.internal:8980",
        "worker_remote_execution_log": True,
        "kubectl_capture_bounded": False,
        "worker_identity": {
            "kind": "physical-darwin-worker",
            "name": DARWIN_WORKER_NAME,
            "host": DARWIN_WORKER_HOST,
            "architecture": "arm64",
            "os": "darwin",
            "closure_digest": WORKER_CLOSURE_DIGEST,
        },
        "worker_execution_evidence": {
            "schema_version": 3,
            "evidence_sha256": "sha256:" + "1" * 64,
            "remote_grpc_log_sha256": "sha256:" + "2" * 64,
            "execution_log_sha256": "sha256:" + "3" * 64,
            "bep_sha256": "sha256:" + "4" * 64,
            "eligibility_manifest_sha256": "sha256:" + "5" * 64,
            "exported_outputs_manifest_sha256": (
                f"sha256:{exported_outputs_sha256}"
            ),
            "target": LABEL,
            "tool_invocation_id": "fixture-invocation",
            "remote_execution_count": 1,
        },
        "exported_outputs": outputs,
        "request": {
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "workflow_run_url": (
                "https://github.com/tinyland-inc/GloriousFlywheel/actions/runs/123"
            ),
            "consumer_repository": "Jesssullivan/prompt-toon",
            "consumer_ref": REVISION,
            "target": LABEL,
            "target_platform": DARWIN_TARGET_PLATFORM,
            "worker_closure_digest": WORKER_CLOSURE_DIGEST,
            "bazel_command": "build",
        },
    }


def evidence_fixture(
    root: Path, *, include_metadata: bool = False
) -> tuple[Path, Path]:
    evidence = root / "evidence"
    executable = evidence / CLAIM_PATH
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"verified ptoon binary\n")
    executable.chmod(0o755)
    smoke_path = evidence / SMOKE_CLAIM_PATH
    smoke_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "prompt-toon-darwin-native-smoke",
                "artifact_sha256": f"sha256:{sha256_file(executable)}",
                "caps_engine": "chapel",
                "caps_serve_protocol": 1,
                "caps_sha256": CAPS_SMOKE_SHA256,
                "normalize_sha256": NORMALIZE_SMOKE_SHA256,
                "one_shot_sha256": ONE_SHOT_SMOKE_SHA256,
                "redaction_canary_absent": True,
                "redaction_sha256": REDACTION_SMOKE_SHA256,
                "resident_round_trip_sha256": RESIDENT_SMOKE_SHA256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    smoke_path.chmod(0o644)
    claims: list[dict[str, object]] = [
        {
            "label": LABEL,
            "path": CLAIM_PATH,
            "sha256": f"sha256:{sha256_file(executable)}",
            "size_bytes": executable.stat().st_size,
            "executable": True,
        },
        {
            "label": LABEL,
            "path": SMOKE_CLAIM_PATH,
            "sha256": f"sha256:{sha256_file(smoke_path)}",
            "size_bytes": smoke_path.stat().st_size,
            "executable": False,
        },
    ]
    if include_metadata:
        metadata = executable.with_name("ptoon.txt")
        metadata.write_bytes(b"metadata\n")
        metadata.chmod(0o644)
        claims.append(
            {
                "label": LABEL,
                "path": CLAIM_PATH + ".txt",
                "sha256": f"sha256:{sha256_file(metadata)}",
                "size_bytes": metadata.stat().st_size,
                "executable": False,
            }
        )
    claims.sort(key=lambda claim: (str(claim["label"]), str(claim["path"])))
    exports_path = evidence / "exported-outputs.json"
    write_json(
        exports_path,
        {
            "schema": 1,
            "kind": "gf-reapi-exported-outputs",
            "outputs": claims,
        },
    )
    write_json(
        evidence / "proof-result.json",
        proof(claims, sha256_file(exports_path)),
    )
    (evidence / GF_ATTESTATION_FILENAME).write_text(
        '{"fixture":"sigstore-bundle"}\n',
        encoding="utf-8",
    )
    return evidence, executable


class FakeAttestation:
    def __init__(self, source_digest: str = GF_REVISION) -> None:
        self.source_digest = source_digest

    def __call__(self, command: object):
        from subprocess import CompletedProcess

        command = list(command)  # type: ignore[arg-type]
        if command[:3] != ["gh", "attestation", "verify"]:
            raise AssertionError(command)
        expected_flags = {
            "--repo": GF_AUTHORITY,
            "--signer-workflow": GF_SIGNER_WORKFLOW,
            "--source-ref": GF_SOURCE_REF,
            "--source-digest": self.source_digest,
            "--format": "json",
        }
        for flag, expected in expected_flags.items():
            if flag not in command or command[command.index(flag) + 1] != expected:
                return CompletedProcess(
                    command,
                    1,
                    b"",
                    b"certificate source digest mismatch",
                )
        proof_path = Path(command[3])
        self.command = command
        payload = [
            {
                "verificationResult": {
                    "statement": {
                        "subject": [
                            {
                                "name": proof_path.name,
                                "digest": {"sha256": sha256_file(proof_path)},
                            }
                        ],
                        "predicateType": "https://slsa.dev/provenance/v1",
                        "predicate": {
                            "buildDefinition": {
                                "externalParameters": {
                                    "workflow": {
                                        "repository": (
                                            "https://github.com/"
                                            "tinyland-inc/GloriousFlywheel"
                                        ),
                                        "ref": GF_SOURCE_REF,
                                        "path": (
                                            "/.github/workflows/"
                                            "gf-reapi-cell-proof.yml"
                                        ),
                                    }
                                },
                                "resolvedDependencies": [
                                    {
                                        "uri": (
                                            "git+https://github.com/"
                                            f"{GF_AUTHORITY}@{GF_SOURCE_REF}"
                                        ),
                                        "digest": {"gitCommit": self.source_digest},
                                    }
                                ],
                            }
                        },
                    }
                }
            }
        ]
        return CompletedProcess(command, 0, json.dumps(payload).encode(), b"")


def verify_fixture(
    evidence_dir: Path,
    *,
    revision: str = REVISION,
    gf_revision: str = GF_REVISION,
    runner: object | None = None,
):
    return verify_evidence(
        evidence_dir,
        f"{LABEL}={CLAIM_PATH}",
        f"{LABEL}={SMOKE_CLAIM_PATH}",
        expected_revision=revision,
        expected_gf_revision=gf_revision,
        runner=runner or FakeAttestation(),  # type: ignore[arg-type]
    )


def refresh_output_claim(evidence_dir: Path, claim_path: str) -> None:
    output = evidence_dir / claim_path
    exports_path = evidence_dir / "exported-outputs.json"
    proof_path = evidence_dir / "proof-result.json"
    exports = json.loads(exports_path.read_text(encoding="utf-8"))
    claim = next(item for item in exports["outputs"] if item["path"] == claim_path)
    claim["sha256"] = f"sha256:{sha256_file(output)}"
    claim["size_bytes"] = output.stat().st_size
    write_json(exports_path, exports)
    proof_value = json.loads(proof_path.read_text(encoding="utf-8"))
    proof_value["exported_outputs"] = exports["outputs"]
    proof_value["worker_execution_evidence"][
        "exported_outputs_manifest_sha256"
    ] = f"sha256:{sha256_file(exports_path)}"
    write_json(proof_path, proof_value)


class FakeNixDarwin:
    closure_bytes = b"fake complete closure\n"

    def __init__(self, store_root: Path) -> None:
        self.store_root = store_root
        self.imported = store_root / ("a" * 32 + "-ptoon")
        self.dependency = store_root / ("b" * 32 + "-libptoon")
        self.commands: list[list[str]] = []
        self.local_store_roots: list[Path] = []
        self.clean_store_commands: list[list[str]] = []
        library = self.dependency / "lib" / "libptoon.dylib"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"fake dylib")

    def _write_smoke(self, binary: Path, result_path: Path) -> None:
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "prompt-toon-darwin-native-smoke",
                    "artifact_sha256": f"sha256:{sha256_file(binary)}",
                    "caps_engine": "chapel",
                    "caps_serve_protocol": 1,
                    "caps_sha256": CAPS_SMOKE_SHA256,
                    "normalize_sha256": NORMALIZE_SMOKE_SHA256,
                    "one_shot_sha256": ONE_SHOT_SMOKE_SHA256,
                    "redaction_canary_absent": True,
                    "redaction_sha256": REDACTION_SMOKE_SHA256,
                    "resident_round_trip_sha256": RESIDENT_SMOKE_SHA256,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

    def __call__(self, command: object):
        from subprocess import CompletedProcess

        command = list(command)  # type: ignore[arg-type]
        self.commands.append(command)
        if command[:2] == ["/usr/bin/lipo", "-archs"]:
            return CompletedProcess(command, 0, b"arm64\n", b"")
        if command[:3] == ["/usr/bin/codesign", "--verify", "--strict"]:
            return CompletedProcess(command, 0, b"", b"")
        if command[:3] == ["/usr/bin/codesign", "--display", "--verbose=4"]:
            return CompletedProcess(
                command, 0, b"", b"Signature=adhoc\nTeamIdentifier=not set\n"
            )
        if command[:2] == ["/usr/bin/otool", "-L"]:
            dependencies = [
                "\t/usr/lib/libSystem.B.dylib "
                "(compatibility version 1.0.0, current version 1.0.0)"
            ]
            if Path(command[-1]).name != "libptoon.dylib":
                dependencies.append(
                    f"\t{self.dependency}/lib/libptoon.dylib "
                    "(compatibility version 1.0.0, current version 1.0.0)"
                )
            payload = (f"{command[-1]}:\n" + "\n".join(dependencies) + "\n").encode()
            return CompletedProcess(command, 0, payload, b"")
        if command[:2] == ["/usr/bin/otool", "-l"]:
            return CompletedProcess(
                command,
                0,
                b"Load command 0\n      cmd LC_RPATH\n     path @loader_path/../lib (offset 12)\n",
                b"",
            )
        if command and Path(command[0]) == NATIVE_SMOKE_SCRIPT:
            binary = Path(command[1])
            result_path = Path(command[2])
            self._write_smoke(binary, result_path)
            return CompletedProcess(command, 0, b"", b"")
        if command[:2] == ["/usr/bin/sandbox-exec", "-p"]:
            self.clean_store_commands.append(command)
            profile = command[2]
            wrapper = Path(command[4])
            binary = Path(command[5])
            result_path = Path(command[6])
            physical_store = self.local_store_roots[-1].joinpath(
                *self.store_root.parts[1:]
            )
            physical_library = (
                physical_store / self.dependency.name / "lib" / "libptoon.dylib"
            )
            wrapper_text = wrapper.read_text(encoding="utf-8")
            copied_smoke = wrapper.parent / NATIVE_SMOKE_SCRIPT.name
            if (
                f'(subpath "{self.store_root}")' not in profile
                or str(physical_library.parent) not in wrapper_text
                or "export DYLD_LIBRARY_PATH=" not in wrapper_text
                or str(copied_smoke) not in wrapper_text
                or str(NATIVE_SMOKE_SCRIPT) in wrapper_text
                or not copied_smoke.is_file()
                or sha256_file(copied_smoke) != sha256_file(NATIVE_SMOKE_SCRIPT)
            ):
                return CompletedProcess(
                    command, 1, b"", b"clean-store sandbox contract mismatch"
                )
            self._write_smoke(binary, result_path)
            return CompletedProcess(command, 0, b"", b"")
        if command[:3] == ["nix", "store", "add"]:
            shutil.copytree(Path(command[3]), self.imported)
            return CompletedProcess(command, 0, f"{self.imported}\n".encode(), b"")
        if command[:2] == ["nix-store", "--add-root"] and "--realise" in command:
            gc_root = Path(command[command.index("--add-root") + 1])
            gc_root.symlink_to(self.imported)
            return CompletedProcess(command, 0, f"{self.imported}\n".encode(), b"")
        if command[:3] == ["nix-store", "--query", "--requisites"]:
            return CompletedProcess(
                command, 0, f"{self.imported}\n{self.dependency}\n".encode(), b""
            )
        if command[:2] == ["nix-store", "--export"]:
            return CompletedProcess(command, 0, self.closure_bytes, b"")
        if (
            command[:2] == ["nix-store", "--store"]
            and "--import" in command
            and "--archive" in command
        ):
            archive = Path(command[command.index("--archive") + 1])
            if archive.read_bytes() != self.closure_bytes:
                return CompletedProcess(command, 1, b"", b"invalid closure archive")
            replay_root = Path(command[2])
            self.local_store_roots.append(replay_root)
            physical_store = replay_root.joinpath(*self.store_root.parts[1:])
            shutil.copytree(
                self.imported,
                physical_store / self.imported.name,
            )
            shutil.copytree(
                self.dependency,
                physical_store / self.dependency.name,
            )
            return CompletedProcess(
                command,
                0,
                f"{self.imported}\n{self.dependency}\n".encode(),
                b"",
            )
        if (
            command[:2] == ["nix-store", "--store"]
            and "--query" in command
            and "--requisites" in command
        ):
            self.local_store_roots.append(Path(command[2]))
            return CompletedProcess(
                command, 0, f"{self.imported}\n{self.dependency}\n".encode(), b""
            )
        raise AssertionError(command)


class FakeReplay:
    def __init__(self, darwin: FakeNixDarwin) -> None:
        self.darwin = darwin
        self.attestation = FakeAttestation()

    def __call__(self, command: object):
        if list(command)[:3] == ["gh", "attestation", "verify"]:  # type: ignore[arg-type]
            return self.attestation(command)
        return self.darwin(command)


class GfDarwinBridgeTests(unittest.TestCase):
    def test_verify_only_contract_accepts_countable_bound_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir, executable = evidence_fixture(Path(temporary))
            expected_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
            attestation = FakeAttestation()
            evidence = verify_fixture(evidence_dir, runner=attestation)
        self.assertEqual(evidence.output.sha256, expected_sha256)
        self.assertEqual(evidence.proof["platform"], DARWIN_PLATFORM)
        self.assertEqual(evidence.gf_source_digest, GF_REVISION)
        self.assertEqual(
            attestation.command[attestation.command.index("--source-digest") + 1],
            GF_REVISION,
        )

    @unittest.skipUnless(
        sys.platform == "darwin",
        "native smoke script requires the Darwin system toolchain",
    )
    def test_native_smoke_rejects_caps_with_trailing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "ptoon"
            output = root / "native-smoke.json"
            binary.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = caps ]; then\n"
                "  printf '%s' "
                """'{"engine":"chapel","utf8proc":true,"unicode_version":"""
                """"15.1.0","patterns":9,"serve_protocol":1,"features":["""
                """"normalize","redact","defang","redact-batch","""
                """"condense-batch","condense","serve"]}trailing'\n"""
                "  exit 0\n"
                "fi\n"
                "exit 99\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            result = subprocess.run(
                [str(NATIVE_SMOKE_SCRIPT), str(binary), str(output)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(result.returncode, 70)
        self.assertIn(
            b"caps bytes differ from the complete expected contract",
            result.stderr,
        )

    def test_rejects_alternate_label_for_canonical_release_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir, _ = evidence_fixture(Path(temporary))
            alternate = "//src/ptoon:other"
            with self.assertRaisesRegex(BridgeError, "not the release target"):
                verify_evidence(
                    evidence_dir,
                    f"{alternate}={CLAIM_PATH}",
                    f"{alternate}={SMOKE_CLAIM_PATH}",
                    expected_revision=REVISION,
                    expected_gf_revision=GF_REVISION,
                    runner=FakeAttestation(),
                )

    def test_clean_store_import_canonicalizes_symlinked_parent(self) -> None:
        from subprocess import CompletedProcess

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_parent = root / "private"
            canonical_parent.mkdir()
            symlink_parent = root / "var"
            symlink_parent.symlink_to(canonical_parent, target_is_directory=True)
            archive = root / "closure.nar"
            archive.write_bytes(b"fixture closure")
            expected_root = (canonical_parent / "replay-store").resolve()

            def runner(command: object):
                command = list(command)  # type: ignore[arg-type]
                self.assertEqual(command[2], str(expected_root))
                self.assertEqual(
                    Path(command[command.index("--archive") + 1]),
                    archive,
                )
                return CompletedProcess(
                    command,
                    0,
                    b"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture\n",
                    b"",
                )

            imported = _import_closure_archive(
                runner,
                archive,
                symlink_parent / "replay-store",
            )
        self.assertEqual(
            imported,
            ["/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-fixture"],
        )

    def test_rejects_uncountable_or_unbound_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            proof_path = evidence_dir / "proof-result.json"
            bad = json.loads(proof_path.read_text(encoding="utf-8"))
            bad["countable_remote_execution"] = False
            write_json(proof_path, bad)
            with self.assertRaisesRegex(BridgeError, "countable_remote_execution"):
                verify_fixture(evidence_dir)

            evidence_dir, _ = evidence_fixture(root / "again")
            proof_path = evidence_dir / "proof-result.json"
            bad = json.loads(proof_path.read_text(encoding="utf-8"))
            bad["exported_outputs"] = []
            write_json(proof_path, bad)
            with self.assertRaisesRegex(BridgeError, "does not match"):
                verify_fixture(evidence_dir)

    def test_rejects_symlinked_and_mismatched_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, executable = evidence_fixture(root)
            executable.unlink()
            executable.symlink_to(root / "elsewhere")
            with self.assertRaisesRegex(BridgeError, "symlink"):
                verify_fixture(evidence_dir)

            evidence_dir, executable = evidence_fixture(root / "again")
            executable.write_bytes(b"tampered")
            with self.assertRaisesRegex(BridgeError, "sha256"):
                verify_fixture(evidence_dir)

    def test_rejects_stale_or_misdirected_proof_requests(self) -> None:
        cases = (
            (
                "stale revision",
                "consumer_ref",
                "b" * 40,
                "consumer_ref is stale",
            ),
            (
                "wrong repository",
                "consumer_repository",
                "tinyland-inc/prompt-toon",
                "consumer_repository mismatch",
            ),
            (
                "wrong target",
                "target",
                "//src/ptoon:other",
                "target does not match",
            ),
            (
                "test command",
                "bazel_command",
                "test",
                "bazel_command must be build",
            ),
            (
                "wrong worker closure",
                "worker_closure_digest",
                "sha256:" + "b" * 64,
                "requested worker closure mismatch",
            ),
            (
                "wrong workflow",
                "workflow_run_url",
                "https://github.com/Jesssullivan/prompt-toon/actions/runs/123",
                "must identify its GF workflow run",
            ),
        )
        for name, field, value, expected_error in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                evidence_dir, _ = evidence_fixture(Path(temporary))
                proof_path = evidence_dir / "proof-result.json"
                bad = json.loads(proof_path.read_text(encoding="utf-8"))
                bad["request"][field] = value
                write_json(proof_path, bad)
                with self.assertRaisesRegex(BridgeError, expected_error):
                    verify_fixture(evidence_dir)

    def test_rejects_legacy_or_unbound_worker_execution_proof(self) -> None:
        cases = (
            ("schema_version", 1, "schema_version must be 2"),
            ("schema_version", True, "schema_version must be 2"),
            (
                "exported_outputs_manifest_sha256",
                "sha256:" + "0" * 64,
                "does not match exported-outputs.json",
            ),
            (
                "target",
                "//src/ptoon:other",
                "worker execution binding target mismatch",
            ),
        )
        for field, value, expected_error in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                evidence_dir, _ = evidence_fixture(Path(temporary))
                proof_path = evidence_dir / "proof-result.json"
                bad = json.loads(proof_path.read_text(encoding="utf-8"))
                if field == "schema_version":
                    bad[field] = value
                else:
                    bad["worker_execution_evidence"][field] = value
                write_json(proof_path, bad)
                with self.assertRaisesRegex(BridgeError, expected_error):
                    verify_fixture(evidence_dir)

    def test_verify_rejects_duplicate_keys_in_all_evidence_records(self) -> None:
        cases = (
            ("proof-result.json", '"schema_version":2,', None),
            ("exported-outputs.json", '"schema":1,', None),
            (
                SMOKE_CLAIM_PATH,
                '"schema_version":1,',
                SMOKE_CLAIM_PATH,
            ),
        )
        for filename, duplicate, refresh_claim in cases:
            with (
                self.subTest(filename=filename),
                tempfile.TemporaryDirectory() as temporary,
            ):
                evidence_dir, _ = evidence_fixture(Path(temporary))
                path = evidence_dir / filename
                original = path.read_text(encoding="utf-8")
                path.write_text("{" + duplicate + original[1:], encoding="utf-8")
                if refresh_claim is not None:
                    refresh_output_claim(evidence_dir, refresh_claim)
                with self.assertRaisesRegex(BridgeError, "duplicate JSON key"):
                    verify_fixture(evidence_dir)

    def test_boolean_export_and_native_smoke_schemas_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir, _ = evidence_fixture(Path(temporary))
            exports_path = evidence_dir / "exported-outputs.json"
            exports = json.loads(exports_path.read_text(encoding="utf-8"))
            exports["schema"] = True
            write_json(exports_path, exports)
            with self.assertRaisesRegex(BridgeError, "schema or kind mismatch"):
                verify_fixture(evidence_dir)

        for field in ("schema_version", "caps_serve_protocol"):
            with (
                self.subTest(field=field),
                tempfile.TemporaryDirectory() as temporary,
            ):
                evidence_dir, _ = evidence_fixture(Path(temporary))
                smoke_path = evidence_dir / SMOKE_CLAIM_PATH
                smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
                smoke[field] = True
                write_json(smoke_path, smoke)
                refresh_output_claim(evidence_dir, SMOKE_CLAIM_PATH)
                with self.assertRaisesRegex(
                    BridgeError,
                    "does not bind a passing ptoon run",
                ):
                    verify_fixture(evidence_dir)

    def test_accepts_multiple_files_for_one_label_and_rejects_duplicate_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root, include_metadata=True)
            evidence = verify_fixture(evidence_dir)
            self.assertEqual(evidence.output.label, LABEL)

            exports_path = evidence_dir / "exported-outputs.json"
            proof_path = evidence_dir / "proof-result.json"
            exports = json.loads(exports_path.read_text(encoding="utf-8"))
            duplicate = dict(exports["outputs"][0])
            duplicate["label"] = "//src/ptoon:duplicate-owner"
            exports["outputs"].append(duplicate)
            exports["outputs"].sort(key=lambda item: (item["label"], item["path"]))
            write_json(exports_path, exports)
            proof_value = json.loads(proof_path.read_text(encoding="utf-8"))
            proof_value["exported_outputs"] = exports["outputs"]
            write_json(proof_path, proof_value)
            with self.assertRaisesRegex(BridgeError, "duplicate exported output path"):
                verify_fixture(evidence_dir)

    def test_rejects_unverified_gf_attestation(self) -> None:
        from subprocess import CompletedProcess

        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir, _ = evidence_fixture(Path(temporary))

            def reject_attestation(command: object):
                return CompletedProcess(command, 1, b"", b"verification failed")

            with self.assertRaisesRegex(BridgeError, "verification failed"):
                verify_fixture(evidence_dir, runner=reject_attestation)

    def test_rejects_attested_gf_source_commit_other_than_operator_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir, _ = evidence_fixture(Path(temporary))
            with self.assertRaisesRegex(BridgeError, "source digest mismatch"):
                verify_fixture(evidence_dir, gf_revision="e" * 40)

    def test_import_exports_closure_and_emits_canonical_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, executable = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            closure = root / "out" / "ptoon-aarch64-darwin.nar"
            transferred = root / "out" / "ptoon-aarch64-darwin.bin"
            record_path = root / "out" / "bridge.json"
            record = import_verified_output(
                evidence,
                closure_export=closure,
                entrypoint_export=transferred,
                record_path=record_path,
                runner=fake,
                store_root=store_root,
            )
            commands = list(fake.commands)
            rendered = record_path.read_bytes()
            expected_sha256 = sha256_file(executable)
            transferred_bytes = transferred.read_bytes()
            source_bytes = executable.read_bytes()
            transfer_parent = str(closure.parent)
            replay_files_present = all(
                (record_path.parent / replay_filename).is_file()
                for replay_filename in (
                    DARWIN_GF_PROOF_FILENAME,
                    DARWIN_GF_EXPORTS_FILENAME,
                    DARWIN_GF_ATTESTATION_FILENAME,
                    DARWIN_NATIVE_SMOKE_FILENAME,
                )
            )
        self.assertEqual(record["kind"], "prompt-toon-gf-darwin-nix-bridge")
        self.assertEqual(record["gf"]["request"]["consumer_ref"], REVISION)
        self.assertTrue(
            record["native_smoke"]["result"]["redaction_canary_absent"]
        )
        self.assertEqual(
            record["gf"]["attestation"]["source_digest"],
            GF_REVISION,
        )
        self.assertTrue(replay_files_present)
        self.assertEqual(transferred_bytes, source_bytes)
        self.assertEqual(
            record["exported_output"]["transfer_filename"],
            transferred.name,
        )
        self.assertEqual(record["nix"]["entrypoint"]["sha256"], expected_sha256)
        self.assertEqual(
            record["nix"]["closure_export"]["size_bytes"],
            len(FakeNixDarwin.closure_bytes),
        )
        self.assertNotIn(transfer_parent, json.dumps(record))
        self.assertEqual(
            rendered,
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n",
        )
        self.assertEqual(
            record["nix"]["store_requisites"],
            sorted(record["nix"]["store_requisites"]),
        )
        self.assertEqual(
            record["nix"]["post_import_smoke"]["script"],
            NATIVE_SMOKE_SCRIPT_REPO_PATH,
        )
        self.assertEqual(
            record["nix"]["post_import_smoke"]["expected"],
            NATIVE_SMOKE_EXPECTED_REPO_PATH,
        )
        self.assertTrue(
            record["nix"]["post_import_smoke"]["byte_identical_to_remote"]
        )
        add_index = next(
            index
            for index, command in enumerate(commands)
            if command[:3] == ["nix", "store", "add"]
        )
        root_index = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ["nix-store", "--add-root"]
            and "--realise" in command
        )
        query_index = next(
            index
            for index, command in enumerate(commands)
            if command[:3] == ["nix-store", "--query", "--requisites"]
        )
        export_index = next(
            index
            for index, command in enumerate(commands)
            if command[:2] == ["nix-store", "--export"]
        )
        self.assertLess(add_index, root_index)
        self.assertLess(root_index, query_index)
        self.assertLess(query_index, export_index)
        self.assertIn("--add-root", commands[root_index])
        self.assertIn("--indirect", commands[root_index])

    def test_release_replay_rejects_duplicate_or_boolean_bridge_schema(self) -> None:
        cases = ("duplicate", "boolean")
        for mutation in cases:
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                evidence_dir, _ = evidence_fixture(root)
                evidence = verify_fixture(evidence_dir)
                store_root = root / "nix" / "store"
                fake = FakeNixDarwin(store_root)
                output = root / "out"
                closure = output / "ptoon-aarch64-darwin.nar"
                entrypoint = output / "ptoon-aarch64-darwin.bin"
                record_path = output / "ptoon-aarch64-darwin.gf-nix-bridge.json"
                import_verified_output(
                    evidence,
                    closure_export=closure,
                    entrypoint_export=entrypoint,
                    record_path=record_path,
                    runner=fake,
                    store_root=store_root,
                )
                if mutation == "duplicate":
                    original = record_path.read_text(encoding="utf-8")
                    record_path.write_text(
                        '{"schema_version":1,' + original[1:],
                        encoding="utf-8",
                    )
                    expected_error = "duplicate JSON key"
                else:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    record["schema_version"] = True
                    write_json(record_path, record)
                    expected_error = "schema_version must be 1"

                with self.assertRaisesRegex(BridgeError, expected_error):
                    replay_bridge_bundle(
                        record_path=record_path,
                        closure_export=closure,
                        entrypoint_export=entrypoint,
                        expected_revision=REVISION,
                        expected_gf_revision=GF_REVISION,
                        runner=FakeReplay(fake),
                        store_root=store_root,
                    )

    def test_import_rejects_identity_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            original = fake.__call__

            def identity_signature(command: object):
                result = original(command)
                if list(command)[:3] == [
                    "/usr/bin/codesign",
                    "--display",
                    "--verbose=4",
                ]:  # type: ignore[arg-type]
                    result.stderr += b"Authority=Developer ID Application\n"
                return result

            with self.assertRaisesRegex(BridgeError, "Authority"):
                import_verified_output(
                    evidence,
                    closure_export=root / "closure.nar",
                    entrypoint_export=root / "ptoon.bin",
                    record_path=root / "bridge.json",
                    runner=identity_signature,
                    store_root=store_root,
                )

    def test_release_replay_reverifies_attestation_store_and_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            output = root / "out"
            closure = output / "ptoon-aarch64-darwin.nar"
            entrypoint = output / "ptoon-aarch64-darwin.bin"
            record_path = (
                output / "ptoon-aarch64-darwin.gf-nix-bridge.json"
            )
            import_verified_output(
                evidence,
                closure_export=closure,
                entrypoint_export=entrypoint,
                record_path=record_path,
                runner=fake,
                store_root=store_root,
            )
            replay = replay_bridge_bundle(
                record_path=record_path,
                closure_export=closure,
                entrypoint_export=entrypoint,
                expected_revision=REVISION,
                expected_gf_revision=GF_REVISION,
                runner=FakeReplay(fake),
                store_root=store_root,
            )

        self.assertEqual(
            replay["kind"], "prompt-toon-gf-darwin-release-replay"
        )
        self.assertEqual(replay["consumer_revision"], REVISION)
        self.assertEqual(replay["gf_revision"], GF_REVISION)
        self.assertEqual(
            replay["transfer_smoke_sha256"],
            replay["store_smoke_sha256"],
        )
        self.assertEqual(len(fake.clean_store_commands), 1)
        self.assertIn(
            f'(subpath "{store_root}")',
            fake.clean_store_commands[0][2],
        )

    def test_release_replay_rejects_substituted_closure_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            output = root / "out"
            closure = output / "ptoon-aarch64-darwin.nar"
            entrypoint = output / "ptoon-aarch64-darwin.bin"
            record_path = (
                output / "ptoon-aarch64-darwin.gf-nix-bridge.json"
            )
            import_verified_output(
                evidence,
                closure_export=closure,
                entrypoint_export=entrypoint,
                record_path=record_path,
                runner=fake,
                store_root=store_root,
            )
            closure.write_bytes(b"substituted archive bytes\n")
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["nix"]["closure_export"]["sha256"] = sha256_file(closure)
            record["nix"]["closure_export"]["size_bytes"] = closure.stat().st_size
            write_json(record_path, record)

            with self.assertRaisesRegex(BridgeError, "invalid closure archive"):
                replay_bridge_bundle(
                    record_path=record_path,
                    closure_export=closure,
                    entrypoint_export=entrypoint,
                    expected_revision=REVISION,
                    expected_gf_revision=GF_REVISION,
                    runner=FakeReplay(fake),
                    store_root=store_root,
                )

    def test_release_replay_canonicalizes_every_local_store_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            output = root / "out"
            closure = output / "ptoon-aarch64-darwin.nar"
            entrypoint = output / "ptoon-aarch64-darwin.bin"
            record_path = output / "ptoon-aarch64-darwin.gf-nix-bridge.json"
            import_verified_output(
                evidence,
                closure_export=closure,
                entrypoint_export=entrypoint,
                record_path=record_path,
                runner=fake,
                store_root=store_root,
            )
            canonical_parent = root / "private-var"
            canonical_parent.mkdir()
            symlink_parent = root / "var"
            symlink_parent.symlink_to(
                canonical_parent,
                target_is_directory=True,
            )
            original_tempdir = tempfile.tempdir
            tempfile.tempdir = str(symlink_parent)
            try:
                replay_bridge_bundle(
                    record_path=record_path,
                    closure_export=closure,
                    entrypoint_export=entrypoint,
                    expected_revision=REVISION,
                    expected_gf_revision=GF_REVISION,
                    runner=FakeReplay(fake),
                    store_root=store_root,
                )
            finally:
                tempfile.tempdir = original_tempdir

        self.assertGreaterEqual(len(fake.local_store_roots), 2)
        for local_store_root in fake.local_store_roots:
            self.assertTrue(local_store_root.is_relative_to(canonical_parent.resolve()))
            self.assertNotIn(str(symlink_parent), str(local_store_root))

    def test_release_replay_fails_closed_when_live_store_denial_cannot_run(
        self,
    ) -> None:
        from subprocess import CompletedProcess

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            output = root / "out"
            closure = output / "ptoon-aarch64-darwin.nar"
            entrypoint = output / "ptoon-aarch64-darwin.bin"
            record_path = output / "ptoon-aarch64-darwin.gf-nix-bridge.json"
            import_verified_output(
                evidence,
                closure_export=closure,
                entrypoint_export=entrypoint,
                record_path=record_path,
                runner=fake,
                store_root=store_root,
            )
            replay = FakeReplay(fake)

            def reject_sandbox(command: object):
                if list(command)[:2] == [  # type: ignore[arg-type]
                    "/usr/bin/sandbox-exec",
                    "-p",
                ]:
                    return CompletedProcess(
                        command,
                        1,
                        b"",
                        b"live store denial unavailable",
                    )
                return replay(command)

            with self.assertRaisesRegex(BridgeError, "live store denial unavailable"):
                replay_bridge_bundle(
                    record_path=record_path,
                    closure_export=closure,
                    entrypoint_export=entrypoint,
                    expected_revision=REVISION,
                    expected_gf_revision=GF_REVISION,
                    runner=reject_sandbox,
                    store_root=store_root,
                )

    def test_import_rejects_unresolved_rpath_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            evidence = verify_fixture(evidence_dir)
            store_root = root / "nix" / "store"
            fake = FakeNixDarwin(store_root)
            original = fake.__call__

            def unresolved_dependency(command: object):
                from subprocess import CompletedProcess

                if list(command)[:2] == ["/usr/bin/otool", "-L"]:  # type: ignore[arg-type]
                    payload = (
                        f"{list(command)[-1]}:\n"
                        "\t@rpath/libptoon.dylib "
                        "(compatibility version 1.0.0, current version 1.0.0)\n"
                    ).encode()
                    return CompletedProcess(command, 0, payload, b"")
                return original(command)

            with self.assertRaisesRegex(BridgeError, "unsafe Mach-O dependency"):
                import_verified_output(
                    evidence,
                    closure_export=root / "closure.nar",
                    entrypoint_export=root / "ptoon.bin",
                    record_path=root / "bridge.json",
                    runner=unresolved_dependency,
                    store_root=store_root,
                )

    def test_import_rejects_lexical_macho_path_traversal(self) -> None:
        from subprocess import CompletedProcess

        cases = (
            (
                "absolute dependency",
                ["/usr/bin/otool", "-L"],
                (
                    "fixture:\n"
                    "\t/usr/lib/../../tmp/evil.dylib "
                    "(compatibility version 1.0.0, current version 1.0.0)\n"
                ).encode(),
                "unsafe Mach-O dependency",
            ),
            (
                "loader RPATH",
                ["/usr/bin/otool", "-l"],
                (
                    "Load command 0\n"
                    "      cmd LC_RPATH\n"
                    "     path @loader_path/../../../../tmp (offset 12)\n"
                ).encode(),
                "unsafe Mach-O RPATH",
            ),
        )
        for name, prefix, payload, expected_error in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                evidence_dir, _ = evidence_fixture(root)
                evidence = verify_fixture(evidence_dir)
                store_root = root / "nix" / "store"
                fake = FakeNixDarwin(store_root)
                original = fake.__call__

                def traversal(command: object):
                    if list(command)[:2] == prefix:  # type: ignore[arg-type]
                        return CompletedProcess(command, 0, payload, b"")
                    return original(command)

                with self.assertRaisesRegex(BridgeError, expected_error):
                    import_verified_output(
                        evidence,
                        closure_export=root / "closure.nar",
                        entrypoint_export=root / "ptoon.bin",
                        record_path=root / "bridge.json",
                        runner=traversal,
                        store_root=store_root,
                    )

    def test_verify_rejects_failed_remote_native_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_dir, _ = evidence_fixture(root)
            smoke_path = evidence_dir / SMOKE_CLAIM_PATH
            smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
            smoke["redaction_canary_absent"] = False
            write_json(smoke_path, smoke)
            refresh_output_claim(evidence_dir, SMOKE_CLAIM_PATH)
            with self.assertRaisesRegex(
                BridgeError, "does not bind a passing ptoon run"
            ):
                verify_fixture(evidence_dir)


if __name__ == "__main__":
    unittest.main()
