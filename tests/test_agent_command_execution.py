import importlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent as agent_module
from agent import build_windows_command_launcher, resolve_agent_action
from app.agents.decision import build_decision_evidence, llm_action_decision
from app.agents.manager import AgentManager


class AgentCommandExecutionTests(unittest.TestCase):
    def test_build_windows_command_launcher_uses_elevation_for_netsh_commands(self):
        command = 'netsh int ip reset'
        launcher = build_windows_command_launcher(command, elevated=True)

        self.assertIn('Start-Process', launcher)
        self.assertIn('-Verb RunAs', launcher)
        self.assertIn(command, launcher)

    def test_resolve_agent_action_uses_local_platform_allowlist(self):
        self.assertEqual(
            resolve_agent_action('check_connectivity', {}, 'Windows'),
            'ping -n 4 1.1.1.1',
        )
        self.assertEqual(
            resolve_agent_action('flush_dns_cache', {}, 'Linux'),
            'resolvectl flush-caches',
        )

    def test_resolve_agent_action_rejects_unknown_ids_platforms_and_parameters(self):
        for action_id, parameters, os_name in (
            ('unknown_action', {}, 'Windows'),
            ('check_connectivity', {}, 'Plan9'),
            ('check_connectivity', {'host': 'untrusted.example'}, 'Windows'),
        ):
            with self.subTest(action_id=action_id, os_name=os_name):
                with self.assertRaises(ValueError):
                    resolve_agent_action(action_id, parameters, os_name)

    def test_interface_metrics_use_counter_delta_and_report_no_first_window(self):
        agent = agent_module.NetworkAgent.__new__(agent_module.NetworkAgent)
        agent._interface_counters = None
        agent._interface_sampled_at = None
        samples = [
            {'Ethernet': SimpleNamespace(bytes_sent=0, bytes_recv=0)},
            {'Ethernet': SimpleNamespace(bytes_sent=50_000_000, bytes_recv=100_000_000)},
        ]
        with patch.object(agent_module.time, 'monotonic', side_effect=[10.0, 20.0]), \
             patch.object(agent_module.psutil, 'net_io_counters', side_effect=samples), \
             patch.object(agent_module.psutil, 'net_if_stats', return_value={
                 'Ethernet': SimpleNamespace(isup=True, speed=100, mtu=1500, flags=''),
             }):
            self.assertEqual(agent._get_interface_metrics(), [])
            metrics = agent._get_interface_metrics()

        self.assertEqual(metrics, [{
            'name': 'Ethernet',
            'speed_mbps': 100,
            'rx_mbps': 80.0,
            'tx_mbps': 40.0,
            'utilization_pct': 80.0,
        }])

    def test_decision_evidence_omits_host_identity_and_free_text(self):
        evidence = build_decision_evidence({
            'type': 'high_latency',
            'score': -0.7,
            'details': {'latency': 240, 'domain': 'private.example'},
            'agent_context': {
                'dns_ok': False,
                'hostname': 'private-host',
                'ip': '192.0.2.1',
            },
        }, {'os': 'Windows', 'hostname': 'private-host'})

        serialized = json.dumps(evidence)
        self.assertIn('240', serialized)
        self.assertIn('dns_ok', serialized)
        self.assertNotIn('private-host', serialized)
        self.assertNotIn('192.0.2.1', serialized)
        self.assertNotIn('private.example', serialized)

    def test_llm_response_must_select_one_supplied_candidate(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size=-1):
                return json.dumps({
                    'choices': [{
                        'message': {'content': json.dumps({
                            'action_id': 'check_connectivity',
                            'confidence': 0.9,
                            'rationale': 'Check the path.',
                        })}
                    }]
                }).encode()

        env = {
            'ACTION_LLM_ENABLED': 'true',
            'OPENAI_API_KEY': 'test-secret',
            'OPENAI_MODEL': 'test-model',
        }
        candidates = [{'id': 'check_connectivity', 'name': 'Check connectivity'}]
        with patch.dict(os.environ, env, clear=False), \
             patch('app.agents.decision.urlopen', return_value=FakeResponse()) as open_request:
            result = llm_action_decision(candidates, {'anomaly_type': 'high_latency'})

        self.assertEqual(result['action_id'], 'check_connectivity')
        request = open_request.call_args.args[0]
        request_payload = json.loads(request.data.decode())
        self.assertIn('check_connectivity', request_payload['messages'][1]['content'])
        self.assertNotIn('test-secret', request.data.decode())

    def test_llm_failure_or_unapproved_action_returns_no_recommendation(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size=-1):
                return json.dumps({
                    'choices': [{
                        'message': {'content': json.dumps({
                            'action_id': 'shell_command',
                            'confidence': 0.99,
                            'rationale': 'not in the catalog',
                        })}
                    }]
                }).encode()

        env = {'ACTION_LLM_ENABLED': 'true', 'OPENAI_API_KEY': 'test-secret'}
        with patch.dict(os.environ, env, clear=False), \
             patch('app.agents.decision.urlopen', return_value=FakeResponse()):
            result = llm_action_decision(
                [{'id': 'check_connectivity', 'name': 'Check connectivity'}],
                {'anomaly_type': 'high_latency'},
            )
        self.assertIsNone(result)

        with patch.dict(os.environ, {'ACTION_LLM_ENABLED': 'true'}, clear=True):
            self.assertIsNone(llm_action_decision([], {}))

    def test_llm_rejects_remote_http_and_invalid_runtime_configuration_before_request(self):
        candidates = [{'id': 'check_connectivity', 'name': 'Check connectivity'}]
        invalid_configs = (
            {
                'ACTION_LLM_ENABLED': 'true',
                'OPENAI_API_KEY': 'test-secret',
                'OPENAI_BASE_URL': 'http://remote.example/v1/chat/completions',
            },
            {
                'ACTION_LLM_ENABLED': 'true',
                'OPENAI_API_KEY': 'test-secret',
                'ACTION_LLM_MIN_CONFIDENCE': 'nan',
            },
            {
                'ACTION_LLM_ENABLED': 'true',
                'OPENAI_API_KEY': 'test-secret',
                'ACTION_LLM_TIMEOUT_SECONDS': '100',
            },
        )
        for config in invalid_configs:
            with self.subTest(config=config), \
                 patch.dict(os.environ, config, clear=True), \
                 patch('app.agents.decision.urlopen') as open_request:
                self.assertIsNone(llm_action_decision(candidates, {}))
                open_request.assert_not_called()

    def test_loopback_provider_can_abstain_and_low_confidence_falls_back(self):
        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size=-1):
                return json.dumps({
                    'choices': [{'message': {'content': json.dumps(self.content)}}],
                }).encode()

        env = {
            'ACTION_LLM_ENABLED': 'true',
            'OPENAI_API_KEY': 'test-secret',
            'OPENAI_BASE_URL': 'http://127.0.0.1:8000/v1/chat/completions',
            'ACTION_LLM_MIN_CONFIDENCE': '0.75',
        }
        candidate = [{'id': 'check_connectivity', 'name': 'Check connectivity'}]
        for content in (
            {'action_id': None, 'confidence': 0.9, 'rationale': 'Abstain'},
            {'action_id': 'check_connectivity', 'confidence': 0.4, 'rationale': 'Unsure'},
        ):
            with self.subTest(content=content), \
                 patch.dict(os.environ, env, clear=True), \
                 patch('app.agents.decision.urlopen', return_value=FakeResponse(content)):
                self.assertIsNone(llm_action_decision(candidate, {}))

    def test_load_config_uses_agent_api_key_environment_variable(self):
        with patch.dict(os.environ, {'AGENT_API_KEY': 'shared-secret'}, clear=False):
            with tempfile.NamedTemporaryFile('w', delete=False) as handle:
                handle.write('{}')
                temp_path = handle.name

            reloaded_agent_module = importlib.reload(agent_module)
            config = reloaded_agent_module.load_config(temp_path, env=os.environ)
            self.assertEqual(config['api_key'], 'shared-secret')

            os.remove(temp_path)

    def test_register_agent_accepts_any_configured_api_key(self):
        with patch.dict(os.environ, {'API_KEY': 'primary-secret', 'AGENT_API_KEY': 'secondary-secret'}, clear=True):
            manager = AgentManager()
            accepted = manager.register_agent('sid-1', {
                'agent_id': 'agent-1',
                'api_key': 'secondary-secret',
                'hostname': 'test-host',
            })

        self.assertTrue(accepted)


if __name__ == '__main__':
    unittest.main()
