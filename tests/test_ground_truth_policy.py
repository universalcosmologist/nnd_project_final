"""Tests for machine-specific calibration profiles."""

import unittest
from datetime import datetime, timedelta

from app.healing.ground_truth import (
    MIN_CALIBRATION_SAMPLES,
    build_numeric_profile_candidate,
    derive_upper_bound,
    get_approved_metric_profile,
    get_issue_metric_target_id,
)


class GroundTruthProfileTests(unittest.TestCase):
    def test_upper_bound_tolerates_small_variation_and_ignores_isolated_outlier(self):
        samples = [10.0 + (index % 3 - 1) * 0.2 for index in range(30)]
        samples.append(1000.0)

        profile = derive_upper_bound(samples)

        self.assertEqual(profile['sample_count'], 31)
        self.assertLess(profile['upper_bound'], 20)
        self.assertGreaterEqual(profile['upper_bound'], profile['p95'])

    def test_upper_bound_requires_minimum_healthy_samples(self):
        with self.assertRaises(ValueError):
            derive_upper_bound([10.0] * (MIN_CALIBRATION_SAMPLES - 1))

    def test_profile_lookup_is_scoped_to_agent_metric_and_target(self):
        profile = {
            'target_id': 'probe:edge.example:443',
            'unit': 'ms',
            'approved': True,
            'approved_by': 'operator-1',
            'approved_at': '2026-11-01T00:00:00',
            'version': 1,
            'sample_count': MIN_CALIBRATION_SAMPLES,
            'upper_bound': 42.0,
        }
        profiles = {
            'agent-a': {'metrics': {'latency_ms': profile}},
            'agent-b': {'metrics': {'latency_ms': {**profile, 'upper_bound': 84.0}}},
        }

        self.assertEqual(
            get_approved_metric_profile('agent-a', 'latency_ms', 'probe:edge.example:443', profiles),
            profile,
        )
        self.assertIsNone(
            get_approved_metric_profile('agent-a', 'latency_ms', 'probe:other.example:443', profiles)
        )
        self.assertIsNone(
            get_approved_metric_profile('agent-a', 'packet_loss_pct', 'probe:edge.example:443', profiles)
        )
        self.assertEqual(
            get_approved_metric_profile('agent-b', 'latency_ms', 'probe:edge.example:443', profiles)['upper_bound'],
            84.0,
        )

    def test_unapproved_or_incomplete_profile_fails_closed(self):
        profile = {
            'target_id': 'dns:example.com',
            'unit': 'ms',
            'approved': False,
            'approved_by': '',
            'approved_at': None,
            'version': 1,
            'sample_count': 30,
            'upper_bound': 1.0,
        }
        profiles = {'agent-a': {'metrics': {'dns_latency_ms': profile}}}

        self.assertIsNone(
            get_approved_metric_profile('agent-a', 'dns_latency_ms', 'dns:example.com', profiles)
        )
        profile['approved'] = True
        profile['approved_by'] = 'operator-1'
        profile['sample_count'] = 5
        self.assertIsNone(
            get_approved_metric_profile('agent-a', 'dns_latency_ms', 'dns:example.com', profiles)
        )

    def test_candidate_requires_healthy_window_and_stays_unapproved(self):
        start = datetime(2026, 11, 1)
        samples = [
            {
                'agent_id': 'agent-a',
                'timestamp': (start + timedelta(seconds=index * 10)).isoformat(),
                'latency_ms': 20 + (index % 3),
                'metric_targets': {'latency_ms': 'icmp:edge.example'},
            }
            for index in range(31)
        ]

        candidate = build_numeric_profile_candidate(
            'agent-a', 'latency_ms', 'icmp:edge.example', 'ms', samples
        )

        self.assertEqual(candidate['sample_count'], 31)
        self.assertEqual(candidate['target_id'], 'icmp:edge.example')
        self.assertFalse(candidate['approved'])
        self.assertIsNone(candidate['approved_by'])
        self.assertEqual(candidate['method'], 'max(p95, median + 3 * 1.4826 * MAD)')

    def test_candidate_rejects_mismatched_target_and_short_window(self):
        start = datetime(2026, 11, 1)
        samples = [
            {
                'agent_id': 'agent-a',
                'timestamp': (start + timedelta(seconds=index * 5)).isoformat(),
                'latency_ms': 20,
                'metric_targets': {'latency_ms': 'icmp:other.example'},
            }
            for index in range(31)
        ]

        with self.assertRaisesRegex(ValueError, 'target does not match'):
            build_numeric_profile_candidate(
                'agent-a', 'latency_ms', 'icmp:edge.example', 'ms', samples
            )

        for sample in samples:
            sample['metric_targets']['latency_ms'] = 'icmp:edge.example'
        with self.assertRaisesRegex(ValueError, 'at least 300 seconds'):
            build_numeric_profile_candidate(
                'agent-a', 'latency_ms', 'icmp:edge.example', 'ms', samples
            )

    def test_generic_dns_and_connectivity_probes_do_not_match_other_targets(self):
        dns_anomaly = {
            'type': 'dns_resolution_failure',
            'details': {'domain': 'internal.example'},
            'agent_context': {'metric_targets': {'dns_ok': 'dns:google.com'}},
        }
        connection_anomaly = {
            'type': 'connection_timeout',
            'details': {'host': 'database.internal', 'port': 5432},
            'agent_context': {'metric_targets': {'connection_ok': 'tcp:1.1.1.1:443'}},
        }

        self.assertIsNone(get_issue_metric_target_id(dns_anomaly, 'dns_ok'))
        self.assertIsNone(get_issue_metric_target_id(connection_anomaly, 'connection_ok'))


if __name__ == '__main__':
    unittest.main()