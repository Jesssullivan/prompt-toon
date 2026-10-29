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
A provider is an allow-list entry: the exact model ids a seat on that provider
may run and the access path it uses. A seat whose provider or model id is not
listed here fails closed (TIN-7760).
-}
let Provider =
      { id : Text
      , model_ids : List Text
      , access : Text
      , notes : Text
      }

{-
model_id names the exact model a seat runs (J81). It is Optional because the
fable seat stays provider-managed; dhall-to-json omits the field when None.
provider names the providers[] entry whose allow-list admits that model_id.
-}
let Seat =
      { id : Text
      , model_class : Text
      , model_id : Optional Text
      , provider : Text
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
quota is exhausted (J81(e)). It is scoped by lane, not only by persona: lanes
lists every route it reaches, and each must sit on one of its personas. A
lane of a covered persona that is not listed keeps its default seat. The
obligations bind the downgraded lane. A fallback never widens who may merge:
the merge waits for a review on the original seat.
-}
let BudgetFallback =
      { id : Text
      , from_seat : Text
      , to_seat : Text
      , trigger : Text
      , personas : List Text
      , lanes : List Text
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

{-
A custody class names the seats that may see a Cade child's data. A child's
custody class follows its parent home. An empty seat list means no hosted
seat serves that class at all.
-}
let CustodyClass =
      { id : Text
      , seats : List Text
      , notes : Text
      }

{-
A Cade lane is a business-side route. Its seats are the default seat plus the
permitted alternates; every one must sit in the lane persona's model classes
and in the lane's custody class. gate names the standing confirm that binds
the lane's external acts.
-}
let CadeLane =
      { route : Text
      , persona : Text
      , custody : Text
      , default_seat : Text
      , alternate_seats : List Text
      , gate : Text
      , purpose : Text
      }

{-
Cade's children: the level they run at, where their custody comes from, what
happens when their seat is unavailable, and the standing rules that bind them.
-}
let CadeChildren =
      { level : Text
      , custody : Text
      , provider_fallback : Text
      , bindings : List Text
      , notes : Text
      }

let Cade =
      { orchestrator : Text
      , cross_provider_review : Text
      , children : CadeChildren
      , custody_classes : List CustodyClass
      , lanes : List CadeLane
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
      , providers : List Provider
      , seats : List Seat
      , harness_seats : List HarnessSeat
      , personas : List Persona
      , lanes : List Lane
      , budget_fallbacks : List BudgetFallback
      , cade : Cade
      , enforcement : List Rule
      , attribution : Attribution
      }

in  { Metadata
    , ScarceResource
    , Provider
    , Seat
    , HarnessSeat
    , Persona
    , BudgetFallback
    , Lane
    , Rule
    , CustodyClass
    , CadeLane
    , CadeChildren
    , Cade
    , Attribution
    , Policy
    }
