import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import handoff
import install


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(handoff, 'STATE_ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_reset_jitter_does_not_reset_attempts(self):
        event = {'session_id': 'a'}
        for reset in ('2030-01-01T00:00:00.729817Z', '2030-01-01T00:00:00.564816Z'):
            self.assertEqual(handoff.decide('claude', event, (99, reset))['decision'], 'block')
        self.assertFalse(handoff.decide('claude', event, (99, '2030-01-01T00:00:01Z'))['continue'])
        self.assertEqual(handoff.decide('claude', event, (99, '2030-01-08T00:00:00Z'))['decision'], 'block')

    def test_grok_inherited_hooks_do_not_probe_claude(self):
        with patch.dict(os.environ, {'GROK_HOOK_EVENT': 'Stop'}), patch.object(handoff, 'read_usage') as probe:
            self.assertEqual(handoff.hook('claude', {'session_id': 'a'}), {})
            probe.assert_not_called()

    def test_subagent_does_not_overwrite_parent(self):
        with patch.object(handoff, 'read_usage') as probe:
            self.assertEqual(handoff.hook('codex', {'session_id': 'a', 'agent_id': 'child'}), {})
            probe.assert_not_called()

    def test_ids_cannot_collide_by_sanitization(self):
        self.assertNotEqual(handoff.safe_id('a/b'), handoff.safe_id('a_b'))
        self.assertNotEqual(handoff.safe_id('a' * 129), handoff.safe_id('a' * 130))

    def test_busy_state_skips_without_waiting(self):
        with handoff.locked(handoff.state_path('codex', 'a')):
            self.assertEqual(handoff.decide('codex', {'session_id': 'a'}, (99, '2030-01-01T00:00:00Z')), {})

    def test_cached_usage_probes_once(self):
        with patch.object(handoff, 'read_usage', return_value=(99, '2030-01-01T00:00:00Z')) as probe:
            self.assertEqual(handoff.cached_usage('codex'), handoff.cached_usage('codex'))
            self.assertEqual(probe.call_count, 1)

    def test_invalid_collector_values(self):
        import subprocess
        for percent, reset in [('NaN', '2030-01-01T00:00:00Z'), (-1, '2030-01-01T00:00:00Z'), (.99, 'bad'), (.99, '2000-01-01T00:00:00Z')]:
            output = json.dumps({'limits': [{'label': 'Weekly (7-day)', 'percent': percent, 'resetsAt': reset}]})
            with patch.object(handoff.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)):
                self.assertIsNone(handoff.read_usage('codex'))

    def test_installs_without_existing_hooks_and_is_idempotent(self):
        with patch.object(install, 'HOME', self.root), patch.object(install, 'STATE_ROOT', self.root / 'state'):
            install.install_codex()
            path = self.root / '.claude/settings.json'
            install.install_combined('claude', path)
            before = path.read_text()
            install.install_combined('claude', path)
            install.install_codex()
            self.assertEqual(before, path.read_text())
            self.assertEqual(len(json.loads((self.root / '.codex/hooks.json').read_text())['hooks']['Stop']), 1)
            self.assertFalse((self.root / 'state/fallbacks.json').exists())

    def test_installer_wraps_only_keep_going(self):
        path = self.root / 'settings.json'
        unrelated = {'type': 'command', 'command': 'echo unrelated'}
        path.write_text(json.dumps({'other': True, 'hooks': {'Stop': [{'hooks': [unrelated, {'type': 'command', 'command': 'node keep-going.mjs claude'}]}]}}))
        with patch.object(install, 'STATE_ROOT', self.root / 'state'):
            install.install_combined('claude', path)
        data = json.loads(path.read_text())
        self.assertTrue(data['other'])
        self.assertEqual(data['hooks']['Stop'][0]['hooks'][0], unrelated)
        self.assertEqual(json.loads((self.root / 'state/fallbacks.json').read_text())['claude'], 'node keep-going.mjs claude')
