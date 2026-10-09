"""Report contracts: the lines a worker report carries, parsed by owner code (#625).

The foreman gates a report on these lines, its classifier gates and the
judge's rulings, and on nothing else. This module is the one parser: it reads
text and returns what the lines say, or refuses naming every gap. It decides
nothing about the round; callers pass the role and specialty from the owner
dispatch, never from the caller's own reading.

Line forms (one per line; in a report a leading `-`, `*` or up to three spaces
of indent, a line wrapped whole in one backtick span, and trailing backticks,
are tolerated):

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

Refusal classes, each naming the gap: missing, duplicate (an identical repeat
included), extra, N mismatch, malformed candidate. A candidate is any line whose
unmarked text starts with one of the upper-case keywords above.

A report may quote the contract. These lines are illustrative, never contract
lines and never `TRIGGER_DECLARATION` evidence (#737): lines inside a fenced code
block (`` ``` `` or `~~~`, any info string), blockquote lines, lines indented four
spaces or a tab, and lines opening with an inline code span that does not wrap
the whole line. A fence left open runs to the end of the report. The same
classification serves every report-body scanner: `report_lines`,
`trigger_bindings` and, through them, the owners in `engagement.py` and
`triggers.py`. Briefs are foreman-composed, not quoted, and keep their own
tolerant scan (`brief_criteria`).

`declared_contributions` reads the well-formed CONTRIBUTION values anywhere in
the text, quoted or not, gap or no gap. It only ever adds an independence
exclusion, so over-reading fails safe and a declared contribution is never lost
to a refusal elsewhere.
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

_MARKUP = re.compile(r"^[\s>*`-]*")
#: A fenced code block delimiter: up to three spaces, then three or more backticks or tildes.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
#: Report-side leading markup: indent, list markers and quote marks; backticks are judged separately.
_REPORT_MARKUP = re.compile(r"^[\s>*-]*")
_INDENTED_CODE = re.compile(r"^(?: {4}|\t)")
_CODE_SPAN = re.compile(r"^`([^`]+)`$")
#: The prefix of the one structured evidence line a first trigger consultation reports.
TRIGGER_PREFIX = "TRIGGER_DECLARATION: "
_KEYWORD = re.compile(r"^(VERDICT|ACCEPTANCE|CONTRIBUTION|CRITERION)\b")
_VERDICT = re.compile(r"^VERDICT: (\S+)$")
_CONTRIBUTION = re.compile(r"^CONTRIBUTION: (\S+)$")
_ACCEPTANCE = re.compile(r"^ACCEPTANCE ([0-9]+)/([0-9]+): (met|unmet)\s+[—–-]\s*(.*)$")
_CRITERION = re.compile(r"^CRITERION ([0-9]+): (\S.*)$")


def _unmarked(line):
    """The line's text with tolerated leading markup and trailing backticks removed."""
    return _MARKUP.sub("", line).rstrip().rstrip("`").rstrip()


def _candidates(lines, keywords):
    """(line number, unmarked text) for every line starting with one of `keywords`."""
    found = []
    for number, line in enumerate(lines, 1):
        text = _unmarked(line)
        match = _KEYWORD.match(text)
        if match and match.group(1) in keywords:
            found.append((number, match.group(1), text))
    return found


def _unfenced(text):
    """((line number, line) outside fenced code, opening line number of a fence left open or 0).

    CommonMark fence grammar, the one `report_delivery.bare_final` also uses: a
    closing delimiter repeats the opener's character at least as long with no
    text after it. A backtick fence's info string holds no backtick, so
    ```` ```a``` ```` is an inline span rather than an opener.
    """
    rows, fence, length, opened = [], "", 0, 0
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if match:
            run, tail = match.groups()
            if fence:
                if run[0] == fence and len(run) >= length and not tail.strip():
                    fence = ""
                continue
            if run[0] != "`" or "`" not in tail:
                fence, length, opened = run[0], len(run), number
                continue
        if not fence:
            rows.append((number, line))
    return rows, opened if fence else 0


def _operative_text(line):
    """A report line's contract text, or None when the line is quoted, indented code or inline-code prose."""
    if _INDENTED_CODE.match(line):
        return None
    unmarked = _REPORT_MARKUP.sub("", line, count=1)
    if ">" in line[:len(line) - len(unmarked)]:
        return None
    rest = unmarked.rstrip()
    if rest.startswith("`"):
        span = _CODE_SPAN.match(rest)
        return span.group(1) if span else None
    return rest.rstrip("`").rstrip()


def _report_candidates(text, keywords):
    """([(line number, keyword, unmarked text)], open fence line) for every operative report line starting with `keywords`."""
    rows, opened = _unfenced(text)
    found = []
    for number, line in rows:
        candidate = _operative_text(line)
        match = _KEYWORD.match(candidate) if candidate is not None else None
        if match and match.group(1) in keywords:
            found.append((number, match.group(1), candidate))
    return found, opened


def trigger_bindings(text):
    """Every operative `TRIGGER_DECLARATION: ` value in a report: an unfenced line starting at column 0.

    Quoted, indented, list-prefixed, inline-code and fenced copies are examples.
    This recognises lines only; `triggers.validate_bootstrap_binding` judges them.
    """
    return [line[len(TRIGGER_PREFIX):] for _number, line in _unfenced(text)[0] if line.startswith(TRIGGER_PREFIX)]


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
    headings = [index for index, line in enumerate(lines) if line.strip() == CRITERIA_HEADING]
    if len(headings) != 1:
        raise UsageError("The consultation brief carries {} `{}` sections; compose it with exactly one, holding "
                         "`CRITERION <k>: <text>` lines numbered from 1.".format(len(headings), CRITERIA_HEADING),
                         {"gaps": ["criteria section count {}".format(len(headings))]})
    section = []
    for line in lines[headings[0] + 1:]:
        if line.startswith("#"):
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
    found, opened = _report_candidates(text, {"VERDICT", "ACCEPTANCE", "CONTRIBUTION", "CRITERION"})
    for _number, keyword, candidate in found:
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
    if gaps and opened:
        gaps.append("the code fence opened on line {} is never closed, so every line after it is illustrative".format(opened))
    if gaps:
        raise UsageError("The {} report does not meet its contract: {}. Record `needs_work` and send it back to "
                         "its responsibility with these gaps named; no assessment was recorded.".format(
                             role, "; ".join(gaps)), {"gaps": gaps})
    return {"verdict": verdicts[0] if verdicts else None,
            "acceptance": [accepted[k] for k in sorted(accepted)] if consultation else None,
            "contribution": contributions[0] if contributions else None}
