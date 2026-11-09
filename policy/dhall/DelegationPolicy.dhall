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
An endpoint is one access path to a provider. Model ids are keyed by endpoint
because the same model can carry a different id on each path. admitted says
whether any seat may run on it; a refused endpoint is listed so a consumer can
recognise and refuse it. status is in-service, unmeasured or forbidden.
-}
let ProviderEndpoint =
      { id : Text
      , model_ids : List Text
      , admitted : Bool
      , status : Text
      , notes : Text
      }

{-
A provider is an allow-list entry: the exact model ids a seat on that provider
may run, the access path it uses and its endpoints. model_ids lists only the
ids on admitted endpoints. A seat whose provider, endpoint or model id is not
admitted here fails closed (TIN-7760).
-}
let Provider =
      { id : Text
      , model_ids : List Text
      , access : Text
      , endpoints : List ProviderEndpoint
      , notes : Text
      }

{-
model_id names the exact model a seat runs (J81). It is Optional because the
fable seat stays provider-managed; dhall-to-json omits the field when None.
model_id_source says where the id comes from: pinned (this policy names it)
or account-config (a config value read from the account after login, never
written here; TIN-7125). provider names the providers[] entry whose
allow-list admits a pinned model_id. endpoint names the admitted provider
endpoint whose model_ids hold that model_id. It is set for a seat whose
provider keys model ids by endpoint and omitted otherwise.
-}
let Seat =
      { id : Text
      , model_class : Text
      , model_id : Optional Text
      , model_id_source : Text
      , provider : Text
      , endpoint : Optional Text
      , cost_tier : Text
      , default_personas : List Text
      , forbidden_personas : List Text
      , notes : Text
      }

{-
A harness seat is a *dispatch* identity, not a model class. It names an agent
harness Fable or Cade can hand bounded work to, the lane that harness runs on,
the exact task envelope it may receive, and the surface that dispatch is
allowed to go through. launcher names the fixed launcher behind a transport
surface such as an swb recipient; it is omitted when the surface is itself
the launcher. Posture is evidentiary-only for the review seats: their output
is evidence for the synthesis seat. The gated-worker posture is the Cade job
seat's: its output is work product whose external acts wait for a GO. No
harness seat carries the operator WORD leg.
-}
let HarnessSeat =
      { id : Text
      , harness : Text
      , model_lane : Text
      , purpose : Text
      , delegable_tasks : List Text
      , forbidden_tasks : List Text
      , dispatch_surface : Text
      , launcher : Optional Text
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
seat serves that class at all. harness_seats names the harness seats that may
see the class's data. Child seat resolution reads seats only, so a harness
seat never becomes a Cade child. SPR-182 puts every Kimi seat, the pi-k3
harness seat included, in the ops class.
-}
let CustodyClass =
      { id : Text
      , seats : List Text
      , harness_seats : List Text
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
Cade's children: the level they run at, where their custody comes from, how
their seats resolve, what happens when no seat remains or their seat is
unavailable, and the standing rules that bind them.

seat_resolution names how the two custody sources combine: a child's
effective seats are its lane's default and alternate seats, in that order,
that also sit in the custody class of its parent home. on_no_seat says what a
child does when that set is empty.
-}
let CadeChildren =
      { level : Text
      , custody : Text
      , seat_resolution : Text
      , on_no_seat : Text
      , provider_fallback : Text
      , bindings : List Text
      , notes : Text
      }

{-
The Cade broker (TIN-7125, new policy). The broker seat may do only what may
lists: route a request to a cade.* lane and author cade.dispatch/v1
envelopes. gates lists what it may gate, and it is empty. names_model is
False: an envelope has no model field, and the dispatch resolver picks the
root. writes lists the envelope fields the broker writes; Cade's own code
stamps every other field, custody from the source item's label included.
new_policy marks a rule that v0.4.0 did not carry: kimi-never-gates had no
clause about naming a model.
-}
let CadeBroker =
      { seat : Text
      , may : List Text
      , gates : List Text
      , names_model : Bool
      , writes : List Text
      , unattended : Text
      , new_policy : Bool
      , notes : Text
      }

{-
A dispatch lane is a cade.delegate.* route that sends a whole job to a Claude
Code job session. parent_persona and root_seat name the job root: the
fable-orchestrator persona on the fable seat, or the critical persona on
opus. custody lists the custody classes the lane admits from the source
item's label.
-}
let DispatchLane =
      { route : Text
      , parent_persona : Text
      , root_seat : Text
      , custody : List Text
      , gate : Text
      , purpose : Text
      }

{-
A unit kind inside a job and the seats that may run it, in preference order.
-}
let DispatchUnit =
      { kind : Text
      , seats : List Text
      , notes : Text
      }

{-
The Fable model id for a job root. It is a config value read from the
account after Jess's attended login, never a value in this policy.
id_pattern only checks that a read id is a Fable-class id.
-}
let FableModelId =
      { config_key : Text
      , source : Text
      , account : Text
      , admitted_when : Text
      , id_pattern : Text
      , on_unread : Text
      , on_unread_reason : Text
      , notes : Text
      }

{-
One login-isolation check: the path, and when cade-job refuses with
login-isolation. Paths compare by real path, and files also by (st_dev,
st_ino), which catches a hard link.
-}
let LoginIsolationPath =
      { path : Text
      , refused_when : Text
      }

{-
Section 8 of the TIN-7125 wire contract: cade-job refuses with
login-isolation unless the job's config dir and its two login files are its
own, and Jess's ~/.claude does not resolve into the job's config dir.
-}
let LoginIsolation =
      { compare : Text
      , refuse_reason : Text
      , paths : List LoginIsolationPath
      , refused_env : List Text
      , inherited_env : Text
      }

{-
Where a job runs and whose login it uses.
-}
let JobSeat =
      { host : Text
      , unix_user : Text
      , account : Text
      , config_dir : Text
      , operator_login : Text
      , key_reach : Text
      , login_isolation : LoginIsolation
      , notes : Text
      }

{-
How the receiver registers on swb. pid is fixed at 1 so the 4-part address
never changes, and bind holds the delivery key for the receiver alone.
-}
let ReceiverRegistration =
      { harness : Text
      , host : Text
      , pid : Natural
      , session_id : Text
      , bind : Bool
      }

{-
Receiver authority (wire contract section 7). allowed_senders is config:
lab's tinyland.cadeJobs.receiver.allowedSenders option. A message from an
unknown sender, an unbound key or another ticket is dropped with a receipt
row and no reply.
-}
let DispatchReceiver =
      { allowed_senders : Text
      , default_sender : Text
      , ticket : Text
      , halt_from : Text
      , unknown_sender : Text
      , drop_reasons : List Text
      , body_label : Text
      , relay_prefix : Text
      , halt_ack_split : Text
      }

{-
A custody class and the source labels that map to it after normalizing.
v1_result is admitted-where-lane-admits or the refusal reason.
-}
let CustodyLabelClass =
      { custody : Text
      , labels : List Text
      , v1_result : Text
      }

{-
Custody from the source item's label (wire contract section 3.3). Any label
outside the table, and no label, refuses with unknown. trim names the
characters the normalize step removes from both ends: ascii-whitespace is
U+0009 to U+000D and U+0020 only (round 4, R4-c).
-}
let CustodyLabels =
      { normalize : Text
      , trim : Text
      , classes : List CustodyLabelClass
      , unknown : Text
      , not_admitted : Text
      , widens : Text
      }

{-
A reply or refusal reason and who raises it: receiver, cade, both or job.
Only cade's own reasons never reach the wire.
-}
let DispatchReason =
      { id : Text
      , raised_by : Text
      }

{-
A carve-out stem and the canonical carve-out tag it stands for (TIN-7125
contract-v1 section 6.1, round 3). A tag or a goal hits the canonical tag
when its normalized form contains the normalized stem.
-}
let CarveOutStem =
      { stem : Text
      , canonical : Text
      }

{-
What a non-ASCII letter after NFKC means: a raw tag is refused with tag, and
a goal makes the carve-out status uncertain, which reroutes a Fable lane to
Opus with goal as the reroute.
-}
let CarveOutNonAscii =
      { tag : Text
      , goal : Text
      }

{-
The inputs the carve-out rule knowingly leaves on a Fable root (wire contract
section 6.1, round 4, R4-a). ruling is the TIN-7125 comment that accepted the
gap, classes names each accepted input class, and added_matching is none:
no resolver adds matching for them. Inside a job the carve-out holds for
them only through section 6.2.
-}
let CarveOutAcceptedGap =
      { ruling : Text
      , answer : Text
      , classes : List Text
      , added_matching : Text
      }

{-
An effect a job may have. envelope says whether an envelope may list it in
allowed_effects. needs_go says the job replies needs-go and waits for Jess
instead. identity names the credential the effect acts under.
-}
let DispatchEffect =
      { id : Text
      , envelope : Bool
      , needs_go : Bool
      , identity : Text
      , notes : Text
      }

{-
A credential Cade's jobs act under. minted_by names who creates it; an agent
never mints one.
-}
let DispatchCredential =
      { id : Text
      , provider : Text
      , holder : Text
      , minted_by : Text
      , agent_mints : Bool
      , notes : Text
      }

{-
What a receipt accounts for. containment is "none": dispatch records counts
and spend and never caps them.
-}
let DispatchAccounting =
      { budget_envelope : Text
      , containment : Text
      , queue : Text
      , audit : List Text
      , notes : Text
      }

{-
Cade dispatch (TIN-7125): the transport, the shared envelope and receipt
contract, the root resolver, the carve-out reroute and inheritance, custody
narrowing, the units inside a job, the Fable id, the job seat, effects,
credentials and accounting.
-}
let CadeDispatch =
      { envelope_schema : Text
      , envelope_schema_file : Text
      , envelope_fields : List Text
      , receipt_schema : Text
      , receipt_schema_file : Text
      , receipt_fields : List Text
      , request_schema : Text
      , request_schema_file : Text
      , resolution_schema : Text
      , resolution_schema_file : Text
      , reply_schema : Text
      , reply_schema_file : Text
      , halt_schema : Text
      , halt_schema_file : Text
      , recipient : Text
      , recipient_delivery_key : Text
      , receiver_registration : ReceiverRegistration
      , receiver : DispatchReceiver
      , reply_kinds : List Text
      , reasons : List DispatchReason
      , receipt_statuses : List Text
      , harness_seat : Text
      , resolver : Text
      , carve_out_tags : List Text
      , carve_out_match : Text
      , carve_out_goal_match : Text
      , carve_out_normalize : Text
      , carve_out_stems : List CarveOutStem
      , carve_out_non_ascii : CarveOutNonAscii
      , carve_out_accepted_gap : CarveOutAcceptedGap
      , carve_out_seat : Text
      , develop_uncertain_tags : Text
      , tag_inheritance : Text
      , custody_source : Text
      , custody_labels : CustodyLabels
      , custody_order : List Text
      , custody_rule : Text
      , child_dispatch : Text
      , fable_model_id : FableModelId
      , job_seat : JobSeat
      , lanes : List DispatchLane
      , units : List DispatchUnit
      , unknown_unit : Text
      , effects : List DispatchEffect
      , credentials : List DispatchCredential
      , accounting : DispatchAccounting
      , notes : Text
      }

let Cade =
      { orchestrator : Text
      , cross_provider_review : Text
      , children : CadeChildren
      , custody_classes : List CustodyClass
      , lanes : List CadeLane
      , broker : CadeBroker
      , dispatch : CadeDispatch
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
    , ProviderEndpoint
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
    , CadeBroker
    , DispatchLane
    , DispatchUnit
    , FableModelId
    , LoginIsolationPath
    , LoginIsolation
    , JobSeat
    , ReceiverRegistration
    , DispatchReceiver
    , CustodyLabelClass
    , CustodyLabels
    , DispatchReason
    , CarveOutStem
    , CarveOutNonAscii
    , CarveOutAcceptedGap
    , DispatchEffect
    , DispatchCredential
    , DispatchAccounting
    , CadeDispatch
    , Cade
    , Attribution
    , Policy
    }
