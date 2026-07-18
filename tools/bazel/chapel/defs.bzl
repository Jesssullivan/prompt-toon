"""Bazel rule for compiling Chapel binaries through a selected toolchain.

TIN-2709 C2, pulled forward into the C1 lane: this encodes the GloriousFlywheel
"Bazel validates/bundles, nix owns the toolchain version" contract. Bazel owns
the build graph, declared compiler boundary, content-addressed cache, and --
under `--config=executor-backed` -- remote execution on GF REAPI.

TIN-2949 introduces the mandatory `ChapelToolchainInfo` boundary. The registered
Linux implementation remains an explicitly non-hermetic bridge to the pinned GF
executor runtime. Darwin resolves an action-keyed, worker-owned Nix closure but
also remains non-hermetic and non-cacheable until Bazel declares or keys every
compiler input. Full reusable `rules_chapel` extraction remains TIN-2710.

Toolchain resolution binds both execution and target platforms. Darwin resolves
only the authenticated GF worker-closure bridge; it cannot fall through to the
developer host's PATH and remains non-cacheable until its closure digest is
part of the action key.
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
    # - Chapel chpl man page: --fast, -M/--module-dir, -o.
    # - Chapel C interop technote: require paths are source-file-relative.
    # - Bazel actions.run: tools and transitive compiler files are declared
    #   inputs; only the transitional Linux toolchain inherits its worker env.
    # chpl resolves the modules' file-relative `require "../../c_src/..."` from the
    # main source's directory, so the whole srcs+data tree must be action inputs and
    # the action must run from the exec root (Bazel's default cwd).
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
    executable = True,
    attrs = {
        "main": attr.label(
            allow_single_file = [".chpl"],
            mandatory = True,
            doc = "The .chpl file with `proc main` (the binary entry point).",
        ),
        "srcs": attr.label_list(
            allow_files = [".chpl", ".c", ".h"],
            doc = "All Chapel modules + vendored C sources/headers reachable via `require`.",
        ),
        "data": attr.label_list(
            allow_files = True,
            doc = "Extra runtime/compile inputs (e.g. utf8proc data tables).",
        ),
        "module_paths": attr.string_list(
            doc = "Directories passed as `-M` so chpl finds sibling modules.",
        ),
        "require_worker_closure_toolchain": attr.bool(
            default = True,
            doc = "Reject toolchains not selected by an action-keyed worker closure policy.",
        ),
        "binary_name": attr.string(
            doc = "Output basename; defaults to the target name.",
        ),
        "native_smoke_script": attr.label(
            allow_files = True,
            cfg = "exec",
            executable = True,
            doc = "Optional executable that runs the built binary and writes a smoke JSON sidecar.",
        ),
        "native_smoke_data": attr.label_list(
            allow_files = True,
            doc = "Declared inputs used by native_smoke_script.",
        ),
    },
    toolchains = [_CHAPEL_TOOLCHAIN_TYPE],
)
