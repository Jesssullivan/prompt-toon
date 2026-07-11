{-
Toon of Mythos IO-layer policy — typed source of truth (TIN-2700).

Regenerate the JSON transition artifact with:

    dhall-to-json --pretty --file policy/dhall/io.dhall > policy/io.json

Enforcement ordering is A (advisory) -> C (MCP gateway stage, TIN-2702) ->
B (Claude in-harness rewrite), per the blast-radius analysis in
docs/mythos-delivery-design.md. Every surface stays disabled while the
enforcement gate is locked.
-}

let T = ./IoPolicy.dhall

in    { `$comment` =
          "Validated transition artifact. Typed source of truth: policy/dhall/io.dhall. Regenerate with: dhall-to-json --pretty --file policy/dhall/io.dhall. Invariants enforced by tests/test_io_policy.py; enforcement stays locked until the gate tickets close."
      , schema_version = 1
      , metadata =
          { name = "toon-of-mythos-io", owner_linear_issue = "TIN-2700" }
      , enforcement_gate =
          { gated_on = [ "TIN-2695", "TIN-2699" ]
          , unlocked = False
          , rule =
              "While unlocked is false, every surface must have enabled = false. Flipping unlocked to true requires the gate tickets closed and INV-1 through INV-6 satisfied (docs/mythos-delivery-design.md)."
          }
      , thresholds =
          { enforce_threshold_tokens = 600
          , min_savings = 0.25
          , max_input_bytes = 2000000
          , wall_clock_budget_ms = 2000
          }
      , service_limits =
          { max_concurrent_streams = 64
          , transform_workers = 16
          , pending_queue_depth = 64
          , max_documents_per_request = 64
          , max_request_bytes = 16777216
          , max_response_bytes = 268435456
          , max_label_bytes = 4096
          , max_cards_per_document = 24
          }
      , surfaces =
          [ { id = "subagent"
            , enabled = False
            , purpose =
                "Task-tool returns condensed before the fable synthesis seat reads them (Claude PostToolUse updatedToolOutput). Highest fable-token payoff; controlled source set."
            }
          , { id = "mcp"
            , enabled = False
            , purpose =
                "MCP tool results condensed by the TIN-2524 gateway condenser stage (TIN-2702). Harness-agnostic; MCP-sourced output only."
            }
          , { id = "model_gateway"
            , enabled = False
            , purpose =
                "Typed tool/subagent context transformed on an opt-in local harness-to-provider request path (TIN-2790); authority-bearing request fields pass byte-for-byte."
            }
          , { id = "bash"
            , enabled = False
            , purpose = "Shell output condensation in-harness (Claude only today)."
            }
          , { id = "read"
            , enabled = False
            , purpose = "Large file-read condensation in-harness (Claude only today)."
            }
          , { id = "webfetch"
            , enabled = False
            , purpose =
                "Web content condensation in-harness; always untrusted tier, always defanged (INV-4)."
            }
          ]
      , trust_tiers =
          [ { source = "Task", tier = "subagent_return" }
          , { source = "Agent", tier = "subagent_return" }
          , { source = "Bash", tier = "untrusted_tool_output" }
          , { source = "Read", tier = "repo_source" }
          , { source = "WebFetch", tier = "untrusted_web" }
          , { source = "WebSearch", tier = "untrusted_web" }
          , { source = "mcp:searxng", tier = "untrusted_web" }
          , { source = "mcp:linear", tier = "semi_trusted_service" }
          , { source = "mcp:github", tier = "semi_trusted_service" }
          ]
      , failure_modes =
          { redaction = "fail_closed", condensation = "fail_open" }
      }
    : T.Policy
