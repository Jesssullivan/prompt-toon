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

let ServiceLimits =
      { max_concurrent_streams : Natural
      , transform_workers : Natural
      , pending_queue_depth : Natural
      , max_documents_per_request : Natural
      , max_request_bytes : Natural
      , max_inflight_request_bytes : Natural
      , max_response_bytes : Natural
      , max_label_bytes : Natural
      , max_cards_per_document : Natural
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
      , service_limits : ServiceLimits
      , surfaces : List Surface
      , trust_tiers : List TrustTier
      , failure_modes : FailureModes
      }

in  { Metadata
    , Gate
    , Thresholds
    , ServiceLimits
    , Surface
    , TrustTier
    , FailureModes
    , Policy
    }
