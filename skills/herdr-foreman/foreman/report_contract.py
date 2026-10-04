"""Report contracts: the lines a worker report carries, parsed by owner code (#625).

The foreman gates a report on these lines, its classifier gates and the
judge's rulings, and on nothing else. This module is the one parser: it reads
text and returns what the lines say, or refuses naming every gap. It decides
nothing about the round; callers pass the role and specialty from the owner
dispatch, never from the caller's own reading.

Line forms (one per line). Leading whitespace and a list marker (`-` or `*`)
are tolerated on an active declaration. An explicit Markdown quoted example is
report body text: it supplies no verdict, acceptance, criterion, or contribution
declaration. Quote contexts are structural, not semantic:

  - a line whose own prefix is a blockquote marker (`>`), including nested
    markers and list or code content inside the quote. An unmarked following
    line is not a lazy continuation of that quote; it remains an active
    candidate when it starts with a reserved keyword
  - a line inside a Markdown backtick or tilde fence (opening indent of at
    most three spaces, a delimiter of three or more, optional info text; a
    shorter or mismatched delimiter does not close; an unclosed fence runs to
    the end, so declarations hidden inside it cannot satisfy required lines).
    A blockquoted fence opener does not start a document fence
  - a balanced inline-code span that covers the leading reserved token,
    including multi-backtick delimiters and a list-prefixed code example.
    An unmatched opening backtick is not a quote: the line stays an active
    candidate, and the unmatched delimiter stays on the text presented to
    the declaration grammar so the line is malformed rather than valid.
    Four-space indentation alone is not a code block; it remains leading
    whitespace on an active declaration

Plain active declarations may still contain inline code in their evidence.

  VERDICT: blocking | approved
      Exactly one on a reviewer or tester report, and on a consultation whose
      dispatch specialty is in VERDICT_SPECIALTIES. Any other report carrying
      one has an extra line.
  ACCEPTANCE <k>/<N>: met | unmet <dash> <evidence>
      One per k in 1..N on every consultation report. The dash is an em dash,
      an en dash or a hyphen; the evidence is non-empty. N must equal the
      criteria count recorded from the dispatched brief; the report's own N
      never decides anything.
  CONTRIBUTION: none | design | implementation
      Optional, at most once. The worker's own declaration: it can add an
      exclusion, never clear one.
  CRITERION <k>: <text>
      Brief-side only, inside the brief's single `## Acceptance Criteria`
      section. `brief_criteria` counts them; a CRITERION line anywhere else in
      the brief is ignored. In a report, every CRITERION candidate is extra.
      A fenced `## Acceptance Criteria` heading does not create a section.

Refusal classes, each naming the gap: missing, duplicate (an identical repeat
included), extra, N mismatch, malformed candidate. A candidate is any active
(unquoted) line whose unmarked text starts with one of the upper-case keywords
above. Quoted examples never satisfy a required line, never add a contribution
exclusion, and never increase a brief's dispatched N.

`declared_contributions` reads the well-formed CONTRIBUTION values alone, gap or
no gap, so a declared contribution is never lost to a refusal elsewhere. It
uses the same candidate interpretation as `report_lines`.
"""

import re

from .errors import UsageError

#: The two values a VERDICT line may carry.
VERDICTS = frozenset({"blocking", "approved"})
#: The values a CONTRIBUTION line may carry.
CONTRIBUTIONS = frozenset({"none", "design", "implementation"})
#: The two values an ACCEPTANCE line may carry per criterion.
ACCEPTANCE_STATES = frozenset({"met", "unmet"})
#: Responsibilities whose report carries exactly one VERDICT and no ACCEPTANCE.
VERDICT_ROLES = frozenset({"reviewer", "tester"})
#: Consultation responsibilities: one ACCEPTANCE line per recorded criterion.
CONSULTATION_ROLES = frozenset({"advisor", "investigator", "architect"})
#: Consultation specialties whose report also carries exactly one VERDICT.
VERDICT_SPECIALTIES = frozenset({"security", "ux-product", "documentation"})
#: The brief heading the CRITERION block lives under, matched as a whole line.
CRITERIA_HEADING = "## Acceptance Criteria"

_KEYWORD = re.compile(r"^(VERDICT|ACCEPTANCE|CONTRIBUTION|CRITERION)\b")
_VERDICT = re.compile(r"^VERDICT: (\S+)$")
_CONTRIBUTION = re.compile(r"^CONTRIBUTION: (\S+)$")
_ACCEPTANCE = re.compile(r"^ACCEPTANCE ([0-9]+)/([0-9]+): (met|unmet)\s+[—–-]\s*(.*)$")
_CRITERION = re.compile(r"^CRITERION ([0-9]+): (\S.*)$")
_BLOCKQUOTE = re.compile(r"^[ \t]*>")
_LIST_MARKER = re.compile(r"^[-*][ \t]+")
_FENCE_OPEN = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*$")


def _open_fence(line):
    """(`char`, length) when `line` opens a fence, else None."""
    match = _FENCE_OPEN.match(line)
    if match is None:
        return None
    delim, info = match.group(2), match.group(3)
    if delim[0] == "`" and "`" in info:
        return None
    return delim[0], len(delim)


def _closes_fence(line, char, length):
    """Whether `line` closes a fence opened with `char` repeated `length` times."""
    match = _FENCE_CLOSE.match(line)
    if match is None:
        return False
    delim = match.group(2)
    return delim[0] == char and len(delim) >= length


def _fenced_indexes(lines):
    """0-based indexes of lines inside a fence, including the opening and closing rows.

    A blockquoted fence opener does not start a document fence, so an unmarked
    reserved-prefix line after a quoted opener stays an active candidate.
    """
    fenced, fence = set(), None
    for index, line in enumerate(lines):
        if fence is None:
            if _BLOCKQUOTE.match(line):
                continue
            opened = _open_fence(line)
            if opened is not None:
                fence = opened
                fenced.add(index)
            continue
        fenced.add(index)
        if _BLOCKQUOTE.match(line):
            continue
        if _closes_fence(line, fence[0], fence[1]):
            fence = None
    return fenced


def _quoted(lines):
    """True for each line that is a Markdown quote example, not an active declaration."""
    fenced = _fenced_indexes(lines)
    return [index in fenced or _BLOCKQUOTE.match(line) is not None for index, line in enumerate(lines)]


def _code_span_covers_keyword(text):
    """Whether a balanced inline-code span covers a leading reserved token."""
    if not text.startswith("`"):
        return False
    n = 0
    while n < len(text) and text[n] == "`":
        n += 1
    i = n
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        run = 0
        while i + run < len(text) and text[i + run] == "`":
            run += 1
        if run == n:
            return _KEYWORD.match(text[n:i].lstrip()) is not None
        i += run
    return False


def _unmarked(line):
    """Active declaration text, or None when a balanced inline-code example covers the keyword.

    An unmatched opening backtick is never a quote exemption. The delimiter
    stays on the candidate so the declaration grammar refuses it as malformed
    instead of accepting a stripped line.
    """
    text = line.lstrip()
    listed = _LIST_MARKER.match(text)
    if listed is not None:
        text = text[listed.end():].lstrip()
    if _code_span_covers_keyword(text):
        return None
    return text.rstrip()


def _candidates(lines, keywords):
    """(line number, unmarked text) for every active line starting with one of `keywords`."""
    found = []
    for number, (line, is_quoted) in enumerate(zip(lines, _quoted(lines)), 1):
        if is_quoted:
            continue
        text = _unmarked(line)
        if text is None:
            continue
        discovery = text.lstrip("`").lstrip() if text.startswith("`") else text
        match = _KEYWORD.match(discovery)
        if match and match.group(1) in keywords:
            found.append((number, match.group(1), text))
    return found


def declared_contributions(text):
    """Every value a well-formed CONTRIBUTION line in `text` declares, whatever else the report carries."""
    found = set()
    for _number, _keyword, candidate in _candidates(text.splitlines(), {"CONTRIBUTION"}):
        match = _CONTRIBUTION.match(candidate)
        if match is not None and match.group(1) in CONTRIBUTIONS:
            found.add(match.group(1))
    return found


def verdict_required(role, specialty):
    """Whether a report of this responsibility and dispatch specialty carries a VERDICT line."""
    return role in VERDICT_ROLES or role in CONSULTATION_ROLES and specialty in VERDICT_SPECIALTIES


def brief_criteria(text):
    """The criteria count N of a consultation brief, or a refusal naming the gap.

    N is the count of a contiguous `CRITERION 1..N` block in the brief's single
    `## Acceptance Criteria` section, which runs to the next `#`-heading.
    """
    lines = text.splitlines()
    quoted = _quoted(lines)
    headings = [index for index, line in enumerate(lines)
                if not quoted[index] and line.strip() == CRITERIA_HEADING]
    if len(headings) != 1:
        raise UsageError("The consultation brief carries {} `{}` sections; compose it with exactly one, holding "
                         "`CRITERION <k>: <text>` lines numbered from 1.".format(len(headings), CRITERIA_HEADING),
                         {"gaps": ["criteria section count {}".format(len(headings))]})
    section = []
    for index in range(headings[0] + 1, len(lines)):
        line = lines[index]
        if not quoted[index] and line.startswith("#"):
            break
        section.append(line)
    gaps, seen = [], {}
    for number, _keyword, candidate in _candidates(section, {"CRITERION"}):
        match = _CRITERION.match(candidate)
        if match is None:
            gaps.append("malformed CRITERION line {!r}".format(candidate))
            continue
        k = int(match.group(1))
        if k in seen:
            gaps.append("duplicate CRITERION {}".format(k))
        seen[k] = number
    count = len(seen)
    if not count:
        gaps.append("missing CRITERION lines")
    elif sorted(seen) != list(range(1, count + 1)):
        gaps.append("CRITERION numbers {} are not contiguous from 1".format(sorted(seen)))
    if gaps:
        raise UsageError("The consultation brief's acceptance criteria are not a contiguous `CRITERION 1..N` "
                         "block: {}. Recompose the brief with one numbered criterion per line and dispatch "
                         "again; nothing was sent.".format("; ".join(gaps)), {"gaps": gaps})
    return count


def report_lines(text, role, specialty=None, criteria=None):
    """What a report's contract lines say, or a refusal naming every gap.

    `role` is the dispatch's responsibility and `specialty` its requirement's
    specialty. `criteria` is the N recorded from the dispatched brief; it is
    required for a consultation and refused for a reviewer or tester.

    Returns `{"verdict", "acceptance", "contribution"}`: `verdict` is a VERDICTS
    value or None, `acceptance` a list of `{"k", "state", "evidence"}` in k order
    or None, and `contribution` a CONTRIBUTIONS value or None.
    """
    if role not in VERDICT_ROLES | CONSULTATION_ROLES:
        raise UsageError("A {} report carries no required contract line; only reviewer, tester and consultation "
                         "reports are parsed.".format(role), {"role": role})
    consultation = role in CONSULTATION_ROLES
    if consultation and (type(criteria) is not int or criteria < 1):
        raise UsageError("A consultation report is read against the criteria count its dispatched brief "
                         "recorded; none was recorded.", {"role": role})
    if not consultation and criteria is not None:
        raise UsageError("A {} report carries no ACCEPTANCE lines, so no criteria count applies.".format(role),
                         {"role": role})
    wants_verdict = verdict_required(role, specialty)
    limit = criteria if consultation and type(criteria) is int else 0
    gaps, verdicts, contributions, accepted = [], [], [], {}
    for _number, keyword, candidate in _candidates(text.splitlines(),
                                                   {"VERDICT", "ACCEPTANCE", "CONTRIBUTION", "CRITERION"}):
        if keyword == "CRITERION":
            gaps.append("extra CRITERION line {!r}: criteria belong to the brief, never the report".format(candidate))
        elif keyword == "VERDICT":
            match = _VERDICT.match(candidate)
            if match is None or match.group(1) not in VERDICTS:
                gaps.append("malformed VERDICT line {!r}".format(candidate))
            elif not wants_verdict:
                gaps.append("extra VERDICT line: a {} report carries none".format(
                    role if not consultation else "{} consultation".format(specialty or "non-verdict")))
            else:
                verdicts.append(match.group(1))
        elif keyword == "CONTRIBUTION":
            match = _CONTRIBUTION.match(candidate)
            if match is None or match.group(1) not in CONTRIBUTIONS:
                gaps.append("malformed CONTRIBUTION line {!r}".format(candidate))
            else:
                contributions.append(match.group(1))
        else:
            match = _ACCEPTANCE.match(candidate)
            if match is None or not match.group(4).strip():
                gaps.append("malformed ACCEPTANCE line {!r}".format(candidate))
                continue
            k, stated = int(match.group(1)), int(match.group(2))
            if not consultation:
                gaps.append("extra ACCEPTANCE line: a {} report carries none".format(role))
            elif stated != limit:
                gaps.append("ACCEPTANCE {}/{} states N={}, and the dispatched brief recorded {}".format(
                    k, stated, stated, limit))
            elif not 1 <= k <= limit:
                gaps.append("extra ACCEPTANCE {}: the brief states criteria 1..{}".format(k, limit))
            elif k in accepted:
                gaps.append("duplicate ACCEPTANCE {}".format(k))
            else:
                accepted[k] = {"k": k, "state": match.group(3), "evidence": match.group(4).strip()}
    if wants_verdict and not verdicts:
        gaps.append("missing VERDICT line")
    if len(verdicts) > 1:
        gaps.append("duplicate VERDICT line ({} found)".format(len(verdicts)))
    if len(contributions) > 1:
        gaps.append("duplicate CONTRIBUTION line ({} found)".format(len(contributions)))
    if consultation:
        missing = [k for k in range(1, limit + 1) if k not in accepted]
        if missing:
            gaps.append("missing ACCEPTANCE {}".format(", ".join(str(k) for k in missing)))
    if gaps:
        raise UsageError("The {} report does not meet its contract: {}. Record `needs_work` and send it back to "
                         "its responsibility with these gaps named; no assessment was recorded.".format(
                             role, "; ".join(gaps)), {"gaps": gaps})
    return {"verdict": verdicts[0] if verdicts else None,
            "acceptance": [accepted[k] for k in sorted(accepted)] if consultation else None,
            "contribution": contributions[0] if contributions else None}
