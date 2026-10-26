"""Compile Chapel binaries through the registered Chapel toolchain.

Public API:

- `chapel_binary`: compile one Chapel entry point into an executable.

This file encodes the GloriousFlywheel contract from TIN-2709 C2, pulled
forward into the C1 lane. Nix owns the toolchain version. Bazel owns the build
graph, the declared compiler boundary and the content-addressed cache. Under
`--config=executor-backed` it also owns remote execution on GF REAPI.

`ChapelToolchainInfo` is the mandatory compiler boundary (TIN-2949). Toolchain
resolution binds both the execution platform and the target platform.

- Linux registers a non-hermetic bridge to the pinned GF executor runtime.
- Darwin resolves only the authenticated GF worker-closure bridge. It never
  falls back to the PATH of the developer host.

Both bridges stay non-hermetic. Neither is cacheable until the action key
covers every compiler input, including the Darwin closure digest. TIN-2710
tracks the extraction of a reusable `rules_chapel`.
"""

_CHAPEL_TOOLCHAIN_TYPE = "//tools/bazel/chapel:toolchain_type"

def _chapel_binary_impl(ctx):
    out = ctx.actions.declare_file(ctx.attr.binary_name or ctx.label.name)

    toolchain = ctx.toolchains[_CHAPEL_TOOLCHAIN_TYPE].chapel
    if (
        ctx.attr.require_worker_closure_toolchain and
        not toolchain.worker_closure_bound
    ):
        fail(
            "%s requires an action-keyed worker-closure Chapel toolchain; selected %s" %
            (ctx.label, toolchain.identity),
        )

    args = ctx.actions.args()
    args.add("--fast")
    args.add(ctx.file.main)
    for m in ctx.attr.module_paths:
        args.add("-M")
        args.add(m)
    args.add("-o")
    args.add(out)

    # Upstream references for this exact shape:
    # - Chapel chpl man page: --fast, -M/--module-dir and -o.
    # - Chapel C interop technote: require paths are relative to the source file.
    # - Bazel actions.run: the tools and the transitive compiler files are
    #   declared inputs. Only the transitional Linux toolchain inherits its
    #   worker env.
    # chpl resolves each file-relative `require "../../c_src/..."` from the
    # directory of the main source. So the whole srcs and data tree must be action
    # inputs, and the action must run from the exec root (the Bazel default cwd).
    ctx.actions.run(
        arguments = [args],
        env = toolchain.env,
        executable = toolchain.chpl,
        execution_requirements = {} if toolchain.cacheable else {"no-cache": "1"},
        inputs = depset(
            direct = [ctx.file.main] + ctx.files.srcs + ctx.files.data,
            transitive = [toolchain.files],
        ),
        outputs = [out],
        tools = [toolchain.chpl],
        toolchain = _CHAPEL_TOOLCHAIN_TYPE,
        use_default_shell_env = toolchain.inherit_default_shell_env,
        mnemonic = "ChapelCompile",
        progress_message = "chpl[%s] --fast %s -> %s" % (
            toolchain.identity,
            ctx.file.main.short_path,
            ctx.label.name,
        ),
    )

    outputs = [out]
    if ctx.executable.native_smoke_script:
        smoke = ctx.actions.declare_file(out.basename + ".native-smoke.json")
        ctx.actions.run(
            arguments = [out.path, smoke.path],
            executable = ctx.executable.native_smoke_script,
            inputs = depset(
                direct = [out] + ctx.files.native_smoke_data,
            ),
            outputs = [smoke],
            tools = [ctx.executable.native_smoke_script],
            execution_requirements = {"no-cache": "1"},
            use_default_shell_env = False,
            mnemonic = "PtoonNativeSmoke",
            progress_message = "native smoke %s -> %s" % (
                ctx.label,
                smoke.short_path,
            ),
        )
        outputs.append(smoke)

    return [DefaultInfo(
        executable = out,
        files = depset(outputs),
        runfiles = ctx.runfiles(files = outputs),
    )]

chapel_binary = rule(
    implementation = _chapel_binary_impl,
    doc = """Compile one Chapel program into an executable with `chpl --fast`.

The rule resolves `//tools/bazel/chapel:toolchain_type` and runs `chpl` from
the exec root. Every file in `srcs` and `data` is an action input, so the
file-relative `require` paths in the modules resolve inside the sandbox. A
toolchain that is not cacheable compiles with `no-cache`.

When `native_smoke_script` is set, a second action runs the built binary
through it and writes `<binary>.native-smoke.json`. The default outputs carry
the binary and that sidecar.
""",
    executable = True,
    attrs = {
        "main": attr.label(
            allow_single_file = [".chpl"],
            mandatory = True,
            doc = "The `.chpl` file that holds `proc main`. It is the binary entry point.",
        ),
        "srcs": attr.label_list(
            allow_files = [".chpl", ".c", ".h"],
            doc = "Chapel modules and the vendored C sources and headers that `require` reaches.",
        ),
        "data": attr.label_list(
            allow_files = True,
            doc = "Extra inputs for the compile action, such as the utf8proc data tables.",
        ),
        "module_paths": attr.string_list(
            doc = "Directories passed to `chpl` as `-M`, so it finds sibling modules.",
        ),
        "require_worker_closure_toolchain": attr.bool(
            default = True,
            doc = "Fail analysis unless an action-keyed worker-closure policy selected the toolchain.",
        ),
        "binary_name": attr.string(
            doc = "Basename of the output binary. It defaults to the target name.",
        ),
        "native_smoke_script": attr.label(
            allow_files = True,
            cfg = "exec",
            executable = True,
            doc = "Optional executable that runs the built binary and writes a smoke JSON sidecar. It gets the binary path and the sidecar path as arguments.",
        ),
        "native_smoke_data": attr.label_list(
            allow_files = True,
            doc = "Declared inputs for `native_smoke_script`.",
        ),
    },
    toolchains = [_CHAPEL_TOOLCHAIN_TYPE],
)
