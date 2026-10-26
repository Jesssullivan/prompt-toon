{-
Redaction policy types (J82(d), PT-1b).

Records, lists and enum-like Text only. The JSON artifact stays easy to
compare across consumers.
-}
let Metadata = { name : Text, owner_linear_issue : Text, ruling : Text }

let Dialect =
      { name : Text
      , forbidden_constructs : List Text
      , word_boundary : Text
      , rule : Text
      }

let Replacement = { token : Text, preserve_line_breaks : Bool, rule : Text }

let Pattern = { id : Text, kind : Text, source : Text, case_insensitive : Bool }

let RedactionPolicy =
      { `$comment` : Text
      , schema_version : Natural
      , metadata : Metadata
      , dialect : Dialect
      , replacement : Replacement
      , patterns : List Pattern
      }

in  { Metadata, Dialect, Replacement, Pattern, RedactionPolicy }
