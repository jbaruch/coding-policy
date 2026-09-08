"""A stale Herdr ID never becomes native continuity or review approval."""

import copy
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teamlead import cli, recovery, report_delivery as delivery, state
from teamlead.assign import assignment_text
from tests import test_assign as dispatch_fixture
from tests import test_report_delivery as native_fixture
from teamlead.errors import UsageError
from tests.test_report_delivery import AT, PANE, SESSION, encode, grok_row, grok_rows, identity
from teamlead.herdr import HerdrClient
from tests.fakes import FakeRunner, ScriptedReads, agent_json


class StaleGrokDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = native_fixture.NativeDeliveryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.document, self.data = self.case.recovery_fixture()
        self.assignment = self.document['assignments'][0]
        self.dispatch = self.document['recovery']['dispatches'][0]
        self.observed = {**identity('grok'), 'value': 'old-before-new'}
        for row in (self.assignment, self.dispatch['result']):
            row.update(context_session=None, cleared=True, clear_reason='automatic')
        self.dispatch['observed_before'] = {'pane_id': PANE, 'context_session': {'pane_id': PANE, **self.observed}}
        self.dispatch['context_before_send'] = {'cleared': True, 'clear_reason': 'automatic', 'context_session': None}
        self.prompt = assignment_text('judge', self.dispatch['common'], self.dispatch['brief'])
        self.rows = [grok_row({'sessionUpdate': 'hook_execution', 'event_name': 'session_start'}),
                     grok_row({'sessionUpdate': 'hook_execution', 'event_name': 'user_prompt_submit', 'prompt_id': 'prompt-1'})]
        self.rows += grok_rows(self.case.marker)
        self.rows[2]['params']['update']['content']['text'] = self.prompt
        Path(self.data['pane']).write_text(json.dumps({'result': {'pane': {**self.case.pane, 'agent_session': self.observed}}}))
        Path(self.data['visible']).write_text('     ' + self.case.marker)
        Path(self.data['source']).write_text(encode(self.rows))
        plan = self.case.tmp / 'plan.json'
        plan.write_text(json.dumps({'assignments': {'judge': 'worker'}}))
        self.data['plan'] = str(plan)
        _, self.dispatch['fingerprint'] = recovery.dispatch_identity(
            self.dispatch['task'], 'judge', 'worker', None,
            {'common': self.dispatch['common'], 'judge': self.dispatch['brief']},
            options={'task': self.dispatch['task'], 'fix_round': None, 'plan': None,
                     'work': None, 'rounds': {}, 'retain_context': False, 'no_clear': False})

    def recover(self):
        return delivery.recover(self.document['recovery'], self.document['assignments'], self.data, AT)

    def test_known_stale_clear_keeps_observation_and_proves_source_separately(self):
        old = copy.deepcopy(self.document)
        record = self.recover()
        self.assertEqual(record['schema_version'], 2)
        self.assertEqual(record['native_session'], self.observed)
        self.assertEqual(record['source_session'], {'agent': 'grok', 'kind': 'id', 'value': SESSION})
        self.assertEqual(record['basis'], 'archived_grok_clear_source')
        self.assertIsNone(record['native_session_proof'])
        self.assertFalse(record['grants_review_approval'])
        self.assertEqual(self.document['assignments'], old['assignments'])
        for key in old['recovery']:
            if key not in ('delivery_recoveries', 'events'):
                self.assertEqual(self.document['recovery'][key], old['recovery'][key])
        recovery.validate_store(self.document['recovery'], self.document['assignments'])
        self.assertEqual(self.recover(), record)

    def test_public_owner_recovery_replays_without_worker_input_or_byte_rewrites(self):
        ledger_path, record_path = self.case.tmp / 'state.json', self.case.tmp / 'record.json'
        # Exercise the owner migration as well as writing a schema-2 receipt.
        self.document['recovery']['schema_version'] = 3
        state.save_state(ledger_path, self.document)
        record_path.write_text(json.dumps(self.data))
        original_bytes = {key: Path(value).read_bytes() for key, value in self.data.items() if key not in ('id', 'dispatch')}
        args = ['recover-report', '--state', str(ledger_path), '--record', str(record_path), '--now', AT]
        runner = FakeRunner()
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(cli.main(args, stdout=out, stderr=err, client=HerdrClient(runner=runner)), 0, err.getvalue())
        saved = ledger_path.read_bytes()
        self.assertEqual(json.loads(saved)['recovery']['schema_version'], 4)
        self.assertEqual(cli.main(args, stdout=io.StringIO(), stderr=io.StringIO()), 0)
        self.assertEqual(ledger_path.read_bytes(), saved)
        self.assertEqual(runner.calls, [])
        for key, before in original_bytes.items():
            self.assertEqual(Path(self.data[key]).read_bytes(), before)
        Path(self.data['report']).write_text('changed report bytes')
        self.assertEqual(cli.main(args, stdout=io.StringIO(), stderr=io.StringIO()), 1)
        self.assertEqual(ledger_path.read_bytes(), saved)

    def test_dispatch_brief_common_and_plan_must_match_original_fingerprint(self):
        for key in ('brief', 'common', 'plan'):
            path = Path(self.data[key] if key == 'plan' else self.dispatch[key])
            body = path.read_text()
            changed = json.dumps({'assignments': {'judge': 'worker'}, 'rounds': {'judge': {'type': 'build'}}}) if key == 'plan' else body + '\nChanged scope\n'
            path.write_text(changed)
            before = copy.deepcopy(self.document)
            with self.subTest(key=key), self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
                self.recover()
            self.assertEqual(self.document, before)
            path.write_text(body)

    def test_reused_prompt_paths_and_changed_plan_task_refuse(self):
        other = copy.deepcopy(self.dispatch)
        other['id'] = 'other-dispatch'
        self.document['recovery']['dispatches'].append(other)
        before = copy.deepcopy(self.document)
        with self.assertRaisesRegex(UsageError, 'grok_dispatch_ambiguous'):
            self.recover()
        self.assertEqual(self.document, before)
        self.document['recovery']['dispatches'].pop()
        plan = Path(self.data['plan'])
        plan.write_text(json.dumps({'assignments': {'judge': 'worker'}, 'task_context': {'task': 'another-task'}}))
        with self.assertRaisesRegex(UsageError, 'grok_dispatch_unbound'):
            self.recover()

    def test_source_ambiguities_and_failed_or_incomplete_turns_refuse(self):
        candidates = [self.rows[1:], self.rows[:-1], self.rows + self.rows,
                      self.rows[:3] + self.rows[2:],
                      self.rows[:-1] + [grok_row({'sessionUpdate': 'turn_failed'})] + self.rows[-1:],
                      self.rows[:-1] + [grok_row({'sessionUpdate': 'turn_cancelled'})] + self.rows[-1:]]
        for field, value in [('sessionId', 'other-native'), ('sessionId', []), ('_meta', [])]:
            rows = copy.deepcopy(self.rows)
            rows[-1]['params'][field] = value
            candidates.append(rows)
        for update in ({'prompt_id': 'other-prompt'}, {'stop_reason': 'refusal'}):
            rows = copy.deepcopy(self.rows)
            rows[-1]['params']['update'].update(update)
            candidates.append(rows)
        rows = copy.deepcopy(self.rows)
        rows[2]['params']['update']['content']['text'] = 'another original dispatch'
        candidates.append(rows)
        rows = copy.deepcopy(self.rows)
        rows[3]['params']['_meta']['promptId'] = 'another-prompt'
        candidates.append(rows)
        for rows in candidates:
            Path(self.data['source']).write_text(encode(rows))
            before = copy.deepcopy(self.document)
            with self.subTest(rows=rows), self.assertRaisesRegex(UsageError, 'grok_source_ambiguous'):
                self.recover()
            self.assertEqual(self.document, before)

    def test_authored_or_wrapped_markers_do_not_gain_stale_identity_exception(self):
        for final in ('- ' + self.case.marker, '     ' + self.case.marker, '```\n' + self.case.marker,
                      '> example\n' + self.case.marker, self.case.marker + '\nmore output'):
            rows = copy.deepcopy(self.rows)
            rows[3]['params']['update']['content']['text'] = final
            Path(self.data['source']).write_text(encode(rows))
            with self.subTest(final=final), self.assertRaisesRegex(UsageError, 'bare final marker'):
                self.recover()
        Path(self.data['source']).write_text(encode(self.rows))
        Path(self.data['visible']).write_text('     REPORT: \n' + str(self.case.report))
        with self.assertRaisesRegex(UsageError, 'bare final marker'):
            self.recover()

    def test_known_continuity_and_unproven_clears_cannot_be_replaced(self):
        for target, key, value in ((self.assignment, 'context_session', {'pane_id': PANE, **self.observed}),
                (self.dispatch['context_before_send'], 'cleared', False),
                (self.dispatch['result'], 'clear_reason', 'hand'),
                (self.dispatch['observed_before'], 'pane_id', 'another-pane'),
                (self.dispatch['observed_before'], 'context_session', None)):
            original = target[key]
            target[key] = value
            before = copy.deepcopy(self.document)
            with self.subTest(key=key), self.assertRaisesRegex(UsageError, 'grok_clear_identity_unproven'):
                self.recover()
            self.assertEqual(self.document, before)
            target[key] = original
        negative = json.loads(Path(self.data['wait_receipt']).read_text())
        negative['reason'] = 'terminal_provider_refusal'
        Path(self.data['wait_receipt']).write_text(json.dumps(negative))
        with self.assertRaisesRegex(UsageError, 'refusals are not delivery'):
            self.recover()

    def test_owner_reader_rejects_forged_separate_identity_and_approval(self):
        record = self.recover()
        for key, value in (('source_session', {'agent': 'grok', 'kind': 'id', 'value': self.observed['value']}),
                           ('native_session', identity('codex')), ('native_session_proof', identity('grok')),
                           ('grants_review_approval', True), ('basis', 'archived_native_final_source')):
            original = record[key]
            record[key] = value
            with self.subTest(key=key), self.assertRaises(UsageError):
                recovery.validate_store(self.document['recovery'], self.document['assignments'])
            record[key] = original

    def test_replay_rejects_a_changed_source_identity_even_with_same_receipts(self):
        record = self.recover()
        record['source_session']['value'] = 'unproven-different-native'
        with self.assertRaisesRegex(UsageError, 'different evidence'):
            self.recover()

    def test_migration_preserves_original_receipts_and_refuses_newer_embedded_records(self):
        legacy = native_fixture.NativeDeliveryTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        document, data = legacy.recovery_fixture()
        original_receipt = delivery.recover(document['recovery'], document['assignments'], data, AT)
        document['recovery']['schema_version'] = 3
        before = copy.deepcopy(document)
        self.assertTrue(recovery.migrate_store(document['recovery']))
        self.assertEqual(document['recovery'], {**before['recovery'], 'schema_version': 4})
        self.assertEqual(document['recovery']['delivery_recoveries'], [original_receipt])
        record = self.recover()
        self.document['recovery']['schema_version'] = 3
        with self.assertRaisesRegex(UsageError, 'unowned newer delivery'):
            recovery.migrate_store(self.document['recovery'])
        self.assertEqual(record['schema_version'], 2)

    def test_native_clock_and_viewport_scrollbar_require_bare_source(self):
        row = '     ' + self.case.marker + '    1:55 AM   █'
        Path(self.data['visible']).write_text(row)
        self.assertTrue(self.recover()['found'])
        self.assertIsNone(delivery.decorated_row(row + '█', 'grok', str(self.case.report)))
        self.assertIsNone(delivery.decorated_row(row + ' extra', 'grok', str(self.case.report)))
        self.assertFalse(delivery.bare_final(self.case.marker + '   █', str(self.case.report)))

    def test_grok_source_storage_honors_isolated_native_home(self):
        home = self.case.tmp / 'private-native-home'
        with patch.dict(os.environ, {'GROK_HOME': str(home)}):
            self.assertEqual(delivery.source_root('grok'), home / 'sessions')

    def test_report_changed_during_verification_cannot_get_a_receipt(self):
        real_receipt = recovery.receipt
        def change_report(path):
            result = real_receipt(path)
            if path == self.data['plan']:
                Path(self.data['report']).write_text('changed during verification')
            return result
        before = copy.deepcopy(self.document)
        with patch.object(recovery, 'receipt', side_effect=change_report):
            with self.assertRaisesRegex(UsageError, 'grok_evidence_changed'):
                self.recover()
        self.assertEqual(self.document, before)

    def test_fresh_developer_clear_never_records_a_late_stale_herdr_id(self):
        case = dispatch_fixture.ApplyTest()
        case.setUp()
        self.addCleanup(case.tearDown)
        runner = dispatch_fixture.runner_with({'grok': 'idle'})
        stale = agent_json('grok', 'idle', 'w4:p1', session_id=self.observed['value'])
        runner.responses['agent get grok'] = ScriptedReads([agent_json('grok', 'idle', 'w4:p1'), stale, stale])
        warnings = []
        result = dispatch_fixture.apply(HerdrClient(runner=runner), {'developer': 'grok'},
            dispatch_fixture.BY_NAME, case.paths, AT, task='fresh-grok', warn=warnings.append)
        row = result['applied'][0]
        self.assertEqual(row['status'], 'applied')
        self.assertTrue(row['cleared'])
        self.assertIsNone(row['context_session'])
        self.assertTrue(any('stale-ID recovery is unavailable' in message
                            and 'record the report as unavailable and notify the operator' in message
                            and 'Keep review/release gates unsatisfied' in message for message in warnings))
        self.assertEqual(sum(call[1:3] == ['agent', 'prompt'] for call in runner.calls), 1)


if __name__ == '__main__':
    unittest.main()
