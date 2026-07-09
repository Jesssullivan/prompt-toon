{-
Toon of Mythos delegation-policy types.

Keep these schema files simple: records, lists, and enum-like Text values.
This makes the JSON transition artifact (policy/delegation.json) easy to
compare against this Dhall source of truth, per the lab test/dhall convention.
-}

let Metadata =
      { name : Text
      , owner_linear_issue : Text
      , founding_docs : List Text
      }

let ScarceResource =
      { id : Text
      , kind : Text
      , notes : Text
      }

let Persona =
      { id : Text
      , model_classes : List Text
      , purpose : Text
      , doctrine : Text
      , forbidden_tasks : List Text
      }

let Lane =
      { route : Text
      , persona : Text
      , purpose : Text
      }

let Rule =
      { id : Text
      , severity : Text
      , rule : Text
      }

let Attribution =
      { convention : Text
      , values : List Text
      }

let Policy =
      { `$comment` : Text
      , schema_version : Natural
      , metadata : Metadata
      , scarce_resources : List ScarceResource
      , personas : List Persona
      , lanes : List Lane
      , enforcement : List Rule
      , attribution : Attribution
      }

in  { Metadata
    , ScarceResource
    , Persona
    , Lane
    , Rule
    , Attribution
    , Policy
    }
