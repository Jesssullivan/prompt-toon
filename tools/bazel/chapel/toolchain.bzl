"""Toolchain definition shared by the in-repo Chapel rules.

The compiler is an executable Bazel dependency, not an action-time PATH lookup.
Consumers may attach additional runtime files and environment variables when a
packaged compiler needs them. The Linux bridge is non-hermetic. Darwin is also
explicitly non-hermetic because its compiler is worker-provisioned rather than
a declared Bazel input, but an action-keyed execution-platform policy can bind
it to the reviewed Nix closure. Cache eligibility remains separate.
"""

ChapelToolchainInfo = provider(
    doc = "Inputs and invocation metadata for one Chapel compiler toolchain.",
    fields = {
        "chpl": "FilesToRunProvider for the chpl executable.",
        "env": "Environment passed only to Chapel compile actions.",
        "files": "Transitive compiler/runtime files required by the action.",
        "hermetic": "Whether all non-system compiler inputs are declared to Bazel.",
        "worker_closure_bound": "Whether an action-keyed worker policy selects the compiler closure.",
        "inherit_default_shell_env": "Whether the compile action inherits Bazel's default shell environment.",
        "cacheable": "Whether all compiler identity is represented in the action key.",
        "identity": "Reviewed compiler/toolchain identity for diagnostics.",
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
    attrs = {
        "chpl": attr.label(
            cfg = "exec",
            executable = True,
            mandatory = True,
            doc = "Declared chpl executable for the selected execution platform.",
        ),
        "env": attr.string_dict(
            doc = "Compiler-specific environment; do not place credentials here.",
        ),
        "hermetic": attr.bool(
            default = False,
            doc = "True only when all non-system compiler inputs are declared to Bazel.",
        ),
        "cacheable": attr.bool(
            default = False,
            doc = "True only when compiler closure identity participates in the action key.",
        ),
        "worker_closure_bound": attr.bool(
            default = False,
            doc = "True only for an action-keyed, worker-owned compiler closure.",
        ),
        "inherit_default_shell_env": attr.bool(
            default = False,
            doc = "Whether Bazel's default shell environment reaches the compile action.",
        ),
        "identity": attr.string(
            mandatory = True,
            doc = "Stable compiler/toolchain identity recorded in action diagnostics.",
        ),
        "runtime_files": attr.label_list(
            allow_files = True,
            cfg = "exec",
            doc = "Declared compiler closure files not already carried by chpl runfiles.",
        ),
    },
)
