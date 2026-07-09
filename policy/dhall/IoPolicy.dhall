{-
Toon of Mythos IO-layer policy types (TIN-2700).

Keep these schema files simple: records, lists, and enum-like Text values,
so the JSON transition artifact stays trivially comparable.
-}

let Metadata =
      { name : Text
      , owner_linear_issue : Text
      }

let Gate =
      { gated_on : List Text
      , unlocked : Bool
      , rule : Text
      }

let Thresholds =
      { enforce_threshold_tokens : Natural
      , min_savings : Double
      , max_input_bytes : Natural
      , wall_clock_budget_ms : Natural
      }

let Surface =
      { id : Text
      , enabled : Bool
      , purpose : Text
      }

let TrustTier =
      { source : Text
      , tier : Text
      }

let FailureModes =
      { redaction : Text
      , condensation : Text
      }

let Policy =
      { `$comment` : Text
      , schema_version : Natural
      , metadata : Metadata
      , enforcement_gate : Gate
      , thresholds : Thresholds
      , surfaces : List Surface
      , trust_tiers : List TrustTier
      , failure_modes : FailureModes
      }

in  { Metadata
    , Gate
    , Thresholds
    , Surface
    , TrustTier
    , FailureModes
    , Policy
    }
