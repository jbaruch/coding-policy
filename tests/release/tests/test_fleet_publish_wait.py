"""Enqueue waiting preserves uncertainty and never invents a selection."""

from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[3] / "skills/release"
sys.path.insert(0, str(SOURCE))
import fleet_publish_wait as waiting  # noqa: E402 - co-shipped provider.


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        self.sleeps.append(seconds)


class WaitTests(unittest.TestCase):
    def test_enqueue_latency_ends_on_positive_selection(self):
        clock = Clock()
        calls = []
        selection = {"database_id": 123, "confirmed": False}

        def lookup(*arguments, **keywords):
            calls.append((arguments, keywords))
            if len(calls) <= 2:
                raise waiting.CentralPublishHold("not_found")
            return selection

        result = waiting.wait(
            "jbaruch/fixture",
            "a" * 40,
            lookup=lookup,
            clock=clock.now,
            sleep=clock.sleep,
        )
        self.assertIs(result, selection)
        self.assertEqual(clock.sleeps, [waiting.INTERVAL_SECONDS] * 2)
        self.assertEqual(len(calls), 3)

    def test_uncertain_evidence_stops_without_retry(self):
        for kind in [
            "ambiguous",
            "expired",
            "unavailable",
            "unknown_producer",
            "claim_binding",
        ]:
            clock = Clock()

            def lookup(*_arguments, **_keywords):
                raise waiting.CentralPublishHold(kind)

            with self.assertRaises(waiting.CentralPublishHold) as error:
                waiting.wait(
                    "jbaruch/fixture",
                    "a" * 40,
                    lookup=lookup,
                    clock=clock.now,
                    sleep=clock.sleep,
                )
            self.assertEqual(error.exception.kind, kind)
            self.assertEqual(clock.sleeps, [])

    def test_budget_exhaustion_returns_no_invented_run_id(self):
        clock = Clock()

        def lookup(*_arguments, **_keywords):
            raise waiting.CentralPublishHold("pending")

        with self.assertRaises(waiting.CentralPublishHold):
            waiting.wait(
                "jbaruch/fixture",
                "a" * 40,
                lookup=lookup,
                clock=clock.now,
                sleep=clock.sleep,
            )
        self.assertEqual(clock.elapsed, waiting.BUDGET_SECONDS)

    def test_explicit_recovery_run_is_retained_on_every_snapshot(self):
        calls = []

        def lookup(*_arguments, **keywords):
            calls.append(keywords)
            return {"database_id": 456}

        waiting.wait("jbaruch/fixture", "a" * 40, run_id="456", lookup=lookup)
        self.assertEqual(calls, [{"run_id": "456"}])

    def test_slow_positive_lookup_cannot_return_an_id_after_deadline(self):
        for elapsed in (waiting.BUDGET_SECONDS, waiting.BUDGET_SECONDS + 1):
            clock = Clock()
            calls = []

            def lookup(api, *_args, **_kwargs):
                calls.append(api)
                self.assertEqual(api._deadline, waiting.BUDGET_SECONDS)
                self.assertEqual(api._clock(), 0)
                clock.elapsed = elapsed
                return {"database_id": 123, "confirmed": False}

            with (
                self.subTest(elapsed=elapsed),
                self.assertRaises(waiting.CentralPublishHold) as refused,
            ):
                waiting.wait(
                    "jbaruch/fixture",
                    "a" * 40,
                    lookup=lookup,
                    clock=clock.now,
                    sleep=clock.sleep,
                )
            self.assertEqual(refused.exception.kind, "pending")
            self.assertEqual(len(calls), 1)
            self.assertEqual(clock.sleeps, [])

    def test_each_snapshot_shares_deadline_and_cannot_start_at_expiry(self):
        clock = Clock()
        clients = []

        def lookup(api, *_args, **_kwargs):
            clients.append(api)
            self.assertEqual(api._deadline, waiting.BUDGET_SECONDS)
            clock.elapsed = waiting.BUDGET_SECONDS - 2
            raise waiting.CentralPublishHold("pending")

        with self.assertRaises(waiting.CentralPublishHold):
            waiting.wait(
                "jbaruch/fixture",
                "a" * 40,
                lookup=lookup,
                clock=clock.now,
                sleep=clock.sleep,
            )
        self.assertEqual(len(clients), 1)
        self.assertEqual(clock.sleeps, [2])
        self.assertEqual(clock.elapsed, waiting.BUDGET_SECONDS)

    def test_positive_selection_inside_fixed_deadline_remains_usable(self):
        clock = Clock()
        selected = {"database_id": 123, "confirmed": False}

        def lookup(api, *_args, **_kwargs):
            self.assertEqual(api._deadline, waiting.BUDGET_SECONDS)
            clock.elapsed = waiting.BUDGET_SECONDS - 0.001
            return selected

        self.assertIs(
            waiting.wait(
                "jbaruch/fixture",
                "a" * 40,
                lookup=lookup,
                clock=clock.now,
                sleep=clock.sleep,
            ),
            selected,
        )
        self.assertEqual(clock.sleeps, [])


if __name__ == "__main__":
    unittest.main()
