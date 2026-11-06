"""Verified-label storage and a shadow-mode supervised anomaly detector."""

import json
import logging
import os
import threading
import uuid
import argparse
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, confusion_matrix

from app.healing.ground_truth import get_approved_metric_profile, load_calibration_profiles


logger = logging.getLogger('supervised_anomaly_detector')
MODEL_DIR = os.path.join('data', 'models')
LABELS_FILE = os.path.join(MODEL_DIR, 'verified_anomaly_labels.json')
CANDIDATE_MODEL_FILE = os.path.join(MODEL_DIR, 'supervised_anomaly_candidate.pkl')
FEATURE_NAMES = (
    'latency_ratio', 'latency_available',
    'packet_loss_ratio', 'packet_loss_available',
    'bandwidth_ratio', 'bandwidth_available',
)
MIN_LABELS_PER_CLASS = 20
MIN_HOLDOUT_BALANCED_ACCURACY = 0.70


class SupervisedAnomalyDetector:
    """Record all agent observations and train candidates from verified labels only."""

    def __init__(self, labels_file=LABELS_FILE, candidate_model_file=CANDIDATE_MODEL_FILE):
        self.labels_file = labels_file
        self.candidate_model_file = candidate_model_file
        self.lock = threading.RLock()
        self.observations = {}
        self.labels = []
        self.candidate_model = None
        self.candidate_metrics = None
        self._load_store()
        self._load_candidate_model()

    def _load_store(self):
        if not os.path.exists(self.labels_file):
            return
        try:
            with open(self.labels_file, 'r', encoding='utf-8') as labels_file:
                document = json.load(labels_file)
            if isinstance(document, dict):
                observations = document.get('observations', {})
                labels = document.get('labels', [])
                if isinstance(observations, dict) and isinstance(labels, list):
                    self.observations = observations
                    self.labels = labels
        except (OSError, json.JSONDecodeError):
            logger.exception('Could not load verified anomaly labels')

    def _save_store(self):
        directory = os.path.dirname(self.labels_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary_file = f'{self.labels_file}.tmp'
        with open(temporary_file, 'w', encoding='utf-8') as labels_file:
            json.dump({
                'schema_version': 1,
                'observations': self.observations,
                'labels': self.labels,
            }, labels_file, indent=2)
        os.replace(temporary_file, self.labels_file)

    def list_recent_observations(self, limit=20):
        """Return a compact view of recent detections for an operator review."""
        with self.lock:
            recent = list(self.observations.values())[-max(1, limit):]
            labeled_ids = {label.get('detection_id') for label in self.labels}
            return [
                {
                    'detection_id': item.get('detection_id'),
                    'timestamp': item.get('timestamp'),
                    'agent_id': item.get('agent_id'),
                    'metric_targets': item.get('metric_targets'),
                    'isolation_prediction': item.get('isolation_prediction'),
                    'isolation_score': item.get('isolation_score'),
                    'supervised_prediction': item.get('supervised_prediction'),
                    'already_labeled': item.get('detection_id') in labeled_ids,
                }
                for item in reversed(recent)
            ]

    def _load_candidate_model(self):
        if not os.path.exists(self.candidate_model_file):
            return
        try:
            artifact = joblib.load(self.candidate_model_file)
            if isinstance(artifact, dict) and artifact.get('feature_names') == list(FEATURE_NAMES):
                self.candidate_model = artifact.get('model')
                self.candidate_metrics = artifact.get('metrics')
        except (OSError, ValueError, KeyError, TypeError):
            logger.exception('Could not load supervised candidate model')

    @staticmethod
    def _ratio(value, profile):
        if profile is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return 0.0, 0.0
        upper_bound = profile.get('upper_bound')
        if not isinstance(upper_bound, (int, float)) or isinstance(upper_bound, bool) or upper_bound <= 0:
            return 0.0, 0.0
        return float(value) / float(upper_bound), 1.0

    def extract_features(self, metrics):
        agent_id = metrics.get('agent_id')
        targets = metrics.get('metric_targets', {})
        if not isinstance(agent_id, str) or not isinstance(targets, dict):
            return None
        try:
            profiles = load_calibration_profiles()
        except (OSError, ValueError, TypeError):
            logger.warning('No valid calibration profile available for supervised features')
            profiles = {}

        def profile_for(metric_name):
            target_id = targets.get(metric_name)
            if not isinstance(target_id, str):
                return None
            return get_approved_metric_profile(agent_id, metric_name, target_id, profiles)

        latency_ratio, latency_available = self._ratio(
            metrics.get('latency_ms'), profile_for('latency_ms')
        )
        loss_ratio, loss_available = self._ratio(
            metrics.get('packet_loss_pct'), profile_for('packet_loss_pct')
        )
        bandwidth_ratio, bandwidth_available = self._ratio(
            metrics.get('bandwidth_mbps'), profile_for('bandwidth_mbps')
        )
        return [
            latency_ratio, latency_available,
            loss_ratio, loss_available,
            bandwidth_ratio, bandwidth_available,
        ]

    def observe(self, metrics, isolation_prediction=None, isolation_score=None):
        """Persist one measurement and return its ID plus a non-authoritative prediction."""
        features = self.extract_features(metrics)
        if features is None:
            return None, None
        observation_id = str(uuid.uuid4())
        observation = {
            'detection_id': observation_id,
            'timestamp': metrics.get('timestamp') or datetime.now().astimezone().isoformat(),
            'agent_id': metrics.get('agent_id'),
            'metric_targets': metrics.get('metric_targets', {}),
            'features': features,
            'isolation_prediction': isolation_prediction,
            'isolation_score': isolation_score,
        }
        with self.lock:
            prediction = self._predict_locked(features)
            observation['supervised_prediction'] = prediction
            self.observations[observation_id] = observation
            if len(self.observations) > 5000:
                labeled_ids = {label.get('detection_id') for label in self.labels}
                for oldest_id in list(self.observations):
                    if len(self.observations) <= 5000:
                        break
                    if oldest_id not in labeled_ids:
                        self.observations.pop(oldest_id, None)
            self._save_store()
        return observation_id, prediction

    def _predict_locked(self, features):
        if self.candidate_model is None:
            return None
        probabilities = self.candidate_model.predict_proba([features])[0]
        class_index = list(self.candidate_model.classes_).index(1)
        anomaly_confidence = float(probabilities[class_index])
        recent_false_positives = sum(
            label.get('prediction_source') == 'supervised_candidate'
            and label.get('predicted_anomaly') is True
            and label.get('actual_anomaly') is False
            for label in self.labels[-20:]
        )
        confidence_factor = max(0.5, 1.0 - 0.1 * recent_false_positives)
        drift_score = 0.0
        if self.candidate_metrics and self.candidate_metrics.get('feature_means'):
            means = self.candidate_metrics['feature_means']
            standard_deviations = self.candidate_metrics['feature_stds']
            usable_distances = [
                abs(value - means[index]) / max(standard_deviations[index], 0.1)
                for index, value in enumerate(features)
                if index in (0, 2, 4) and features[index + 1] > 0
            ]
            drift_score = float(np.mean(usable_distances)) if usable_distances else 0.0
        return {
            'model': 'supervised_candidate',
            'predicted_anomaly': bool(self.candidate_model.predict([features])[0] == 1),
            'raw_confidence': anomaly_confidence,
            'confidence_factor': confidence_factor,
            'confidence': anomaly_confidence * confidence_factor,
            'drift_score': drift_score,
            'drift_warning': drift_score >= 3.0,
            'decision_role': 'shadow_only',
        }

    def record_verified_label(
        self,
        detection_id,
        actual_anomaly,
        verification_status,
        label_source,
        issue_id=None,
        verifier=None,
    ):
        """Attach independently established binary truth to a recorded detection."""
        if not isinstance(actual_anomaly, bool):
            raise ValueError('actual_anomaly must be a boolean')
        if verification_status not in ('verified', 'false_positive', 'externally_verified'):
            raise ValueError('Only independently verified outcomes can label training data')
        if verification_status == 'false_positive' and actual_anomaly:
            raise ValueError('A false-positive label must have actual_anomaly=False')
        if not isinstance(label_source, str) or not label_source.strip():
            raise ValueError('A label source is required')

        with self.lock:
            observation = self.observations.get(detection_id)
            if not observation:
                raise KeyError(f'Unknown detection id: {detection_id}')
            if any(label.get('detection_id') == detection_id for label in self.labels):
                return {'recorded': False, 'reason': 'already_labeled'}

            supervised_prediction = observation.get('supervised_prediction')
            if isinstance(supervised_prediction, dict):
                predicted_anomaly = supervised_prediction.get('predicted_anomaly') is True
                prediction_source = 'supervised_candidate'
            elif observation.get('isolation_prediction') in (-1, 1):
                predicted_anomaly = observation['isolation_prediction'] == -1
                prediction_source = 'isolation_forest'
            else:
                predicted_anomaly = None
                prediction_source = None
            case_type = 'unscored' if predicted_anomaly is None else (
                'true_positive' if predicted_anomaly and actual_anomaly else
                'false_positive' if predicted_anomaly and not actual_anomaly else
                'false_negative' if not predicted_anomaly and actual_anomaly else
                'true_negative'
            )
            label = {
                'detection_id': detection_id,
                'issue_id': issue_id,
                'actual_anomaly': actual_anomaly,
                'predicted_anomaly': predicted_anomaly,
                'prediction_source': prediction_source,
                'case_type': case_type,
                'verification_status': verification_status,
                'label_source': label_source,
                'verified_by': verifier,
                'timestamp': datetime.now().astimezone().isoformat(),
            }
            self.labels.append(label)
            observation['label_id'] = detection_id
            observation['actual_anomaly'] = actual_anomaly
            self._save_store()
            training_result = self.train_candidate()
            return {'recorded': True, 'label': label, 'training': training_result}

    def record_external_verified_label(self, detection_id, actual_anomaly, source, verifier, issue_id=None):
        """Accept truth from an independent incident/operator source, including false negatives."""
        if not isinstance(verifier, str) or not verifier.strip():
            raise ValueError('A verifier identity is required for external labels')
        return self.record_verified_label(
            detection_id=detection_id,
            actual_anomaly=actual_anomaly,
            verification_status='externally_verified',
            label_source=source,
            issue_id=issue_id,
            verifier=verifier,
        )

    def feedback_summary(self):
        """Summarize verified prediction errors separately for each model source."""
        summaries = {}
        for label in self.labels:
            source = label.get('prediction_source')
            predicted = label.get('predicted_anomaly')
            actual = label.get('actual_anomaly')
            if source not in ('isolation_forest', 'supervised_candidate') or not isinstance(predicted, bool) or not isinstance(actual, bool):
                continue
            summary = summaries.setdefault(source, {
                'count': 0,
                'correct': 0,
                'false_positives': 0,
                'false_negatives': 0,
                'true_positives': 0,
                'true_negatives': 0,
            })
            summary['count'] += 1
            if predicted == actual:
                summary['correct'] += 1
            if predicted and not actual:
                summary['false_positives'] += 1
            elif not predicted and actual:
                summary['false_negatives'] += 1
            elif predicted:
                summary['true_positives'] += 1
            else:
                summary['true_negatives'] += 1

        for summary in summaries.values():
            count = summary['count']
            summary['error_rate'] = (count - summary['correct']) / count if count else None
            summary['accuracy'] = summary['correct'] / count if count else None
        return summaries

    def _candidate_holdout_indices(self, labeled):
        """Prefer unseen-agent validation, falling back to a later-time holdout."""
        agents = sorted({item['agent_id'] for item in labeled if item.get('agent_id')})
        for held_out_agent in agents:
            test_indices = [index for index, item in enumerate(labeled) if item['agent_id'] == held_out_agent]
            train_indices = [index for index, item in enumerate(labeled) if item['agent_id'] != held_out_agent]
            if (
                len(test_indices) >= 2
                and {labeled[index]['label'] for index in test_indices} == {0, 1}
                and {labeled[index]['label'] for index in train_indices} == {0, 1}
            ):
                return train_indices, test_indices, 'agent_holdout'

        ordered_indices = sorted(
            range(len(labeled)),
            key=lambda index: (labeled[index].get('timestamp') or '', labeled[index]['detection_id']),
        )
        split_index = int(len(ordered_indices) * 0.75)
        train_indices = ordered_indices[:split_index]
        test_indices = ordered_indices[split_index:]
        if (
            len(test_indices) >= 2
            and {labeled[index]['label'] for index in test_indices} == {0, 1}
            and {labeled[index]['label'] for index in train_indices} == {0, 1}
        ):
            return train_indices, test_indices, 'chronological_holdout'
        return None

    def train_candidate(self):
        """Train/evaluate a shadow candidate after enough verified labels per class."""
        with self.lock:
            labeled = [
                {
                    'features': self.observations[label['detection_id']]['features'],
                    'label': int(label['actual_anomaly']),
                    'agent_id': self.observations[label['detection_id']].get('agent_id'),
                    'timestamp': self.observations[label['detection_id']].get('timestamp'),
                    'detection_id': label['detection_id'],
                }
                for label in self.labels
                if label.get('detection_id') in self.observations
                and len(self.observations[label['detection_id']].get('features', [])) == len(FEATURE_NAMES)
                and any(
                    self.observations[label['detection_id']]['features'][index] > 0
                    for index in (1, 3, 5)
                )
            ]
            class_counts = {0: 0, 1: 0}
            for item in labeled:
                class_counts[item['label']] += 1
            if min(class_counts.values()) < MIN_LABELS_PER_CLASS:
                return {'status': 'waiting_for_labels', 'class_counts': class_counts}

            holdout = self._candidate_holdout_indices(labeled)
            if holdout is None:
                return {
                    'status': 'waiting_for_evaluation_data',
                    'class_counts': class_counts,
                    'reason': 'Need both classes in training and a later-time or unseen-agent holdout',
                }
            train_indices, test_indices, split_strategy = holdout
            train_features = np.asarray([labeled[index]['features'] for index in train_indices], dtype=float)
            test_features = np.asarray([labeled[index]['features'] for index in test_indices], dtype=float)
            train_labels = np.asarray([labeled[index]['label'] for index in train_indices], dtype=int)
            test_labels = np.asarray([labeled[index]['label'] for index in test_indices], dtype=int)
            model = RandomForestClassifier(
                n_estimators=100,
                class_weight='balanced',
                random_state=42,
                min_samples_leaf=2,
            )
            model.fit(train_features, train_labels)
            predictions = model.predict(test_features)
            balanced_accuracy = float(balanced_accuracy_score(test_labels, predictions))
            feature_means = np.mean(train_features, axis=0).tolist()
            feature_stds = np.std(train_features, axis=0).tolist()
            metrics = {
                'balanced_accuracy': balanced_accuracy,
                'confusion_matrix': confusion_matrix(test_labels, predictions, labels=[0, 1]).tolist(),
                'holdout_samples': int(len(test_labels)),
                'verified_samples': int(len(labeled)),
                'class_counts': class_counts,
                'split_strategy': split_strategy,
                'train_agents': sorted({labeled[index]['agent_id'] for index in train_indices}),
                'holdout_agents': sorted({labeled[index]['agent_id'] for index in test_indices}),
                'feature_means': feature_means,
                'feature_stds': feature_stds,
                'evaluated_at': datetime.now().astimezone().isoformat(),
            }
            result = {
                'status': 'candidate_ready' if balanced_accuracy >= MIN_HOLDOUT_BALANCED_ACCURACY else 'candidate_rejected',
                'metrics': metrics,
            }
            if result['status'] == 'candidate_ready':
                directory = os.path.dirname(self.candidate_model_file)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                temporary_file = f'{self.candidate_model_file}.tmp'
                joblib.dump({
                    'model': model,
                    'feature_names': list(FEATURE_NAMES),
                    'metrics': metrics,
                }, temporary_file)
                os.replace(temporary_file, self.candidate_model_file)
                self.candidate_model = model
                self.candidate_metrics = metrics
            return result


def _feedback_cli(argv=None):
    parser = argparse.ArgumentParser(
        description='Review and record an independently verified anomaly label'
    )
    parser.add_argument('--list', action='store_true', help='List recent detection IDs for review')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--detection-id')
    parser.add_argument('--actual-anomaly', choices=('true', 'false'))
    parser.add_argument('--source', help='Incident, operator review, or independent monitor reference')
    parser.add_argument('--verifier', help='Operator or trusted system identity')
    parser.add_argument('--issue-id')
    parser.add_argument('--confirm-verified', action='store_true', help='Write only after verifying the ground truth independently')
    parser.add_argument('--labels-file', default=LABELS_FILE)
    parser.add_argument('--candidate-file', default=CANDIDATE_MODEL_FILE)
    args = parser.parse_args(argv)

    detector = SupervisedAnomalyDetector(
        labels_file=args.labels_file,
        candidate_model_file=args.candidate_file,
    )
    if args.list:
        print(json.dumps(detector.list_recent_observations(args.limit), indent=2))
        return 0

    if not args.detection_id or args.actual_anomaly is None or not args.source or not args.verifier:
        parser.error('A detection ID, actual label, source, and verifier are required')
    if not args.confirm_verified:
        parser.error('Review the referenced evidence and pass --confirm-verified to record the label')

    result = detector.record_external_verified_label(
        detection_id=args.detection_id,
        actual_anomaly=args.actual_anomaly == 'true',
        source=args.source,
        verifier=args.verifier,
        issue_id=args.issue_id,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(_feedback_cli())