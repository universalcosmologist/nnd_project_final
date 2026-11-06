"""
Socket.IO event handlers for agent namespace.
"""
from flask_socketio import emit, join_room, leave_room
from flask import request
from app import socketio
from app.agents.manager import agent_manager
from app.agents import decision as decision_support
from app.services import anomaly_detector as detector, network_resolver as resolver
import logging
import os
import uuid
from datetime import datetime

# Map command_id -> issue_id
command_to_issue = {}

logger = logging.getLogger('agent_handlers')
SATURATION_WINDOW_SAMPLES = 3
SATURATION_UTILIZATION_THRESHOLD = 80.0
SATURATION_IMPACT_SAMPLE_THRESHOLD = 2
SATURATION_MAX_WINDOW_SECONDS = 60
SATURATION_MAX_SAMPLE_GAP_SECONDS = 30


def _has_network_impact(metrics):
    latency = metrics.get('latency_ms')
    packet_loss = metrics.get('packet_loss_pct')
    return (
        isinstance(latency, (int, float)) and latency >= 200
        or isinstance(packet_loss, (int, float)) and packet_loss >= 10
        or metrics.get('connection_ok') is False
    )

HEALING_ACTIONS = {
    'flush_dns_cache': {
        'name': 'Flush DNS cache',
        'supported_os': {'windows', 'linux', 'darwin'},
    },
    'check_connectivity': {
        'name': 'Check external connectivity',
        'supported_os': {'windows', 'linux', 'darwin'},
    },
    'inspect_interface_counters': {
        'name': 'Inspect network interface counters',
        'supported_os': {'windows', 'linux', 'darwin'},
    },
}


def select_healing_action(anomaly, agent):
    """Select a supported action from issue evidence and the agent OS."""
    agent_context = anomaly.get('agent_context', {})
    os_name = (agent.get('os') or agent_context.get('os') or '').strip().lower()
    os_key = {'windows': 'windows', 'linux': 'linux', 'darwin': 'darwin'}.get(os_name)
    if not os_key:
        return None

    anomaly_type = anomaly.get('type')
    details = anomaly.get('details', {})
    features = details.get('features', {}) if isinstance(details, dict) else {}
    candidate_ids = []
    rule_rationale = None

    if anomaly_type == 'dns_resolution_failure' or agent_context.get('dns_ok') is False:
        candidate_ids.append('flush_dns_cache')
        rule_rationale = 'DNS resolution failed according to the anomaly or agent connectivity checks.'
    if anomaly_type in ('high_latency', 'packet_loss', 'connection_timeout'):
        candidate_ids.append('check_connectivity')
        if rule_rationale is None:
            rule_rationale = f'{anomaly_type.replace("_", " ").capitalize()} requires a read-only connectivity check.'
    if anomaly_type == 'bandwidth_saturation':
        candidate_ids.append('inspect_interface_counters')
        rule_rationale = 'Bandwidth saturation requires interface counters for diagnosis.'
    if anomaly_type == 'general_anomaly':
        latency = features.get('latency', 0)
        packet_loss = features.get('packet_loss', 0)
        if agent_context.get('connection_ok') is False or latency >= 200 or packet_loss >= 10:
            candidate_ids.append('check_connectivity')
            rule_rationale = 'General anomaly includes degraded connectivity metrics.'
    if anomaly_type == 'dns_resolution_failure' and 'check_connectivity' not in candidate_ids:
        candidate_ids.append('check_connectivity')

    candidate_ids = [
        action_id for action_id in candidate_ids
        if action_id in HEALING_ACTIONS and os_key in HEALING_ACTIONS[action_id]['supported_os']
    ]
    if not candidate_ids:
        return None

    selected_id = candidate_ids[0]
    rationale = rule_rationale or 'Selected by the deterministic issue policy.'
    source = 'rules'
    confidence = None
    try:
        llm_decision = decision_support.llm_action_decision(
            [
                {'id': action_id, 'name': HEALING_ACTIONS[action_id]['name']}
                for action_id in candidate_ids
            ],
            decision_support.build_decision_evidence(anomaly, agent),
        )
        minimum_confidence = float(os.environ.get('ACTION_LLM_MIN_CONFIDENCE', '0.75'))
        if (
            llm_decision
            and llm_decision.get('action_id') in candidate_ids
            and isinstance(llm_decision.get('confidence'), (int, float))
            and llm_decision['confidence'] >= minimum_confidence
        ):
            selected_id = llm_decision['action_id']
            rationale = llm_decision.get('rationale') or rationale
            confidence = llm_decision['confidence']
            source = 'llm'
    except (TypeError, ValueError):
        logger.warning('Invalid LLM confidence threshold; using deterministic action policy')

    action = HEALING_ACTIONS[selected_id]
    return {
        'id': selected_id,
        'name': action['name'],
        'parameters': {},
        'rationale': rationale,
        'decision_source': source,
        'confidence': confidence,
    }


def detect_bandwidth_saturation(agent_id, current_metrics):
    """Confirm saturation only when utilization and network impact persist."""
    if any(
        issue.get('anomaly', {}).get('agent_id') == agent_id
        and issue.get('anomaly', {}).get('type') == 'bandwidth_saturation'
        and issue.get('status') not in ('resolved', 'failed')
        for issue in resolver.active_issues.values()
    ):
        return None

    history = agent_manager.metrics_history.get(agent_id, [])
    window = history[-SATURATION_WINDOW_SAMPLES:]
    if len(window) < SATURATION_WINDOW_SAMPLES:
        return None
    try:
        timestamps = [datetime.fromisoformat(metrics['timestamp']) for metrics in window]
    except (KeyError, TypeError, ValueError):
        return None
    sample_gaps = [
        (timestamps[index] - timestamps[index - 1]).total_seconds()
        for index in range(1, len(timestamps))
    ]
    if (
        any(gap <= 0 or gap > SATURATION_MAX_SAMPLE_GAP_SECONDS for gap in sample_gaps)
        or (timestamps[-1] - timestamps[0]).total_seconds() > SATURATION_MAX_WINDOW_SECONDS
    ):
        return None

    interface_samples = []
    for metrics in window:
        reported_interfaces = metrics.get('interface_metrics', [])
        if not isinstance(reported_interfaces, list):
            return None
        interfaces = {
            item.get('name'): item
            for item in reported_interfaces
            if isinstance(item, dict)
            and isinstance(item.get('name'), str)
            and item.get('name')
            and isinstance(item.get('utilization_pct'), (int, float))
        }
        interface_samples.append(interfaces)
    common_names = set(interface_samples[0])
    for samples in interface_samples[1:]:
        common_names.intersection_update(samples)

    for interface_name in common_names:
        samples = [interfaces[interface_name] for interfaces in interface_samples]
        utilization = [sample['utilization_pct'] for sample in samples]
        if not all(value >= SATURATION_UTILIZATION_THRESHOLD for value in utilization):
            continue

        impacts = sum(_has_network_impact(metrics) for metrics in window)
        if impacts < SATURATION_IMPACT_SAMPLE_THRESHOLD:
            continue

        return {
            'type': 'bandwidth_saturation',
            'score': min(utilization) / 100,
            'details': {
                'interface': interface_name,
                'utilization_pct': utilization,
                'average_utilization_pct': round(sum(utilization) / len(utilization), 2),
                'speed_mbps': samples[-1].get('speed_mbps'),
                'latency_ms': [metrics.get('latency_ms') for metrics in window],
                'packet_loss_pct': [metrics.get('packet_loss_pct') for metrics in window],
                'window_samples': len(window),
                'impact_samples': impacts,
            },
            'detected_at': current_metrics.get('timestamp'),
        }
    return None


def dispatch_issue_to_agent(issue_id, bypass_rate_limit=False):
    """Send one issue to its owning online agent and record an outstanding command.

    Dashboard-initiated manual resolution can bypass the automatic one-minute
    per-agent rate limit; background dispatches cannot.
    """
    issue = resolver.active_issues.get(issue_id)
    if not issue:
        return {'success': False, 'message': 'Issue not found'}
    if issue.get('status') not in ('new', 'pending'):
        return {'success': False, 'message': f"Issue cannot be dispatched from state {issue.get('status')}"}

    anomaly = issue.get('anomaly', {})
    agent_id = anomaly.get('agent_id')
    baseline_outcome = resolver.evaluate_pre_action_baseline(issue)
    if baseline_outcome is True:
        resolver.record_false_positive(issue_id)
        return {
            'success': True,
            'false_positive': True,
            'message': 'Detection was already within the approved healthy profile; no action was sent',
        }
    if baseline_outcome is False:
        anomaly['ground_truth'] = {
            'actual_anomaly': True,
            'status': 'verified',
            'source': 'approved_profile_exceedance',
        }
    agent = agent_manager.agents.get(agent_id, {}) if agent_id else {}
    action = select_healing_action(anomaly, agent)
    sid = agent_manager.get_agent_sid(agent_id) if agent_id else None
    if not agent_id or not sid:
        return {'success': False, 'message': 'Agent is not connected'}
    if not action:
        return {'success': False, 'message': 'No supported action for this issue and agent OS'}
    if issue.get('status') == 'awaiting_agent':
        return {'success': False, 'message': 'Already waiting for agent response'}

    reserved_at = None
    if not bypass_rate_limit:
        allowed, retry_after = agent_manager.reserve_command_slot(agent_id)
        if not allowed:
            return {
                'success': False,
                'message': f'Agent command rate limit active; retry in {retry_after} seconds'
            }
        reserved_at = agent_manager.last_command_dispatch[agent_id]

    command_id = str(uuid.uuid4())
    command_to_issue[command_id] = issue_id
    issue['resolution_decision'] = {
        'action_id': action['id'],
        'action_name': action['name'],
        'rationale': action['rationale'],
        'source': action['decision_source'],
        'confidence': action['confidence'],
    }
    metric_history = agent_manager.metrics_history.get(agent_id, [])
    metric_snapshot = dict(metric_history[-1]) if metric_history else {}
    anomaly_snapshot = anomaly.get('metric_snapshot')
    if isinstance(anomaly_snapshot, dict):
        metric_snapshot.update(anomaly_snapshot)
    anomaly_details = anomaly.get('details', {})
    anomaly_features = anomaly_details.get('features', {}) if isinstance(anomaly_details, dict) else {}
    metric_snapshot.setdefault('latency_ms', anomaly_features.get('latency', anomaly_details.get('latency') if isinstance(anomaly_details, dict) else None))
    metric_snapshot.setdefault('packet_loss_pct', anomaly_features.get('packet_loss', anomaly_details.get('packet_loss') if isinstance(anomaly_details, dict) else None))
    for key in ('dns_ok', 'connection_ok'):
        if key not in metric_snapshot and key in anomaly.get('agent_context', {}):
            metric_snapshot[key] = anomaly['agent_context'][key]
    metric_targets = anomaly.get('agent_context', {}).get('metric_targets', {})
    if not isinstance(metric_targets, dict):
        metric_targets = {}
    target_ids = {
        key: value
        for key, value in metric_targets.items()
        if isinstance(value, str)
    }
    baseline = {
        'anomaly_type': anomaly.get('type'),
        'model_confidence': anomaly.get('confidence', anomaly.get('score')),
        'anomaly_details': anomaly_details,
        'metric_snapshot': metric_snapshot,
        'chosen_action': dict(issue['resolution_decision']),
        'captured_at': datetime.now().isoformat(),
        'agent_id': agent_id,
        'target_ids': target_ids,
    }
    # Persist this state before emitting. A sent command is never a resolved issue.
    resolver.mark_command_dispatched(
        issue_id,
        command_id,
        f"action:{action['id']}",
        action_id=action['id'],
        baseline_metrics=baseline,
    )
    try:
        socketio.emit('run_command', {
            'action_id': action['id'],
            'parameters': action['parameters'],
            'command_id': command_id,
            'issue_id': issue_id,
        }, room=sid, namespace='/agent')
        logger.info("Sent command %s for issue %s to agent %s", command_id, issue_id, agent_id)
        return {'success': True, 'message': 'Resolution command sent; waiting for agent result', 'command_id': command_id}
    except Exception as exc:
        command_to_issue.pop(command_id, None)
        if reserved_at:
            agent_manager.release_command_slot(agent_id, reserved_at)
        resolver.mark_command_dispatch_failed(issue_id, str(exc))
        logger.exception("Could not send command %s", command_id)
        return {'success': False, 'message': f'Could not send command: {exc}'}


def dispatch_pending_issues_for_agent(agent_id=None):
    """Dispatch persisted retryable issues for an agent that is now online."""
    for issue_id in resolver.get_retryable_agent_issue_ids(agent_id):
        dispatch_issue_to_agent(issue_id)

@socketio.on('connect', namespace='/agent')
def handle_agent_connect():
    """Agent connection event."""
    logger.info(f"Agent connected: {request.sid}")
    # Agent will send registration; we'll handle in 'agent_register'

@socketio.on('disconnect', namespace='/agent')
def handle_agent_disconnect():
    """Agent disconnection."""
    sid = request.sid
    agent_manager.remove_agent(sid)
    logger.info(f"Agent disconnected: {sid}")
    socketio.emit('agents_update', agent_manager.get_all_agents(), namespace='/')

@socketio.on('agent_register', namespace='/agent')
def handle_agent_register(data):
    """Agent registration with authentication."""
    sid = request.sid
    logger.info(f"Received agent_register event from {sid}")
    logger.info(f"Registration data: agent_id={data.get('agent_id')}, hostname={data.get('hostname')}")
    result = agent_manager.register_agent(sid, data)
    logger.info(f"Agent registration result: {result}")
    if result:
        logger.info(f"Successfully registered agent {data.get('agent_id')}")
        emit('registration_ack', {'status': 'ok', 'agent_id': data.get('agent_id')}, room=sid)
        # Broadcast agent list update to all web clients if needed
        agents = agent_manager.get_all_agents()
        logger.info(f"Broadcasting agents_update with {len(agents)} agents")
        socketio.emit('agents_update', agents, namespace='/')
        dispatch_pending_issues_for_agent(data.get('agent_id'))
    else:
        logger.warning(f"Failed to register agent {data.get('agent_id')}")
        emit('registration_ack', {'status': 'failed', 'reason': 'Invalid credentials'}, room=sid)
        # Disconnect agent? We can let it retry.

@socketio.on('agent_metrics', namespace='/agent')
def handle_agent_metrics(data):
    """Receive metrics from agent and run anomaly detection."""
    agent_id = data.get('agent_id')
    if not agent_id:
        return

    # Do not let an unregistered sender create issues or contribute evidence.
    if not agent_manager.update_metrics(agent_id, data):
        logger.warning("Ignoring metrics from unregistered agent %s", agent_id)
        return

    # Verify outstanding actions before processing new anomaly detections.
    for issue_id, issue in list(resolver.active_issues.items()):
        if issue.get('anomaly', {}).get('agent_id') == agent_id and issue.get('status') == 'awaiting_verification':
            resolver.add_agent_verification_sample(issue_id, data)

    # Add saturation only when utilization and impact persist in the sample window.
    bandwidth_anomaly = detect_bandwidth_saturation(agent_id, data)

    # Run anomaly detection using the metrics
    # We need to map agent metrics to network_status format expected by detector
    # For simplicity, we'll create a network_status dict
    network_status = {
        'latency': data.get('latency_ms', 0),
        'bandwidth_usage': data.get('bandwidth_mbps', 0),
        'packet_loss': data.get('packet_loss_pct', 0),
        'latency_ms': data.get('latency_ms'),
        'bandwidth_mbps': data.get('bandwidth_mbps'),
        'packet_loss_pct': data.get('packet_loss_pct'),
        'agent_id': agent_id,
        'timestamp': data.get('timestamp'),
        'metric_targets': data.get('metric_targets', {}),
        # additional fields if needed
    }
    anomalies = detector.detect_anomalies(network_status)
    detection_id = network_status.get('_detection_id')
    if detection_id:
        agent_manager.attach_detection_id(agent_id, detection_id)
    if bandwidth_anomaly:
        anomalies.append(bandwidth_anomaly)

    if anomalies:
        agent = agent_manager.agents.get(agent_id, {})
        metric_context = {
            key: data[key]
            for key in (
                'dns_ok', 'connection_ok', 'cpu_percent', 'memory_percent',
                'interface_metrics', 'max_interface_utilization_pct',
                'metric_targets', 'timestamp',
            )
            if key in data
        }
        agent_context = {
            key: agent[key]
            for key in ('hostname', 'os', 'os_version')
            if agent.get(key) is not None
        }
        agent_context.update(metric_context)

        for anomaly in anomalies:
            # Add agent_id to anomaly for tracking
            anomaly['agent_id'] = agent_id
            anomaly['agent_context'] = agent_context.copy()
            anomaly['metric_snapshot'] = dict(data)
            anomaly.setdefault('detection_id', detection_id)
            anomaly.setdefault('model_predictions', {
                'isolation_forest': None,
                'supervised': network_status.get('_supervised_prediction'),
            })
            # Create issue via resolver
            issue_id = resolver.handle_anomaly(anomaly)
            result = dispatch_issue_to_agent(issue_id)
            if not result['success']:
                logger.info("Issue %s remains pending: %s", issue_id, result['message'])
            logger.info(f"Created issue {issue_id} for agent {agent_id}")

            # Optionally, automatically send a healing command if needed
            # For example, if anomaly type is 'high_latency', send flush DNS
            # We'll implement command dispatching in a separate function
            # self._dispatch_healing_command(agent_id, issue_id)

@socketio.on('command_result', namespace='/agent')
def handle_command_result(data):
    command_id = data.get('command_id')
    success = data.get('success', False)
    message = data.get('message') or data.get('error', '')
    logger.info(f"Command {command_id} result: success={success}, message={message}")

    # Look up the associated issue
    issue_id = command_to_issue.pop(command_id, None) or data.get('issue_id')
    if issue_id:
        resolver.update_issue_status(issue_id, success, message, command_id=command_id)
    else:
        logger.warning(f"No pending issue found for command {command_id}")
