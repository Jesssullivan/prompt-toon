/* TIN-2708 C1: pure-function Chapel port of the TOON row-encoding surface
 * from prompt_toon/cli.py (toon_escape, cli.py:272-300; encode_rows_to_toon,
 * cli.py:303-315).
 *
 * Scope for C1 (see mythos-delivery-design.md phase table): this module is
 * exercised ONLY by property tests in this phase —
 *   - escape round-trip: toonEscape output, when unquoted, is byte-identical
 *     to the input; when quoted, the escape sequences it introduces are
 *     unambiguously reversible (backslash escaped first, so `\\n` in the
 *     source can never be misread as an escaped newline).
 *   - column-shift resistance: encodeRows must fail loudly (not silently
 *     misalign cells) when a row's width does not match the header's width.
 * Full typed-row handling (JSON scalars -> TOON cells, null/bool/number
 * formatting) is deferred to C2. Every cell this module touches is already
 * a Chapel string handed in by the caller — see "PRE-STRINGIFICATION" below
 * for exactly what that does and does not mean here.
 *
 * Dependency-free by design: no `use`, no FFI, no subprocess protocol.
 * This is the opposite end of the module spectrum from Main.chpl (the
 * stdin/stdout binary dispatcher in this same directory): Toon.chpl is meant
 * to be trivially unit- and property-testable in pure Chapel, with protocol
 * wiring layered on top of it in a later phase, not baked into it.
 *
 * PRE-STRINGIFICATION AND THE None/True/False SHORTCUT (explicit scope cut):
 * cli.py's toon_escape(value: Any, ...) special-cases Python's `None`,
 * `True`, and `False` objects with early returns ("null"/"true"/"false",
 * always unquoted — cli.py:273-278) *before* it ever calls `str(value)`.
 * That branch depends on Python's dynamic typing (knowing the *original*
 * value was the bool True, not the string "true") and has no equivalent
 * here: Chapel's toonEscape takes a `string` that the caller has already
 * stringified (the moral equivalent of cli.py's `text = str(value)` at
 * cli.py:279), so a cell that is literally the text "true" is, from this
 * module's point of view, indistinguishable from a stringified Python
 * `True`. Per the C1 task scope, this module ports exactly the needs_quote
 * decision table and escape order that follow cli.py:279 onward — i.e. a
 * pre-stringified cell whose lowercase form is "true"/"false"/"null" is
 * always quoted here (cli.py:289), even though a *live* Python bool/None
 * value would have bypassed quoting entirely at cli.py:275-278. Recovering
 * that distinction (so a real JSON `true` round-trips unquoted while the
 * string "true" round-trips quoted) requires carrying a type tag alongside
 * each cell and is explicitly C2 work (typed row handling).
 */
module Toon {

  /* Returns a 4-hex-digit (lowercase) string for a codepoint in 0..0xFFFF.
   * Private helper for jsonQuoteDelimiter's \uXXXX escapes; deliberately
   * hand-rolled (no `use IO`/FormattedIO) to keep this module import-free.
   */
  private proc hex4(value: int): string {
    const digits = "0123456789abcdef";
    var v = value;
    var result = "";
    for i in 1..4 {
      const nibble = (v & 0xF): int;
      result = digits[nibble: byteIndex] + result;
      v >>= 4;
    }
    return result;
  }

  /* Mirrors `json.dumps(delimiter)` as used at cli.py:311 to render the
   * ` delimiter=...` header suffix when the delimiter is not the default
   * ",". json.dumps there is called with its default `ensure_ascii=True`,
   * so CPython's encoder \u-escapes every non-ASCII codepoint in addition
   * to the usual JSON control-character escapes; this reproduces both.
   *
   * UNCERTAINTY (flagged for review): delimiters are, in every call site
   * in cli.py, a single ASCII character ("," or "\t"). The astral-codepoint
   * (>0xFFFF, UTF-16 surrogate-pair) branch below is written to match
   * CPython's encoder but is not exercised by any realistic delimiter and
   * has had no property-test coverage in this phase.
   */
  private proc jsonQuoteDelimiter(text: string): string {
    var acc = "\"";
    for cp in text.codepoints() {
      if cp == 0x22 {
        acc += "\\\"";
      } else if cp == 0x5C {
        acc += "\\\\";
      } else if cp == 0x08 {
        acc += "\\b";
      } else if cp == 0x0C {
        acc += "\\f";
      } else if cp == 0x0A {
        acc += "\\n";
      } else if cp == 0x0D {
        acc += "\\r";
      } else if cp == 0x09 {
        acc += "\\t";
      } else if cp < 0x20 || cp >= 0x7F {
        if cp <= 0xFFFF {
          acc += "\\u" + hex4(cp: int);
        } else {
          // Surrogate pair for astral codepoints (see UNCERTAINTY above).
          const v = (cp: int) - 0x10000;
          const hi = 0xD800 + (v >> 10);
          const lo = 0xDC00 + (v & 0x3FF);
          acc += "\\u" + hex4(hi) + "\\u" + hex4(lo);
        }
      } else {
        var ch = "";
        ch.appendCodepointValues(cp: int);
        acc += ch;
      }
    }
    acc += "\"";
    return acc;
  }

  /* Port of toon_escape (cli.py:272-300), restricted to the
   * post-stringification path (cli.py:279-300) per the PRE-STRINGIFICATION
   * note above: `value` here is the moral equivalent of cli.py's `text`
   * right after `text = str(value)`, not the original Any-typed value.
   *
   * needs_quote (cli.py:280-290), ported condition-for-condition:
   *   - text == ""                                  (cli.py:281)
   *   - text.strip() != text                         (cli.py:282)
   *   - "\n" in text                                  (cli.py:283)
   *   - "\r" in text                                  (cli.py:284)
   *   - "\t" in text                                  (cli.py:285)
   *   - "\\" in text                                  (cli.py:286)
   *   - delimiter in text                             (cli.py:287)
   *   - any of '"', '[', ']', '{', '}', ':' in text    (cli.py:288)
   *   - text.lower() in {"true", "false", "null"}      (cli.py:289)
   *
   * Escape order when quoting (cli.py:293-299), applied in this exact
   * order so a literal backslash in the source can never be misread as
   * introducing one of the later escapes:
   *   backslash -> quote -> \n -> \r -> \t, then wrapped in `"..."`.
   *
   * UNCERTAINTY (flagged for review): Python's `str.strip()` (cli.py:282,
   * no-arg form) strips every Unicode codepoint for which
   * `str.isspace()` is true — beyond ASCII space/tab/CR/LF/VT/FF, that
   * includes things like NBSP (U+00A0) and various Unicode space
   * separators. This port strips exactly " \t\r\n\v\f" (Chapel
   * string.strip's ASCII-whitespace default extended with \v\f to match
   * Python's `string.whitespace`); it will NOT flag a cell as needing a
   * quote solely because it is padded with a non-ASCII Unicode space.
   * Cells built from ASCII/typical JSON text are unaffected.
   *
   * UNCERTAINTY (flagged for review): `text.lower()` (cli.py:289) uses
   * Chapel's `string.toLower()`, which may diverge from CPython's Unicode
   * case-folding on exotic codepoints (e.g. Turkish dotless i, German
   * sharp s expansions). Immaterial for the literal ASCII targets
   * "true"/"false"/"null" this check compares against.
   */
  proc toonEscape(value: string, delimiter: string = ","): string {
    const text = value;
    const stripped = text.strip(" \t\r\n\v\f");
    const lowered = text.toLower();
    const needsQuote =
      text.isEmpty()
      || stripped != text
      || text.find("\n") != -1
      || text.find("\r") != -1
      || text.find("\t") != -1
      || text.find("\\") != -1
      || text.find(delimiter) != -1
      || text.find("\"") != -1
      || text.find("[") != -1
      || text.find("]") != -1
      || text.find("{") != -1
      || text.find("}") != -1
      || text.find(":") != -1
      || lowered == "true"
      || lowered == "false"
      || lowered == "null";

    if !needsQuote then return text;

    var escaped = text.replace("\\", "\\\\");
    escaped = escaped.replace("\"", "\\\"");
    escaped = escaped.replace("\n", "\\n");
    escaped = escaped.replace("\r", "\\r");
    escaped = escaped.replace("\t", "\\t");
    return "\"" + escaped + "\"";
  }

  /* Port of encode_rows_to_toon's line-assembly (cli.py:303-315), over
   * PRE-STRINGIFIED cell matrices: `header` is the column-name row (the
   * analogue of cli.py's `fields = list(rows[0].keys())`, cli.py:306) and
   * `rows` holds, per row, the already-`str()`-ified scalar cell values
   * (the analogue of `row[field]` at cli.py:314, before toon_escape is
   * applied) — this proc applies toonEscape to each cell itself, exactly
   * as cli.py:314 does inline during line assembly.
   *
   * DESIGN DECISION (flagged for review — signature deviation from
   * cli.py): cli.py's encode_rows_to_toon takes a `name` and emits
   * `f"{name}[{len(rows)}]{{{fields}}}{delim_name}:"` as the header line
   * (cli.py:312). The signature given for this port —
   * `encodeRows(header, rows, delimiter)` — has no `name` parameter, so
   * this proc emits the name-less form `"[{rows.size}]{{header}}{delim
   * name}:"` and leaves prefixing the JSON-path `name` (e.g. `orders`) to
   * whatever composes the final document (deferred to C2 integration with
   * typed rows, where the caller holds the source JSON path). The empty
   * -rows short-circuit mirrors this: cli.py returns `f"{name}[0]:"`
   * (cli.py:304-305, no trailing newline) when rows is empty; this port
   * returns `"[0]:"` for the same reason and the same missing-name caveat.
   *
   * Column-shift resistance: cli.py's dict-keyed rows make a field-order
   * mismatch impossible to represent silently once decoded into per-row
   * dicts (cli.py:307-308 raises ValueError on a field-order mismatch
   * before any line is emitted). The string-matrix representation here
   * has no such structural guarantee — a caller could hand in a `rows[i]`
   * whose width does not match `header` — so this proc throws an
   * IllegalArgumentError up front, before emitting anything, rather than
   * silently zipping mismatched cells against the wrong column names.
   */
  proc encodeRows(header: [] string, rows: [] [] string, delimiter: string): string throws {
    if rows.size == 0 then return "[0]:";

    for (row, i) in zip(rows, 0..) {
      if row.size != header.size then
        throw new owned IllegalArgumentError(
          "Toon.encodeRows: row " + i: string + " has " + row.size: string +
          " column(s) but header has " + header.size: string +
          " (column-shift: TOON row encoding requires every row to match " +
          "the header width, cli.py:307-308)");
    }

    const delimName = if delimiter == "," then "" else " delimiter=" + jsonQuoteDelimiter(delimiter);
    var docLines: [0..rows.size] string;
    docLines[0] = "[" + rows.size: string + "]{" + ",".join(header) + "}" + delimName + ":";

    for (row, i) in zip(rows, 0..) {
      var escapedCells: [row.domain] string;
      for j in row.domain do escapedCells[j] = toonEscape(row[j], delimiter);
      docLines[i + 1] = "  " + delimiter.join(escapedCells);
    }

    return "\n".join(docLines) + "\n";
  }

}
