"""The shared TypeSafe client: its HTTP contract, retries and secret hygiene, against a stubbed transport."""

import http.client
import io
import json
import sys
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "classify"))

import typesafe_client as ts  # noqa: E402 -- the classify dir is on sys.path only from here

KEY = "ts-live-0123456789abcdef"
MODEL = "jev-1.13.0"
NOUL = {"is_open": {"type": "noul", "instructions": "Does `report` name an open item?"}}
CHOICE = {"relation": {"type": "choice", "instructions": "How does `sources` relate to `claim`?",
                       "criteria": {"supported": None, "contradicted": None}}}


def answer(qid="is_open", p=0.9, model=MODEL):
    return {"model": model, "answers": {qid: {"type": "noul", "noul": p}},
            "usage": {"input_tokens": 10, "output_tokens": 2}}


class Transport:
    """Replays (status, body) pairs and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(body), "headers": dict(headers), "timeout": timeout})
        status, payload = self.replies.pop(0)
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")


def ask(transport, questions=NOUL, sleeps=None):
    return ts.system_one({"report": "text"}, questions, MODEL, key=KEY, transport=transport,
                         sleep=(sleeps.append if sleeps is not None else lambda _: None))


class KeyTest(unittest.TestCase):
    def test_reads_the_key_from_the_environment(self):
        self.assertEqual(ts.api_key({"TYPESAFE_API_KEY": KEY}), KEY)

    def test_a_missing_or_malformed_key_names_the_variable_and_never_the_value(self):
        for environ in ({}, {"TYPESAFE_API_KEY": ""}, {"TYPESAFE_API_KEY": "has space"}):
            with self.subTest(environ=environ), self.assertRaises(ts.KeyMissing) as caught:
                ts.api_key(environ)
            self.assertIn("TYPESAFE_API_KEY", str(caught.exception))
            self.assertNotIn("has space", str(caught.exception))


class RequestTest(unittest.TestCase):
    def test_posts_state_model_and_questions_with_the_bearer_key(self):
        transport = Transport((200, answer()))
        response = ask(transport)
        call = transport.calls[0]
        self.assertEqual(call["url"], ts.ENDPOINT)
        self.assertEqual(call["body"], {"state": {"report": "text"}, "model": MODEL, "questions": NOUL})
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(ts.nouls(response), {"is_open": 0.9})

    def test_reads_a_choice_distribution(self):
        reply = {"model": MODEL, "usage": {}, "answers": {"relation": {
            "type": "choice", "choice": "supported", "probabilities": {"supported": 0.8, "contradicted": 0.2},
            "confidence": 0.7}}}
        self.assertEqual(ts.choices(ask(Transport((200, reply)), CHOICE))["relation"]["choice"], "supported")

    def test_refuses_a_request_that_carries_the_key(self):
        transport = Transport()
        with self.assertRaises(ts.InvalidRequest) as caught:
            ts.system_one({"report": "leaked " + KEY}, NOUL, MODEL, key=KEY, transport=transport)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(transport.calls, [])

    def test_refuses_an_unreadable_question_before_any_call(self):
        for questions in ({}, {"q": {"type": "score", "instructions": "x"}}, {"q": {"type": "noul"}},
                          {"q": {"type": "choice", "instructions": "x"}}):
            with self.subTest(questions=questions), self.assertRaises(ts.InvalidRequest):
                ts.system_one("s", questions, MODEL, key=KEY, transport=Transport())


class FailureTest(unittest.TestCase):
    def test_rate_limits_back_off_exponentially_then_succeed(self):
        sleeps = []
        transport = Transport((429, b""), (529, b""), (200, answer()))
        ask(transport, sleeps=sleeps)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [ts.BACKOFF_BASE_SECONDS, ts.BACKOFF_BASE_SECONDS * 2])

    def test_rate_limits_past_the_attempt_budget_are_unavailable(self):
        transport = Transport(*[(429, b"")] * ts.MAX_ATTEMPTS)
        with self.assertRaises(ts.Unavailable):
            ask(transport)
        self.assertEqual(len(transport.calls), ts.MAX_ATTEMPTS)

    def test_http_failures_never_echo_the_body_or_the_key(self):
        echo = json.dumps({"detail": "state was: secret report text " + KEY}).encode("utf-8")
        for status, error in ((401, ts.Unavailable), (422, ts.InvalidRequest), (500, ts.Unavailable)):
            with self.subTest(status=status), self.assertRaises(error) as caught:
                ask(Transport((status, echo)))
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn("secret report text", str(caught.exception))

    def test_an_answer_off_contract_is_invalid(self):
        broken = [b"not json", {"model": "jev-latest", "answers": answer()["answers"]},
                  {"model": MODEL, "answers": {}}, answer(p=1.5), answer(p=float("nan")),
                  {"model": MODEL, "answers": {"is_open": {"type": "choice", "noul": 0.5}}}]
        for reply in broken:
            with self.subTest(reply=reply), self.assertRaises(ts.InvalidResponse):
                ask(Transport((200, reply)))

    def test_a_choice_whose_distribution_does_not_sum_is_invalid(self):
        reply = {"model": MODEL, "answers": {"relation": {
            "type": "choice", "choice": "supported", "probabilities": {"supported": 0.8, "contradicted": 0.8},
            "confidence": 0.7}}}
        with self.assertRaises(ts.InvalidResponse):
            ask(Transport((200, reply)), CHOICE)

    def test_redirects_are_never_followed(self):
        request = urllib.request.Request(ts.ENDPOINT, headers={"Authorization": "Bearer " + KEY})
        self.assertIsNone(ts._NoRedirect().redirect_request(request, io.BytesIO(), 302, "Found",
                                                            http.client.HTTPMessage(), "https://elsewhere"))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
