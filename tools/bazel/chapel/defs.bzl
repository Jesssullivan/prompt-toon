"""Walking-skeleton Bazel rule for compiling Chapel binaries via nix-provided chpl.

TIN-2709 C2, pulled forward into the C1 lane: this encodes the GloriousFlywheel
"Bazel validates/bundles, nix owns the toolchain version" contract. The compile
action shells out to `chpl` (provided on the GF RBE executor image / the nix
devshell); Bazel owns the build graph, the content-addressed cache, and -- under
`--config=executor-backed` -- remote execution on GF REAPI.

Deliberately a genrule-grade rule, NOT a hermetic toolchain. `rules_chapel` with a
`rules_nixpkgs` consumer-provided `chpl` label is the C3 deliverable (first-of-kind;
BCR ships zero Chapel modules today). Here `chpl` is an *environmental* dependency of
the action, resolved by the executor image -- which is precisely "nix owns versions."

`target_compatible_with = ["@platforms//cpu:x86_64", "@platforms//os:linux"]`
is load-bearing, not cosmetic: chpl emits an x86_64-linux ELF, so on the
aarch64-darwin dev host this target is *incompatible* -- a local `bazel build
//...` skips it with an explicit incompatible-target message instead of
silently producing an unusable darwin binary. It only realizes under the GF
linux_x86_64 exec platform, i.e. //tools/bazel/platforms:linux_x86_64 on GF
REAPI. That is the remote-only build substrate expressed in the build graph.
"""

def _chapel_binary_impl(ctx):
    out = ctx.actions.declare_file(ctx.attr.binary_name or ctx.label.name)

    module_flags = []
    for m in ctx.attr.module_paths:
        module_flags += ["-M", m]

    # chpl resolves the modules' file-relative `require "../../c_src/..."` from the
    # main source's directory, so the whole srcs+data tree must be action inputs and
    # the action must run from the exec root (Bazel's default cwd).
    cmd = "exec chpl --fast {main} {mods} -o {out}".format(
        main = ctx.file.main.path,
        mods = " ".join([_shquote(f) for f in module_flags]),
        out = out.path,
    )
    ctx.actions.run_shell(
        command = cmd,
        inputs = ctx.files.srcs + ctx.files.data,
        outputs = [out],
        use_default_shell_env = True,  # chpl + CHPL_HOME come from the executor/devshell env
        mnemonic = "ChapelCompile",
        progress_message = "chpl --fast %s -> %s" % (ctx.file.main.short_path, ctx.label.name),
    )
    return [DefaultInfo(
        executable = out,
        files = depset([out]),
        runfiles = ctx.runfiles(files = [out]),
    )]

def _shquote(s):
    # Module paths are repo-relative, no spaces; keep this trivial and explicit
    # rather than pulling shell.bzl for a walking skeleton.
    return "'" + s.replace("'", "'\\''") + "'"

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
        "binary_name": attr.string(
            doc = "Output basename; defaults to the target name.",
        ),
    },
    # NB: remote-only doctrine (incompatible on darwin, realizes only on the GF
    # linux_x86_64 exec platform) is enforced per-target via
    # `target_compatible_with` in the BUILD file -- it is a common attribute,
    # not a rule() argument.
)
