"""A TypeSafe System One client, shared by every Jev consumer in this plugin.

Standard library only. It knows the HTTP contract of
https://docs.typesafe.ai/api and nothing about reports, verdicts or claims:
the report classifier (#531) is its first consumer, and #472's evidence
assessor reuses it as is. A consumer builds its questions, calls
`system_one`, and reads the validated answers with `nouls` or `choices`.

Contract:
  `api_key()` returns `TYPESAFE_API_KEY` from the environment, or raises
  `KeyMissing`. `system_one(state, questions, model, key=...)` POSTs one
  request and returns the parsed response after checking that it answers
  every question asked, with the pinned model. Every failure raises a
  `TypeSafeError` subclass whose message names what to do and never carries
  the key, the request, or the provider's response body: a provider body can
  echo submitted text, and the key must never reach a log.

Retries: 429 and 529 are retried with exponential backoff, up to
`MAX_ATTEMPTS` (script-owned constants below). Every other failure is final.

Tests replace the HTTP layer through the `transport` argument; nothing in the
test suite reaches the network (rules/testing-standards.md Determinism).
"""

import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_ENV = "TYPESAFE_API_KEY"
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 1_048_576
RETRY_STATUSES = frozenset({429, 529})
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.0
QUESTION_TYPES = frozenset({"noul", "choice"})

#: (url, body, headers, timeout) -> (status, response body bytes)
Transport = Callable[[str, bytes, Mapping[str, str], float], "tuple[int, bytes]"]


class TypeSafeError(Exception):
    """A failed TypeSafe call. The message is safe to print."""


class KeyMissing(TypeSafeError):
    """No usable key in the environment."""


class Unavailable(TypeSafeError):
    """The service could not answer: network, auth, rate limit, overload."""


class InvalidResponse(TypeSafeError):
    """The service answered outside the documented contract."""


class InvalidRequest(TypeSafeError):
    """The caller built a request this client refuses to send."""


def api_key(environ: Mapping[str, str] | None = None) -> str:
    """The key from `TYPESAFE_API_KEY`; never echoed anywhere."""
    key = (os.environ if environ is None else environ).get(KEY_ENV, "")
    if not key or any(char.isspace() for char in key):
        raise KeyMissing("{} is not set; export it (see .env.example) to use Jev".format(KEY_ENV))
    return key


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the Authorization header to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> "tuple[int, bytes]":
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # The body is discarded unread: it may echo the submitted state.
        return exc.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Unavailable("cannot reach TypeSafe at {}; check the network and retry later".format(url)) from None


def _check_questions(questions: Mapping[str, Any]) -> None:
    if not isinstance(questions, Mapping) or not questions:
        raise InvalidRequest("ask at least one question")
    for qid, question in questions.items():
        if not isinstance(qid, str) or not qid or not isinstance(question, Mapping):
            raise InvalidRequest("each question needs a non-empty string id and an object body")
        if question.get("type") not in QUESTION_TYPES:
            raise InvalidRequest("question '{}' has a type this client does not read; use noul or choice".format(qid))
        if not question.get("instructions"):
            raise InvalidRequest("question '{}' has no instructions".format(qid))
        if question["type"] == "choice" and not isinstance(question.get("criteria"), Mapping):
            raise InvalidRequest("choice question '{}' needs a criteria map of its options".format(qid))


def _probability(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def validate(response: Any, questions: Mapping[str, Any], model: str) -> dict:
    """The response, checked against the questions asked and the pinned model."""
    bad = "TypeSafe answered outside its documented contract ({}); keep the fallback and check https://docs.typesafe.ai/api"
    if not isinstance(response, dict):
        raise InvalidResponse(bad.format("not an object"))
    if response.get("model") != model:
        raise InvalidResponse(bad.format("answered by a model other than the pinned {}".format(model)))
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise InvalidResponse(bad.format("answers do not match the questions asked"))
    for qid, question in questions.items():
        answer = answers[qid]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise InvalidResponse(bad.format("answer '{}' has the wrong type".format(qid)))
        if question["type"] == "noul":
            if not _probability(answer.get("noul")):
                raise InvalidResponse(bad.format("answer '{}' carries no probability".format(qid)))
        else:
            distribution = answer.get("probabilities")
            if (not isinstance(distribution, dict) or set(distribution) != set(question["criteria"])
                    or not all(_probability(p) for p in distribution.values())
                    or not math.isclose(sum(distribution.values()), 1, abs_tol=1e-3)
                    or answer.get("choice") not in distribution
                    or not _probability(answer.get("confidence"))):
                raise InvalidResponse(bad.format("answer '{}' carries no valid distribution".format(qid)))
    return response


def system_one(state: Any, questions: Mapping[str, Any], model: str, *, key: str,
               transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
               endpoint: str = ENDPOINT) -> dict:
    """One System One evaluation; the validated response."""
    _check_questions(questions)
    body = encode({"state": state, "model": model, "questions": questions})
    if key in body.decode("utf-8"):
        raise InvalidRequest("the request text contains the API key; remove it before asking")
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    send = transport or urllib_transport
    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, data = send(endpoint, body, headers, TIMEOUT_SECONDS)
        if status in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
            sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
            continue
        if status == 401:
            raise Unavailable("TypeSafe refused the key (HTTP 401); check {}".format(KEY_ENV))
        if status in RETRY_STATUSES:
            raise Unavailable("TypeSafe stayed rate-limited or overloaded (HTTP {}) after {} attempts; "
                              "retry later".format(status, MAX_ATTEMPTS))
        if status == 422:
            raise InvalidRequest("TypeSafe rejected the request as malformed (HTTP 422); "
                                 "check the question shape against https://docs.typesafe.ai/api")
        if status != 200:
            raise Unavailable("TypeSafe failed with HTTP {}; retry later".format(status))
        if len(data) > MAX_RESPONSE_BYTES:
            raise InvalidResponse("TypeSafe's response exceeded {} bytes; keep the fallback".format(MAX_RESPONSE_BYTES))
        try:
            parsed = json.loads(data)
        except (UnicodeError, ValueError):
            raise InvalidResponse("TypeSafe returned a body that is not JSON; keep the fallback") from None
        return validate(parsed, questions, model)
    raise Unavailable("TypeSafe made no attempt; MAX_ATTEMPTS must be at least 1")


def nouls(response: Mapping[str, Any]) -> "dict[str, float]":
    """P(yes) per Noul question of a validated response."""
    return {qid: float(answer["noul"]) for qid, answer in response["answers"].items()
            if answer["type"] == "noul"}


def choices(response: Mapping[str, Any]) -> "dict[str, dict]":
    """Choice, distribution and confidence per Choice question of a validated response."""
    return {qid: {"choice": answer["choice"], "probabilities": dict(answer["probabilities"]),
                  "confidence": float(answer["confidence"])}
            for qid, answer in response["answers"].items() if answer["type"] == "choice"}
