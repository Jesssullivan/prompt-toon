"""Declare the Chapel toolchain rule and provider for the in-repo Chapel rules.

Public API:

- `ChapelToolchainInfo`: the provider that one Chapel toolchain returns.
- `chapel_toolchain`: the rule that declares one Chapel compiler toolchain.

The compiler is an executable Bazel dependency, a wrapper target. Bazel never
runs a bare `chpl` from the host PATH. Each registered wrapper still resolves
`chpl` on its executor PATH. A packaged compiler can attach extra runtime files and environment
variables. The Linux bridge is non-hermetic. Darwin is non-hermetic too,
because the worker provisions its compiler and Bazel does not declare it as an
input. An action-keyed policy on the execution platform can still bind it to
the reviewed Nix closure. Cache eligibility is a separate flag.
"""

ChapelToolchainInfo = provider(
    doc = "Inputs and invocation metadata for one Chapel compiler toolchain.",
    fields = {
        "chpl": "`FilesToRunProvider` for the `chpl` executable.",
        "env": "Environment passed only to Chapel compile actions.",
        "files": "Transitive compiler and runtime files that the action needs.",
        "hermetic": "Whether every non-system compiler input is declared to Bazel.",
        "worker_closure_bound": "Whether an action-keyed worker policy selects the compiler closure.",
        "inherit_default_shell_env": "Whether the Bazel default shell environment reaches the compile action.",
        "cacheable": "Whether the action key covers the full compiler identity.",
        "identity": "Reviewed identity of the compiler and toolchain, used in diagnostics.",
    },
)

def _chapel_toolchain_impl(ctx):
    runtime_sets = [ctx.attr.chpl[DefaultInfo].files]
    for target in ctx.attr.runtime_files:
        default_info = target[DefaultInfo]
        runtime_sets.extend([
            default_info.files,
            default_info.default_runfiles.files,
            default_info.data_runfiles.files,
        ])

    info = ChapelToolchainInfo(
        chpl = ctx.attr.chpl[DefaultInfo].files_to_run,
        env = dict(ctx.attr.env),
        files = depset(transitive = runtime_sets),
        hermetic = ctx.attr.hermetic,
        worker_closure_bound = ctx.attr.worker_closure_bound,
        inherit_default_shell_env = ctx.attr.inherit_default_shell_env,
        cacheable = ctx.attr.cacheable,
        identity = ctx.attr.identity,
    )
    return [platform_common.ToolchainInfo(chapel = info)]

chapel_toolchain = rule(
    implementation = _chapel_toolchain_impl,
    doc = """Declare one Chapel compiler toolchain for `//tools/bazel/chapel:toolchain_type`.

The rule returns `platform_common.ToolchainInfo` whose `chapel` field holds a
`ChapelToolchainInfo`. Pair each target with a `toolchain()` target that sets
its execution and target constraints. The flags describe the compiler only.
`chapel_binary` reads three of them. `cacheable` chooses `no-cache`.
`inherit_default_shell_env` chooses the shell environment.
`worker_closure_bound` enforces `require_worker_closure_toolchain`. Nothing
reads `hermetic`. It is informational.
""",
    attrs = {
        "chpl": attr.label(
            cfg = "exec",
            executable = True,
            mandatory = True,
            doc = "Declared `chpl` executable for the selected execution platform.",
        ),
        "env": attr.string_dict(
            doc = "Environment for the compiler only. Never put credentials here.",
        ),
        "hermetic": attr.bool(
            default = False,
            doc = "Set True only when every non-system compiler input is declared to Bazel.",
        ),
        "cacheable": attr.bool(
            default = False,
            doc = "Set True only when the action key covers the identity of the compiler closure.",
        ),
        "worker_closure_bound": attr.bool(
            default = False,
            doc = "Set True only for an action-keyed compiler closure that the worker owns.",
        ),
        "inherit_default_shell_env": attr.bool(
            default = False,
            doc = "Whether the Bazel default shell environment reaches the compile action.",
        ),
        "identity": attr.string(
            mandatory = True,
            doc = "Stable identity of the compiler and toolchain, recorded in action diagnostics.",
        ),
        "runtime_files": attr.label_list(
            allow_files = True,
            cfg = "exec",
            doc = "Declared compiler closure files that the `chpl` runfiles do not already carry.",
        ),
    },
)
