"""Toolchain definition shared by the in-repo Chapel rules.

The compiler is an executable Bazel dependency, not an action-time PATH lookup.
Consumers may attach additional runtime files and environment variables when a
packaged compiler needs them.  The temporary Linux bridge is marked
non-hermetic; Darwin production targets require a hermetic toolchain.
"""

ChapelToolchainInfo = provider(
    doc = "Inputs and invocation metadata for one Chapel compiler toolchain.",
    fields = {
        "chpl": "FilesToRunProvider for the chpl executable.",
        "env": "Environment passed only to Chapel compile actions.",
        "files": "Transitive compiler/runtime files required by the action.",
        "hermetic": "Whether every non-system compiler input is declared.",
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
            default = True,
            doc = "False only for an explicitly transitional host-runtime bridge.",
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
