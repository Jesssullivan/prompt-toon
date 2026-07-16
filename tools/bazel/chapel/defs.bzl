"""Bazel rule for compiling Chapel binaries through a selected toolchain.

TIN-2709 C2, pulled forward into the C1 lane: this encodes the GloriousFlywheel
"Bazel validates/bundles, nix owns the toolchain version" contract. Bazel owns
the build graph, declared compiler boundary, content-addressed cache, and --
under `--config=executor-backed` -- remote execution on GF REAPI.

TIN-2949 introduces the mandatory `ChapelToolchainInfo` boundary. The registered
Linux implementation remains an explicitly non-hermetic bridge to the pinned GF
executor runtime; a Darwin build must resolve a declared, hermetic compiler
closure. Full reusable `rules_chapel` extraction remains TIN-2710.

Toolchain resolution binds both execution and target platforms. Today only the
Linux implementation is registered, so a Darwin analysis fails closed instead
of falling through to the developer host's PATH.
"""

_CHAPEL_TOOLCHAIN_TYPE = "//tools/bazel/chapel:toolchain_type"

def _chapel_binary_impl(ctx):
    out = ctx.actions.declare_file(ctx.attr.binary_name or ctx.label.name)

    toolchain = ctx.toolchains[_CHAPEL_TOOLCHAIN_TYPE].chapel
    if ctx.attr.require_hermetic_toolchain and not toolchain.hermetic:
        fail(
            "%s requires a hermetic Chapel toolchain; selected %s" %
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
        inputs = depset(
            direct = [ctx.file.main] + ctx.files.srcs + ctx.files.data,
            transitive = [toolchain.files],
        ),
        outputs = [out],
        tools = [toolchain.chpl],
        use_default_shell_env = not toolchain.hermetic,
        mnemonic = "ChapelCompile",
        progress_message = "chpl[%s] --fast %s -> %s" % (
            toolchain.identity,
            ctx.file.main.short_path,
            ctx.label.name,
        ),
    )
    return [DefaultInfo(
        executable = out,
        files = depset([out]),
        runfiles = ctx.runfiles(files = [out]),
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
        "require_hermetic_toolchain": attr.bool(
            default = True,
            doc = "Reject transitional environmental compiler toolchains.",
        ),
        "binary_name": attr.string(
            doc = "Output basename; defaults to the target name.",
        ),
    },
    toolchains = [_CHAPEL_TOOLCHAIN_TYPE],
)
