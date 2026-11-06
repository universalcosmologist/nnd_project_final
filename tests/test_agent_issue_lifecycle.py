"""Regression tests for agent command dispatch and issue state synchronization."""
import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.agents import handlers
from app.agents.manager import AgentManager, agent_manager
import app.models.supervised_detector as supervised_module
from app.models.supervised_detector import SupervisedAnomalyDetector
import app.healing.resolver as resolver_module
from app.healing.ground_truth import MIN_CALIBRATION_SAMPLES
from app.services import network_resolver


class AgentIssueLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.original_issues = copy.deepcopy(network_resolver.active_issues)
        self.original_history = copy.deepcopy(network_resolver.resolution_history)
        self.original_agents = copy.deepcopy(agent_manager.agents)
        self.original_sid_map = copy.deepcopy(agent_manager.agent_sid_map)
        self.original_metrics = copy.deepcopy(agent_manager.metrics_history)
        self.original_metrics_history_file = agent_manager.metrics_history_file
        self.metrics_history_directory = tempfile.TemporaryDirectory()
        agent_manager.metrics_history_file = os.path.join(self.metrics_history_directory.name, 'metrics.json')
        self.original_supervised_detector = handlers.detector.supervised_detector
        handlers.detector.supervised_detector = SupervisedAnomalyDetector(
            labels_file=os.path.join(self.metrics_history_directory.name, 'labels.json'),
            candidate_model_file=os.path.join(self.metrics_history_directory.name, 'candidate.pkl'),
        )
        self.original_dispatches = copy.deepcopy(agent_manager.last_command_dispatch)
        handlers.command_to_issue.clear()
        network_resolver.active_issues = {}
        network_resolver.resolution_history = []
        agent_manager.agents = {}
        agent_manager.agent_sid_map = {}
        agent_manager.metrics_history = {}
        agent_manager.last_command_dispatch = {}

    def tearDown(self):
        network_resolver.active_issues = self.original_issues
        network_resolver.resolution_history = self.original_history
        network_resolver._save_active_issues()
        network_resolver._save_resolution_history()
        agent_manager.agents = self.original_agents
        agent_manager.agent_sid_map = self.original_sid_map
        agent_manager.metrics_history = self.original_metrics
        agent_manager.metrics_history_file = self.original_metrics_history_file
        handlers.detector.supervised_detector = self.original_supervised_detector
        self.metrics_history_directory.cleanup()
        agent_manager.last_command_dispatch = self.original_dispatches
        handlers.command_to_issue.clear()

    def _register_agent(self):
        self.assertTrue(agent_manager.register_agent('sid-1', {
            'agent_id': 'agent-1',
            'api_key': 'your-secret-key',
            'hostname': 'test-agent',
            'os': 'Windows',
        }))

    def _create_agent_issue(self):
        return network_resolver.handle_anomaly({
            'type': 'general_anomaly',
            'score': -0.5,
            'details': {'features': {'latency': 300, 'bandwidth_usage': 1, 'packet_loss': 0}},
            'detected_at': '2026-07-27T00:00:00',
            'agent_id': 'agent-1',
            'agent_context': {
                'metric_targets': {
                    'latency_ms': 'icmp:8.8.8.8',
                    'packet_loss_pct': 'icmp:8.8.8.8',
                    'bandwidth_mbps': 'device:agent-1',
                },
            },
        })

    def _approved_profiles(self, bounds):
        return {'agent-1': {'metrics': {
            metric_name: {
                'target_id': target_id,
                'unit': 'metric-unit',
                'approved': True,
                'approved_by': 'test-operator',
                'approved_at': '2026-11-05T00:00:00',
                'version': 1,
                'sample_count': MIN_CALIBRATION_SAMPLES,
                'upper_bound': upper_bound,
            }
            for metric_name, (target_id, upper_bound) in bounds.items()
        }}}

    def _metric_sample(self, **metrics):
        return {
            'agent_id': 'agent-1',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            **metrics,
        }

    def test_issue_without_agent_owner_is_rejected(self):
        issue_id = network_resolver.handle_anomaly({
            'type': 'high_latency',
            'details': {'latency': 250, 'threshold': 200},
            'detected_at': '2026-07-27T00:00:00',
        })

        self.assertIsNone(issue_id)
        self.assertEqual(network_resolver.active_issues, {})

    def test_pre_action_healthy_detection_is_recorded_without_dispatch(self):
        self._register_agent()
        issue_id = network_resolver.handle_anomaly({
            'type': 'high_latency',
            'details': {'latency': 20, 'threshold': 200},
            'detected_at': datetime.now().isoformat(),
            'agent_id': 'agent-1',
            'agent_context': {'metric_targets': {'latency_ms': 'icmp:8.8.8.8'}},
            'metric_snapshot': {'latency_ms': 20},
        })
        profiles = self._approved_profiles({'latency_ms': ('icmp:8.8.8.8', 30)})
        detection_id, _ = handlers.detector.supervised_detector.observe(
            {
                'agent_id': 'agent-1',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'metric_targets': {'latency_ms': 'icmp:8.8.8.8'},
                'latency_ms': 20,
                'packet_loss_pct': 0,
                'bandwidth_mbps': 2,
            },
            isolation_prediction=-1,
            isolation_score=-0.1,
        )
        network_resolver.active_issues[issue_id]['anomaly']['detection_id'] = detection_id

        with (
            patch.object(resolver_module, 'load_calibration_profiles', return_value=profiles),
            patch.object(supervised_module, 'load_calibration_profiles', return_value=profiles),
            patch.object(handlers.socketio, 'emit') as emit,
        ):
            result = handlers.dispatch_issue_to_agent(issue_id)

        self.assertTrue(result['false_positive'])
        self.assertEqual(network_resolver.resolution_history[-1]['verification_status'], 'false_positive')
        self.assertNotIn(issue_id, network_resolver.active_issues)
        self.assertEqual(handlers.detector.supervised_detector.labels[-1]['detection_id'], detection_id)
        self.assertFalse(handlers.detector.supervised_detector.labels[-1]['actual_anomaly'])
        emit.assert_not_called()

    def test_same_latency_measurement_uses_each_agents_own_profile(self):
        profiles = {
            agent_id: {'metrics': {'latency_ms': {
                'target_id': 'icmp:edge.example',
                'unit': 'ms',
                'approved': True,
                'approved_by': 'test-operator',
                'approved_at': '2026-11-05T00:00:00',
                'version': 1,
                'sample_count': MIN_CALIBRATION_SAMPLES,
                'upper_bound': upper_bound,
            }}}
            for agent_id, upper_bound in (('agent-low-latency', 100), ('agent-high-latency', 200))
        }
        sample = {
            'agent_id': 'agent-high-latency',
            'evidence_valid': True,
            'latency_ms': 150,
            'metric_targets': {'latency_ms': 'icmp:edge.example'},
        }
        issue = {
            'anomaly': {
                'type': 'high_latency',
                'agent_id': 'agent-high-latency',
                'agent_context': {'metric_targets': {'latency_ms': 'icmp:edge.example'}},
            },
        }

        with patch.object(resolver_module, 'load_calibration_profiles', return_value=profiles):
            self.assertTrue(network_resolver._evaluate_agent_issue(issue, [sample]))
            issue['anomaly']['agent_id'] = 'agent-low-latency'
            sample['agent_id'] = 'agent-low-latency'
            self.assertFalse(network_resolver._evaluate_agent_issue(issue, [sample]))

    def test_wrong_target_verification_samples_are_inconclusive(self):
        self._register_agent()
        issue_id = network_resolver.handle_anomaly({
            'type': 'high_latency',
            'details': {'latency': 250, 'threshold': 200},
            'detected_at': datetime.now().isoformat(),
            'agent_id': 'agent-1',
            'agent_context': {'metric_targets': {'latency_ms': 'icmp:expected.example'}},
        })
        network_resolver.mark_command_dispatched(issue_id, 'command-1', 'action:check')
        network_resolver.update_issue_status(issue_id, True, 'completed', command_id='command-1')

        with patch.object(resolver_module, 'load_calibration_profiles', return_value=self._approved_profiles({
            'latency_ms': ('icmp:expected.example', 100),
        })):
            for _ in range(5):
                result = network_resolver.add_agent_verification_sample(issue_id, self._metric_sample(
                    metric_targets={'latency_ms': 'icmp:other.example'},
                    latency_ms=50,
                ))

        self.assertEqual(result, 'inconclusive')
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'verification_inconclusive')

    def test_stale_agent_samples_cannot_verify_recovery(self):
        self._register_agent()
        issue_id = network_resolver.handle_anomaly({
            'type': 'high_latency',
            'details': {'latency': 250, 'threshold': 200},
            'detected_at': datetime.now().isoformat(),
            'agent_id': 'agent-1',
            'agent_context': {'metric_targets': {'latency_ms': 'icmp:edge.example'}},
        })
        network_resolver.mark_command_dispatched(issue_id, 'command-1', 'action:check')
        network_resolver.update_issue_status(issue_id, True, 'completed', command_id='command-1')

        with patch.object(resolver_module, 'load_calibration_profiles', return_value=self._approved_profiles({
            'latency_ms': ('icmp:edge.example', 100),
        })):
            for index in range(5):
                stale_sample = self._metric_sample(
                    metric_targets={'latency_ms': 'icmp:edge.example'},
                    latency_ms=50,
                )
                stale_sample['timestamp'] = (datetime.now() - timedelta(seconds=180 - index)).isoformat()
                result = network_resolver.add_agent_verification_sample(issue_id, stale_sample)

        self.assertEqual(result, 'inconclusive')
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'verification_inconclusive')

    def test_unregistered_agent_metrics_are_ignored(self):
        with patch.object(handlers.detector, 'detect_anomalies') as detect:
            handlers.handle_agent_metrics({
                'agent_id': 'unknown-agent',
                'latency_ms': 250,
                'packet_loss_pct': 10,
            })

        detect.assert_not_called()
        self.assertEqual(network_resolver.active_issues, {})

    def test_agent_metric_history_survives_restart_and_re_registration(self):
        manager = AgentManager(metrics_history_file=agent_manager.metrics_history_file)
        self.assertTrue(manager.register_agent('sid-1', {
            'agent_id': 'agent-1',
            'api_key': 'your-secret-key',
        }))
        self.assertTrue(manager.update_metrics('agent-1', {
            'agent_id': 'agent-1',
            'timestamp': datetime.now().isoformat(),
            'latency_ms': 17,
        }))

        restarted_manager = AgentManager(metrics_history_file=agent_manager.metrics_history_file)
        self.assertEqual(restarted_manager.metrics_history['agent-1'][0]['latency_ms'], 17)
        self.assertTrue(restarted_manager.register_agent('sid-2', {
            'agent_id': 'agent-1',
            'api_key': 'your-secret-key',
        }))
        self.assertEqual(restarted_manager.metrics_history['agent-1'][0]['latency_ms'], 17)

    def test_legacy_unowned_issue_cannot_auto_resolve(self):
        issue = {
            'id': 'legacy-issue',
            'status': 'new',
            'anomaly': {'type': 'high_latency'},
            'resolution_attempts': 0,
            'resolution_actions': [],
        }
        network_resolver.active_issues[issue['id']] = issue

        with patch('app.healing.resolver.subprocess.run') as run_command:
            result = network_resolver.auto_resolve(issue['id'])

        self.assertFalse(result['success'])
        self.assertIn(issue['id'], network_resolver.active_issues)
        self.assertNotIn(issue, network_resolver.get_active_issues())
        run_command.assert_not_called()

    def test_issue_waits_for_agent_result_before_becoming_resolved(self):
        self._register_agent()
        issue_id = self._create_agent_issue()
        profiles = self._approved_profiles({
            'latency_ms': ('icmp:8.8.8.8', 200),
            'packet_loss_pct': ('icmp:8.8.8.8', 5),
            'bandwidth_mbps': ('device:agent-1', 20),
        })
        detection_id, _ = handlers.detector.supervised_detector.observe({
            'agent_id': 'agent-1',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'metric_targets': {
                'latency_ms': 'icmp:8.8.8.8',
                'packet_loss_pct': 'icmp:8.8.8.8',
                'bandwidth_mbps': 'device:agent-1',
            },
            'latency_ms': 300,
            'packet_loss_pct': 0,
            'bandwidth_mbps': 1,
        }, isolation_prediction=-1, isolation_score=-0.5)
        network_resolver.active_issues[issue_id]['anomaly']['detection_id'] = detection_id

        with patch.object(resolver_module, 'load_calibration_profiles', return_value=profiles), \
             patch.object(supervised_module, 'load_calibration_profiles', return_value=profiles), \
             patch.object(handlers.socketio, 'emit') as emit:
            result = handlers.dispatch_issue_to_agent(issue_id)

        self.assertTrue(result['success'])
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'awaiting_agent')
        self.assertEqual(network_resolver.resolution_history, [])
        self.assertTrue(any(
            call.args and call.args[0] == 'run_command' and call.kwargs.get('namespace') == '/agent'
            for call in emit.call_args_list
        ))
        command_event = next(
            call for call in emit.call_args_list
            if call.args and call.args[0] == 'run_command'
        )
        self.assertEqual(command_event.args[1]['action_id'], 'check_connectivity')
        self.assertEqual(command_event.args[1]['parameters'], {})
        self.assertNotIn('command', command_event.args[1])
        self.assertEqual(
            network_resolver.active_issues[issue_id]['resolution_decision']['action_id'],
            'check_connectivity',
        )
        baseline = network_resolver.active_issues[issue_id]['verification']['baseline']
        self.assertEqual(baseline['anomaly_type'], 'general_anomaly')
        self.assertEqual(baseline['model_confidence'], -0.5)
        self.assertEqual(baseline['agent_id'], 'agent-1')
        self.assertEqual(baseline['chosen_action']['action_id'], 'check_connectivity')
        self.assertEqual(baseline['target_ids']['latency_ms'], 'icmp:8.8.8.8')
        self.assertIn('captured_at', baseline)
        self.assertIn('latency', baseline['anomaly_details']['features'])

        network_resolver.update_issue_status(issue_id, True, 'completed')
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'awaiting_verification')
        with patch.object(handlers.detector, 'detect_anomalies', return_value=[]):
            with patch.object(resolver_module, 'load_calibration_profiles', return_value=self._approved_profiles({
                'latency_ms': ('icmp:8.8.8.8', 200),
                'packet_loss_pct': ('icmp:8.8.8.8', 5),
                'bandwidth_mbps': ('device:agent-1', 20),
            })):
                for _ in range(5):
                    handlers.handle_agent_metrics({
                        'agent_id': 'agent-1',
                        'timestamp': datetime.now().isoformat(),
                        'latency_ms': 150,
                        'packet_loss_pct': 0,
                        'bandwidth_mbps': 8,
                        'metric_targets': {
                            'latency_ms': 'icmp:8.8.8.8',
                            'packet_loss_pct': 'icmp:8.8.8.8',
                            'bandwidth_mbps': 'device:agent-1',
                        },
                        'connection_ok': True,
                        'dns_ok': True,
                    })
        self.assertNotIn(issue_id, network_resolver.active_issues)
        self.assertTrue(network_resolver.resolution_history[-1]['resolution_success'])
        self.assertEqual(network_resolver.resolution_history[-1]['verification_status'], 'verified')
        self.assertEqual(len(network_resolver.resolution_history[-1]['verification_samples']), 5)
        positive_labels = [
            label for label in handlers.detector.supervised_detector.labels
            if label['detection_id'] == detection_id
        ]
        self.assertEqual(len(positive_labels), 1)
        self.assertTrue(positive_labels[0]['actual_anomaly'])

    def test_issue_in_verification_cannot_be_redispatched(self):
        self._register_agent()
        issue_id = self._create_agent_issue()

        with patch.object(handlers.socketio, 'emit') as emit:
            self.assertTrue(handlers.dispatch_issue_to_agent(issue_id)['success'])
            retry = handlers.dispatch_issue_to_agent(issue_id)

        self.assertFalse(retry['success'])
        self.assertIn('cannot be dispatched', retry['message'])
        command_events = [
            call for call in emit.call_args_list
            if call.args and call.args[0] == 'run_command'
        ]
        self.assertEqual(len(command_events), 1)

    def test_issue_is_not_resolved_when_followup_metrics_do_not_improve(self):
        self._register_agent()
        issue_id = self._create_agent_issue()
        with patch.object(handlers.socketio, 'emit'):
            self.assertTrue(handlers.dispatch_issue_to_agent(issue_id)['success'])
        network_resolver.update_issue_status(issue_id, True, 'completed')

        with patch.object(resolver_module, 'load_calibration_profiles', return_value=self._approved_profiles({
            'latency_ms': ('icmp:8.8.8.8', 200),
            'packet_loss_pct': ('icmp:8.8.8.8', 5),
            'bandwidth_mbps': ('device:agent-1', 20),
        })):
            for _ in range(5):
                network_resolver.add_agent_verification_sample(issue_id, self._metric_sample(
                    metric_targets={
                        'latency_ms': 'icmp:8.8.8.8',
                        'packet_loss_pct': 'icmp:8.8.8.8',
                        'bandwidth_mbps': 'device:agent-1',
                    },
                    latency_ms=290,
                    packet_loss_pct=0,
                    bandwidth_mbps=8,
                    connection_ok=True,
                    dns_ok=True,
                ))

        self.assertIn(issue_id, network_resolver.active_issues)
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'verification_failed')
        self.assertFalse(network_resolver.resolution_history[-1]['resolution_success'])
        self.assertEqual(network_resolver.resolution_history[-1]['verification_status'], 'unresolved')

    def test_unmeasurable_action_is_recorded_as_inconclusive_not_failure(self):
        self._register_agent()
        issue_id = network_resolver.handle_anomaly({
            'type': 'bandwidth_saturation',
            'details': {'usage': 95, 'threshold': 80},
            'detected_at': '2026-10-26T00:00:00',
            'agent_id': 'agent-1',
        })
        with patch.object(handlers.socketio, 'emit'):
            self.assertTrue(handlers.dispatch_issue_to_agent(issue_id)['success'])
        network_resolver.update_issue_status(issue_id, True, 'completed')

        for _ in range(5):
            network_resolver.add_agent_verification_sample(
                issue_id,
                self._metric_sample(bandwidth_mbps=42),
            )

        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'verification_inconclusive')
        self.assertIsNone(network_resolver.resolution_history[-1]['resolution_success'])
        self.assertEqual(network_resolver.resolution_history[-1]['verification_status'], 'inconclusive')

    def test_bandwidth_issue_verifies_with_improved_same_interface_utilization(self):
        self._register_agent()
        agent_manager.metrics_history['agent-1'] = [{
            'latency_ms': 250,
            'packet_loss_pct': 0,
            'connection_ok': True,
            'interface_metrics': [{
                'name': 'Ethernet',
                'speed_mbps': 100,
                'utilization_pct': 90,
            }],
        }]
        issue_id = network_resolver.handle_anomaly({
            'type': 'bandwidth_saturation',
            'details': {
                'interface': 'Ethernet',
                'average_utilization_pct': 90,
                'speed_mbps': 100,
            },
            'detected_at': '2026-10-26T00:00:00',
            'agent_id': 'agent-1',
        })
        with patch.object(handlers.socketio, 'emit'):
            self.assertTrue(handlers.dispatch_issue_to_agent(issue_id)['success'])
        network_resolver.update_issue_status(issue_id, True, 'diagnostics complete')

        anomaly = network_resolver.active_issues[issue_id]['anomaly']
        anomaly['agent_context'] = {
            'metric_targets': {
                'latency_ms': 'icmp:8.8.8.8',
                'packet_loss_pct': 'icmp:8.8.8.8',
                'connection_ok': 'tcp:1.1.1.1:443',
            },
        }
        with patch.object(resolver_module, 'load_calibration_profiles', return_value=self._approved_profiles({
            'utilization_pct': ('interface:Ethernet', 60),
            'latency_ms': ('icmp:8.8.8.8', 100),
            'packet_loss_pct': ('icmp:8.8.8.8', 5),
        })):
            for _ in range(5):
                network_resolver.add_agent_verification_sample(issue_id, self._metric_sample(
                    metric_targets={
                        'latency_ms': 'icmp:8.8.8.8',
                        'packet_loss_pct': 'icmp:8.8.8.8',
                        'connection_ok': 'tcp:1.1.1.1:443',
                    },
                    latency_ms=50,
                    packet_loss_pct=0,
                    connection_ok=True,
                    interface_metrics=[{
                        'name': 'Ethernet',
                        'speed_mbps': 100,
                        'utilization_pct': 50,
                    }],
                ))

        self.assertNotIn(issue_id, network_resolver.active_issues)
        self.assertEqual(network_resolver.resolution_history[-1]['verification_status'], 'verified')
        self.assertEqual(network_resolver.resolution_history[-1]['verification_samples'][-1]['interface_metrics'][0]['utilization_pct'], 50)

    def test_verification_timeout_keeps_issue_open_and_unlabeled(self):
        self._register_agent()
        issue_id = self._create_agent_issue()
        with patch.object(handlers.socketio, 'emit'):
            self.assertTrue(handlers.dispatch_issue_to_agent(issue_id)['success'])
        network_resolver.update_issue_status(issue_id, True, 'completed')

        self.assertTrue(network_resolver.mark_verification_timed_out(issue_id))
        self.assertIn(issue_id, network_resolver.active_issues)
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'verification_inconclusive')
        self.assertIsNone(network_resolver.resolution_history[-1]['resolution_success'])

    def test_pending_issue_is_dispatched_when_its_agent_is_online(self):
        issue_id = self._create_agent_issue()
        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'pending')
        self._register_agent()

        with patch.object(handlers.socketio, 'emit'):
            handlers.dispatch_pending_issues_for_agent('agent-1')

        self.assertEqual(network_resolver.active_issues[issue_id]['status'], 'awaiting_agent')

    def test_agent_anomaly_includes_client_context(self):
        agent_manager.register_agent('sid-1', {
            'agent_id': 'agent-1',
            'api_key': 'your-secret-key',
            'hostname': 'test-agent',
            'os': 'Windows',
            'os_version': 'Windows 11',
        })
        anomaly = {'type': 'high_latency', 'details': {'latency': 250}}
        metrics = {
            'agent_id': 'agent-1',
            'latency_ms': 250,
            'bandwidth_mbps': 12.5,
            'packet_loss_pct': 2,
            'dns_ok': True,
            'connection_ok': False,
            'cpu_percent': 40,
            'memory_percent': 65,
        }

        with patch.object(handlers.detector, 'detect_anomalies', return_value=[anomaly]), \
             patch.object(handlers.resolver, 'handle_anomaly', return_value='issue-1') as create_issue, \
             patch.object(handlers, 'dispatch_issue_to_agent', return_value={'success': True}):
            handlers.handle_agent_metrics(metrics)

        created_anomaly = create_issue.call_args.args[0]
        self.assertEqual(created_anomaly['agent_context'], {
            'hostname': 'test-agent',
            'os': 'Windows',
            'os_version': 'Windows 11',
            'dns_ok': True,
            'connection_ok': False,
            'cpu_percent': 40,
            'memory_percent': 65,
        })

    def test_dns_failure_selects_platform_specific_dns_action(self):
        with patch.object(handlers.decision_support, 'llm_action_decision', return_value=None):
            action = handlers.select_healing_action(
                {'type': 'high_latency', 'agent_context': {'dns_ok': False}},
                {'os': 'Windows'},
            )

        self.assertEqual(action['id'], 'flush_dns_cache')
        self.assertEqual(action['parameters'], {})
        self.assertEqual(action['decision_source'], 'rules')

    def test_llm_can_only_rank_eligible_action_ids_with_minimum_confidence(self):
        anomaly = {'type': 'high_latency', 'agent_context': {'dns_ok': False}}
        agent = {'os': 'Windows'}
        with patch.dict(os.environ, {
            'ACTION_LLM_ENABLED': 'true',
            'ACTION_LLM_MIN_CONFIDENCE': '0.75',
        }, clear=False), patch.object(handlers.decision_support, 'llm_action_decision', return_value={
            'action_id': 'check_connectivity',
            'confidence': 0.91,
            'rationale': 'DNS works; validate reachability and latency.',
        }) as choose_action:
            action = handlers.select_healing_action(anomaly, agent)

        self.assertEqual(action['id'], 'check_connectivity')
        self.assertEqual(action['decision_source'], 'llm')
        self.assertEqual(action['confidence'], 0.91)
        candidates = [candidate['id'] for candidate in choose_action.call_args.args[0]]
        self.assertEqual(candidates, ['flush_dns_cache', 'check_connectivity'])

    def test_invalid_or_low_confidence_llm_recommendation_falls_back_to_rules(self):
        anomaly = {'type': 'high_latency', 'agent_context': {'dns_ok': False}}
        for recommendation in (
            {'action_id': 'invented_command', 'confidence': 0.99, 'rationale': 'not allowed'},
            {'action_id': 'check_connectivity', 'confidence': 0.2, 'rationale': 'uncertain'},
        ):
            with self.subTest(recommendation=recommendation), \
                 patch.dict(os.environ, {
                     'ACTION_LLM_ENABLED': 'true',
                     'ACTION_LLM_MIN_CONFIDENCE': '0.75',
                 }, clear=False), \
                 patch.object(handlers.decision_support, 'llm_action_decision', return_value=recommendation):
                action = handlers.select_healing_action(anomaly, {'os': 'Windows'})
            self.assertEqual(action['id'], 'flush_dns_cache')
            self.assertEqual(action['decision_source'], 'rules')

    def test_unknown_platform_or_issue_fails_closed(self):
        self.assertIsNone(handlers.select_healing_action(
            {'type': 'high_latency'},
            {'os': 'Plan9'},
        ))
        self.assertIsNone(handlers.select_healing_action(
            {'type': 'security_breach'},
            {'os': 'Windows'},
        ))

    def test_bandwidth_saturation_requires_three_busy_samples_and_impact(self):
        agent_manager.metrics_history['agent-1'] = [
            {
            'timestamp': f'2026-10-26T18:00:0{index}',
                'latency_ms': latency,
                'packet_loss_pct': 0,
                'connection_ok': True,
                'interface_metrics': [{
                    'name': 'Ethernet',
                    'speed_mbps': 100,
                    'utilization_pct': 85,
                }],
            }
            for index, latency in enumerate((250, 230, 30))
        ]

        anomaly = handlers.detect_bandwidth_saturation('agent-1', {'timestamp': 'now'})

        self.assertEqual(anomaly['type'], 'bandwidth_saturation')
        self.assertEqual(anomaly['details']['interface'], 'Ethernet')
        self.assertEqual(anomaly['details']['window_samples'], 3)
        self.assertEqual(anomaly['details']['impact_samples'], 2)

    def test_high_throughput_without_known_capacity_is_not_saturation(self):
        agent_manager.metrics_history['agent-1'] = [
            {
                'timestamp': f'2026-10-26T18:00:0{index}',
                'bandwidth_mbps': 500,
                'latency_ms': 20,
                'packet_loss_pct': 0,
            }
            for index in range(3)
        ]

        self.assertIsNone(handlers.detect_bandwidth_saturation('agent-1', {}))

    def test_saturation_is_not_confirmed_without_sustained_impact(self):
        agent_manager.metrics_history['agent-1'] = [
            {
                'timestamp': f'2026-10-26T18:00:0{index}',
                'latency_ms': 20,
                'packet_loss_pct': 0,
                'connection_ok': True,
                'interface_metrics': [{
                    'name': 'Ethernet',
                    'speed_mbps': 100,
                    'utilization_pct': 95,
                }],
            }
            for index in range(3)
        ]

        self.assertIsNone(handlers.detect_bandwidth_saturation('agent-1', {}))

    def test_saturation_window_rejects_stale_samples(self):
        agent_manager.metrics_history['agent-1'] = [
            {
                'timestamp': timestamp,
                'latency_ms': 250,
                'packet_loss_pct': 0,
                'connection_ok': True,
                'interface_metrics': [{
                    'name': 'Ethernet',
                    'speed_mbps': 100,
                    'utilization_pct': 90,
                }],
            }
            for timestamp in (
                '2026-10-26T18:00:00',
                '2026-10-26T18:00:10',
                '2026-10-26T18:01:00',
            )
        ]

        self.assertIsNone(handlers.detect_bandwidth_saturation('agent-1', {}))

    def test_only_one_pending_issue_is_dispatched_per_agent_per_minute(self):
        first_issue = self._create_agent_issue()
        second_issue = self._create_agent_issue()
        self._register_agent()

        with patch.object(handlers.socketio, 'emit') as emit:
            handlers.dispatch_pending_issues_for_agent('agent-1')

        dispatched = [
            call for call in emit.call_args_list
            if call.args and call.args[0] == 'run_command'
        ]
        self.assertEqual(len(dispatched), 1)
        states = {network_resolver.active_issues[first_issue]['status'], network_resolver.active_issues[second_issue]['status']}
        self.assertEqual(states, {'awaiting_agent', 'pending'})

    def test_manual_dispatch_bypasses_the_per_agent_rate_limit(self):
        first_issue = self._create_agent_issue()
        second_issue = self._create_agent_issue()
        self._register_agent()

        with patch.object(handlers.socketio, 'emit') as emit:
            self.assertTrue(handlers.dispatch_issue_to_agent(first_issue)['success'])
            self.assertTrue(handlers.dispatch_issue_to_agent(second_issue, bypass_rate_limit=True)['success'])

        dispatched = [call for call in emit.call_args_list if call.args and call.args[0] == 'run_command']
        self.assertEqual(len(dispatched), 2)

    def test_retryable_issues_are_returned_newest_first(self):
        network_resolver.active_issues = {
            'old': {'status': 'pending', 'detected_at': '2026-07-28T09:00:00', 'anomaly': {'agent_id': 'agent-1'}},
            'new': {'status': 'pending', 'detected_at': '2026-07-28T09:01:00', 'anomaly': {'agent_id': 'agent-1'}},
        }
        self.assertEqual(network_resolver.get_retryable_agent_issue_ids('agent-1'), ['new', 'old'])

    def test_reloaded_awaiting_issue_is_retryable_after_server_restart(self):
        issue = {
            'id': 'persisted',
            'status': 'awaiting_agent',
            'command_id': 'lost-after-restart',
            'detected_at': '2026-07-28T09:00:00',
            'anomaly': {'agent_id': 'agent-1', 'type': 'general_anomaly'},
        }
        with tempfile.NamedTemporaryFile('w', delete=False) as handle:
            tempfile_path = handle.name
            import json
            json.dump({'persisted': issue}, handle)
        try:
            with patch.object(resolver_module, 'ISSUES_FILE', tempfile_path):
                network_resolver.active_issues = {}
                network_resolver._load_active_issues()
        finally:
            os.remove(tempfile_path)

        self.assertEqual(network_resolver.active_issues['persisted']['status'], 'pending')
        self.assertNotIn('command_id', network_resolver.active_issues['persisted'])
        self.assertEqual(network_resolver.get_retryable_agent_issue_ids('agent-1'), ['persisted'])

    def test_reconnecting_agent_replaces_old_sid_without_old_disconnect_removing_it(self):
        self._register_agent()
        self.assertTrue(agent_manager.register_agent('sid-2', {
            'agent_id': 'agent-1',
            'api_key': 'your-secret-key',
            'hostname': 'test-agent',
        }))

        agent_manager.remove_agent('sid-1')

        self.assertEqual(agent_manager.get_agent_sid('agent-1'), 'sid-2')
        self.assertEqual(agent_manager.get_all_agents()[0]['agent_id'], 'agent-1')


if __name__ == '__main__':
    unittest.main()
