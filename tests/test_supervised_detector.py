"""Tests for the verified-label supervised detector and candidate gate."""

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from app.healing.ground_truth import MIN_CALIBRATION_SAMPLES
from app.models.anomaly_detector import AnomalyDetector
from app.models.supervised_detector import SupervisedAnomalyDetector, _feedback_cli


class SupervisedDetectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.detector = SupervisedAnomalyDetector(
            labels_file=os.path.join(self.temporary_directory.name, 'labels.json'),
            candidate_model_file=os.path.join(self.temporary_directory.name, 'candidate.pkl'),
        )
        self.profiles = {'agent-a': {'metrics': {
            metric_name: {
                'target_id': target_id,
                'unit': unit,
                'approved': True,
                'approved_by': 'operator',
                'approved_at': '2026-11-05T00:00:00',
                'version': 1,
                'sample_count': MIN_CALIBRATION_SAMPLES,
                'upper_bound': upper_bound,
            }
            for metric_name, target_id, unit, upper_bound in (
                ('latency_ms', 'icmp:host.example', 'ms', 100),
                ('packet_loss_pct', 'icmp:host.example', 'percent', 5),
                ('bandwidth_mbps', 'device:agent-a', 'Mbps', 20),
            )
        }}}

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _metrics(self, factor, timestamp='2026-11-05T12:00:00+00:00', agent_id='agent-a'):
        return {
            'agent_id': agent_id,
            'timestamp': timestamp,
            'metric_targets': {
                'latency_ms': 'icmp:host.example',
                'packet_loss_pct': 'icmp:host.example',
                'bandwidth_mbps': 'device:agent-a',
            },
            'latency_ms': 100 * factor,
            'packet_loss_pct': 5 * factor,
            'bandwidth_mbps': 20 * factor,
        }

    def test_inconclusive_feedback_is_rejected(self):
        with patch('app.models.supervised_detector.load_calibration_profiles', return_value=self.profiles):
            detection_id, _ = self.detector.observe(self._metrics(2), isolation_prediction=-1)

        with self.assertRaisesRegex(ValueError, 'Only independently verified'):
            self.detector.record_verified_label(
                detection_id,
                actual_anomaly=True,
                verification_status='inconclusive',
                label_source='test',
            )
        self.assertEqual(self.detector.labels, [])

    def test_verified_labels_are_joined_to_detection_and_deduplicated(self):
        with patch('app.models.supervised_detector.load_calibration_profiles', return_value=self.profiles):
            detection_id, _ = self.detector.observe(self._metrics(2), isolation_prediction=-1)

        result = self.detector.record_verified_label(
            detection_id,
            actual_anomaly=True,
            verification_status='verified',
            label_source='approved profile and recovery',
            issue_id='issue-1',
        )
        duplicate = self.detector.record_verified_label(
            detection_id,
            actual_anomaly=True,
            verification_status='verified',
            label_source='approved profile and recovery',
        )

        self.assertTrue(result['recorded'])
        self.assertEqual(result['label']['case_type'], 'true_positive')
        self.assertFalse(duplicate['recorded'])
        self.assertEqual(len(self.detector.labels), 1)

        summary = self.detector.feedback_summary()
        self.assertEqual(summary['isolation_forest']['true_positives'], 1)
        self.assertEqual(summary['isolation_forest']['error_rate'], 0)

    def test_external_report_can_label_a_false_negative(self):
        with patch('app.models.supervised_detector.load_calibration_profiles', return_value=self.profiles):
            detection_id, _ = self.detector.observe(self._metrics(2), isolation_prediction=1)

        result = self.detector.record_external_verified_label(
            detection_id,
            actual_anomaly=True,
            source='independent incident report',
            verifier='operator-1',
        )

        self.assertTrue(result['recorded'])
        self.assertEqual(result['label']['case_type'], 'false_negative')
        self.assertEqual(result['label']['prediction_source'], 'isolation_forest')

    def test_false_positive_status_cannot_claim_anomaly_truth(self):
        with patch('app.models.supervised_detector.load_calibration_profiles', return_value=self.profiles):
            detection_id, _ = self.detector.observe(self._metrics(2), isolation_prediction=-1)

        with self.assertRaisesRegex(ValueError, 'actual_anomaly=False'):
            self.detector.record_verified_label(
                detection_id,
                actual_anomaly=True,
                verification_status='false_positive',
                label_source='test',
            )

    def test_agent_measurement_runs_supervised_shadow_observer(self):
        class IsolationStub:
            offset_ = 0

            def predict(self, _features):
                return [-1]

            def decision_function(self, _features):
                return [-0.4]

        detector = AnomalyDetector()
        detector.model = IsolationStub()
        detector.supervised_detector = SupervisedAnomalyDetector(
            labels_file=os.path.join(self.temporary_directory.name, 'live-labels.json'),
            candidate_model_file=os.path.join(self.temporary_directory.name, 'live-candidate.pkl'),
        )
        with patch.object(detector.supervised_detector, 'observe', return_value=('detection-1', None)) as observe:
            anomalies = detector.detect_anomalies({
                'agent_id': 'agent-a',
                'timestamp': '2026-11-05T12:00:00+00:00',
                'metric_targets': {},
                'latency': 50,
                'bandwidth_usage': 10,
                'packet_loss': 1,
            })

        observe.assert_called_once()
        self.assertTrue(anomalies)
        self.assertTrue(all(anomaly['detection_id'] == 'detection-1' for anomaly in anomalies))

    def test_balanced_verified_labels_train_shadow_candidate(self):
        with patch('app.models.supervised_detector.load_calibration_profiles', return_value=self.profiles):
            for index in range(40):
                is_anomaly = index % 2 == 1
                detection_id, _ = self.detector.observe(
                    self._metrics(
                        2 if is_anomaly else 0.5,
                        timestamp=f'2026-11-05T12:{index // 60:02d}:{index % 60:02d}+00:00',
                    ),
                    isolation_prediction=-1 if is_anomaly else 1,
                    isolation_score=-0.5 if is_anomaly else 0.5,
                )
                result = self.detector.record_verified_label(
                    detection_id,
                    actual_anomaly=is_anomaly,
                    verification_status='verified',
                    label_source='test ground truth',
                )

            self.assertEqual(result['training']['status'], 'candidate_ready')
            self.assertEqual(result['training']['metrics']['split_strategy'], 'chronological_holdout')
            self.assertIsNotNone(self.detector.candidate_model)
            shadow_id, prediction = self.detector.observe(self._metrics(2))

        self.assertTrue(shadow_id)
        self.assertTrue(prediction['predicted_anomaly'])
        self.assertEqual(prediction['decision_role'], 'shadow_only')
        self.assertGreaterEqual(self.detector.candidate_metrics['balanced_accuracy'], 0.70)

    def test_candidate_split_prefers_unseen_agent_when_both_classes_exist(self):
        labeled = []
        for agent_id in ('agent-a', 'agent-b'):
            for label in (0, 1, 0, 1):
                labeled.append({
                    'agent_id': agent_id,
                    'label': label,
                    'features': [float(label)] * 6,
                    'timestamp': f'2026-11-05T12:00:0{len(labeled)}+00:00',
                    'detection_id': f'detection-{len(labeled)}',
                })

        split = self.detector._candidate_holdout_indices(labeled)

        self.assertIsNotNone(split)
        train_indices, test_indices, strategy = split
        self.assertEqual(strategy, 'agent_holdout')
        self.assertFalse(
            {labeled[index]['agent_id'] for index in train_indices}
            & {labeled[index]['agent_id'] for index in test_indices}
        )
        self.assertEqual({labeled[index]['label'] for index in test_indices}, {0, 1})

    def test_feedback_cli_requires_confirmation_before_persisting(self):
        with patch('app.models.supervised_detector.load_calibration_profiles', return_value=self.profiles):
            detection_id, _ = self.detector.observe(self._metrics(2), isolation_prediction=1)

        args = [
            '--detection-id', detection_id,
            '--actual-anomaly', 'true',
            '--source', 'incident INC-123',
            '--verifier', 'operator-7',
            '--labels-file', self.detector.labels_file,
            '--candidate-file', self.detector.candidate_model_file,
        ]
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            _feedback_cli(args)
        self.assertEqual(self.detector.labels, [])

        output = StringIO()
        with redirect_stdout(output):
            _feedback_cli(args + ['--confirm-verified'])

        result = json.loads(output.getvalue())
        self.assertTrue(result['recorded'])
        self.assertEqual(result['label']['case_type'], 'false_negative')
        self.assertEqual(result['label']['label_source'], 'incident INC-123')
        self.assertEqual(result['label']['verified_by'], 'operator-7')


if __name__ == '__main__':
    unittest.main()