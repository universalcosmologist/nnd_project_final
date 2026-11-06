"""Machine- and target-specific healthy metric profiles."""

import json
import math
import os
import argparse
from datetime import datetime, timezone
from statistics import median


PROFILE_FILE = os.path.join('data', 'healing', 'calibration_profiles.json')
PROFILE_SCHEMA_VERSION = 1
MIN_CALIBRATION_SAMPLES = 30
MIN_CALIBRATION_DURATION_SECONDS = 300
NUMERIC_VERIFICATION_SAMPLES = 5
BOOLEAN_VERIFICATION_SAMPLES = 3
VERIFICATION_WINDOW_SECONDS = 120
MAX_AGENT_CLOCK_SKEW_SECONDS = 120


def parse_iso_timestamp(value):
    """Parse an ISO timestamp and normalize it to UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(sorted_values, percentile):
    position = (len(sorted_values) - 1) * percentile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    return sorted_values[lower_index] + (
        sorted_values[upper_index] - sorted_values[lower_index]
    ) * fraction


def derive_upper_bound(values, min_samples=MIN_CALIBRATION_SAMPLES):
    """Derive a robust upper bound from an explicitly healthy sample window.

    The bound is the larger of the empirical 95th percentile and the median
    plus three robust standard deviations (scaled MAD). This tolerates ordinary
    variation while limiting the influence of isolated calibration outliers.
    """
    numeric_values = [
        float(value)
        for value in values
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ]
    if len(numeric_values) < min_samples:
        raise ValueError(f'At least {min_samples} valid calibration samples are required')

    ordered = sorted(numeric_values)
    center = median(ordered)
    absolute_deviations = sorted(abs(value - center) for value in ordered)
    mad = median(absolute_deviations)
    robust_sigma = 1.4826 * mad

    if robust_sigma == 0:
        first_quartile = _percentile(ordered, 0.25)
        third_quartile = _percentile(ordered, 0.75)
        robust_sigma = (third_quartile - first_quartile) / 1.349

    percentile_95 = _percentile(ordered, 0.95)
    upper_bound = max(percentile_95, center + 3 * robust_sigma)
    return {
        'sample_count': len(ordered),
        'median': center,
        'p95': percentile_95,
        'mad': mad,
        'upper_bound': upper_bound,
    }


def load_calibration_profiles(path=PROFILE_FILE):
    """Load persisted profiles; a missing file means no profiles are approved."""
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as profile_file:
        document = json.load(profile_file)
    if not isinstance(document, dict) or document.get('schema_version') != PROFILE_SCHEMA_VERSION:
        raise ValueError('Unsupported calibration profile document')
    profiles = document.get('profiles')
    if not isinstance(profiles, dict):
        raise ValueError('Calibration profile document must contain a profiles object')
    return profiles


def get_approved_metric_profile(agent_id, metric_name, target_id, profiles=None):
    """Return a profile only when ownership, target, approval, and bound match."""
    if profiles is None:
        profiles = load_calibration_profiles()
    agent_profiles = profiles.get(agent_id)
    if not isinstance(agent_profiles, dict):
        return None
    metric_profiles = agent_profiles.get('metrics')
    if not isinstance(metric_profiles, dict):
        return None
    profile = metric_profiles.get(metric_name)
    if not isinstance(profile, dict):
        return None
    if profile.get('target_id') != target_id or profile.get('approved') is not True:
        return None
    if not isinstance(profile.get('approved_by'), str) or not profile['approved_by'].strip():
        return None
    if not isinstance(profile.get('approved_at'), str) or not profile['approved_at'].strip():
        return None
    try:
        datetime.fromisoformat(profile['approved_at'])
    except ValueError:
        return None
    if isinstance(profile.get('version'), bool) or not isinstance(profile.get('version'), int) or profile['version'] < 1:
        return None
    if not isinstance(profile.get('unit'), str) or not profile['unit'].strip():
        return None
    sample_count = profile.get('sample_count')
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < MIN_CALIBRATION_SAMPLES:
        return None
    upper_bound = profile.get('upper_bound')
    if (
        isinstance(upper_bound, bool)
        or not isinstance(upper_bound, (int, float))
        or not math.isfinite(upper_bound)
        or upper_bound < 0
    ):
        return None
    return profile


def get_issue_metric_target_id(anomaly, metric_name):
    """Resolve the target identity carried by an anomaly for a metric."""
    details = anomaly.get('details', {})
    context = anomaly.get('agent_context', {})
    if not isinstance(details, dict):
        details = {}
    if not isinstance(context, dict):
        context = {}

    if metric_name == 'utilization_pct':
        interface_name = details.get('interface')
        if isinstance(interface_name, str) and interface_name.strip():
            return f'interface:{interface_name}'
        return None

    metric_targets = context.get('metric_targets', {})
    target_id = metric_targets.get(metric_name) if isinstance(metric_targets, dict) else None
    if not isinstance(target_id, str) or not target_id.strip():
        return None

    if anomaly.get('type') == 'dns_resolution_failure' and metric_name == 'dns_ok':
        domain = details.get('domain')
        if not isinstance(domain, str) or not domain.strip() or domain.strip().lower() == 'multiple domains':
            return None
        if target_id.strip().lower() != f'dns:{domain.strip().lower()}':
            return None

    if anomaly.get('type') == 'connection_timeout' and metric_name == 'connection_ok':
        expected_target = details.get('target_id')
        host = details.get('host')
        port = details.get('port')
        if not expected_target and isinstance(host, str) and isinstance(port, int):
            expected_target = f'tcp:{host}:{port}'
        if not isinstance(expected_target, str) or target_id.strip().lower() != expected_target.strip().lower():
            return None

    return target_id


def build_numeric_profile_candidate(agent_id, metric_name, target_id, unit, samples, version=1):
    """Build an unapproved profile candidate from one explicitly selected healthy window."""
    if not all(isinstance(value, str) and value.strip() for value in (agent_id, metric_name, target_id, unit)):
        raise ValueError('Agent, metric, target, and unit are required')

    values = []
    timestamps = []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get('agent_id') != agent_id:
            raise ValueError('Every calibration sample must belong to the selected agent')
        timestamp = sample.get('timestamp')
        if not isinstance(timestamp, str):
            raise ValueError('Every calibration sample must have a timestamp')
        try:
            timestamps.append(parse_iso_timestamp(timestamp))
        except ValueError as exc:
            raise ValueError('Calibration sample has an invalid timestamp') from exc

        if metric_name == 'utilization_pct':
            if not target_id.startswith('interface:'):
                raise ValueError('Interface utilization profiles require an interface target')
            interface_name = target_id.removeprefix('interface:')
            interfaces = sample.get('interface_metrics')
            if not isinstance(interfaces, list):
                raise ValueError('Calibration sample is missing interface metrics')
            matching_interface = next((
                item for item in interfaces
                if isinstance(item, dict) and item.get('name') == interface_name
            ), None)
            value = matching_interface.get('utilization_pct') if matching_interface else None
        else:
            targets = sample.get('metric_targets')
            if not isinstance(targets, dict) or targets.get(metric_name) != target_id:
                raise ValueError('Calibration sample target does not match the selected target')
            value = sample.get(metric_name)

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Calibration sample contains an invalid metric value')
        values.append(value)

    if len(values) < MIN_CALIBRATION_SAMPLES:
        raise ValueError(f'At least {MIN_CALIBRATION_SAMPLES} samples are required')
    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise ValueError('Calibration samples must be strictly timestamp-ordered')
    duration_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
    if duration_seconds < MIN_CALIBRATION_DURATION_SECONDS:
        raise ValueError(f'Calibration window must span at least {MIN_CALIBRATION_DURATION_SECONDS} seconds')

    statistics = derive_upper_bound(values)
    return {
        'target_id': target_id,
        'unit': unit,
        'method': 'max(p95, median + 3 * 1.4826 * MAD)',
        **statistics,
        'calibration_started_at': timestamps[0].isoformat(),
        'calibration_completed_at': timestamps[-1].isoformat(),
        'approved': False,
        'approved_by': None,
        'approved_at': None,
        'version': version,
    }


def _candidate_cli(argv=None):
    parser = argparse.ArgumentParser(description='Build an unapproved healthy calibration profile candidate')
    parser.add_argument('--agent-id', required=True)
    parser.add_argument('--metric', required=True)
    parser.add_argument('--target-id', required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--since', required=True, help='Inclusive ISO timestamp')
    parser.add_argument('--until', required=True, help='Inclusive ISO timestamp')
    parser.add_argument('--confirm-healthy-window', action='store_true')
    parser.add_argument('--history', default=os.path.join('data', 'healing', 'agent_metrics_history.json'))
    parser.add_argument('--output', required=True, help='Candidate JSON output path')
    args = parser.parse_args(argv)
    if not args.confirm_healthy_window:
        parser.error('Review the selected window and pass --confirm-healthy-window only when it was healthy')

    try:
        since = parse_iso_timestamp(args.since)
        until = parse_iso_timestamp(args.until)
    except ValueError:
        parser.error('--since and --until must be valid ISO timestamps')
    if until <= since:
        parser.error('--until must be later than --since')
    with open(args.history, 'r', encoding='utf-8') as history_file:
        history = json.load(history_file)
    if not isinstance(history, dict) or not isinstance(history.get(args.agent_id), list):
        parser.error('No persisted metric history found for the selected agent')
    selected_samples = []
    for sample in history[args.agent_id]:
        try:
            timestamp = parse_iso_timestamp(sample.get('timestamp', ''))
        except (AttributeError, TypeError, ValueError):
            continue
        if since <= timestamp <= until:
            selected_samples.append(sample)

    try:
        candidate = build_numeric_profile_candidate(
            args.agent_id,
            args.metric,
            args.target_id,
            args.unit,
            selected_samples,
        )
    except ValueError as exc:
        parser.error(str(exc))

    document = {
        'agent_id': args.agent_id,
        'metric_name': args.metric,
        'profile': candidate,
    }
    output_directory = os.path.dirname(args.output)
    if output_directory:
        os.makedirs(output_directory, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as output_file:
        json.dump(document, output_file, indent=2)
    print(f"Unapproved profile candidate written to {args.output}")


if __name__ == '__main__':
    _candidate_cli()