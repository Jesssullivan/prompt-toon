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

{-
model_id names the exact model a seat runs (J81). It is Optional because the
fable seat stays provider-managed; dhall-to-json omits the field when None.
-}
let Seat =
      { id : Text
      , model_class : Text
      , model_id : Optional Text
      , cost_tier : Text
      , default_personas : List Text
      , forbidden_personas : List Text
      , notes : Text
      }

{-
A harness seat is a *dispatch* identity, not a model class. It names an agent
harness Fable can hand bounded technical work to, the lane that harness runs
on, the exact task envelope it may receive, and the launcher surface that
dispatch is allowed to go through. Its posture is evidentiary-only: harness
output is evidence for the synthesis seat, never the operator WORD leg.
-}
let HarnessSeat =
      { id : Text
      , harness : Text
      , model_lane : Text
      , purpose : Text
      , delegable_tasks : List Text
      , forbidden_tasks : List Text
      , dispatch_surface : Text
      , posture : Text
      }

{-
A budget fallback names the seat a lane may drop to when its default seat's
quota is exhausted, the personas it covers, and the obligations the downgraded
lane carries (J81(e)). It never widens who may merge: obligations bind the
merge to a review on the original seat.
-}
let BudgetFallback =
      { id : Text
      , from_seat : Text
      , to_seat : Text
      , trigger : Text
      , personas : List Text
      , obligations : List Text
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
      , seats : List Seat
      , harness_seats : List HarnessSeat
      , personas : List Persona
      , lanes : List Lane
      , budget_fallbacks : List BudgetFallback
      , enforcement : List Rule
      , attribution : Attribution
      }

in  { Metadata
    , ScarceResource
    , Seat
    , HarnessSeat
    , Persona
    , BudgetFallback
    , Lane
    , Rule
    , Attribution
    , Policy
    }
