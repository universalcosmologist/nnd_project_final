"""Optional constrained LLM action ranking for agent issue resolution."""
import json
import logging
import os
import math
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger('agent_decision')
DEFAULT_BASE_URL = 'https://api.openai.com/v1/chat/completions'
MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 3


def _validated_provider_config():
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini').strip()
    base_url = os.environ.get('OPENAI_BASE_URL', DEFAULT_BASE_URL).strip()
    parsed_url = urlsplit(base_url)
    is_loopback = parsed_url.hostname in {'localhost', '127.0.0.1', '::1'}
    if parsed_url.scheme != 'https' and not (parsed_url.scheme == 'http' and is_loopback):
        raise ValueError('LLM provider URL must use HTTPS except for loopback development')
    if (
        not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError('LLM provider URL is invalid')
    if not api_key or not model:
        raise ValueError('LLM provider credentials or model are missing')
    return api_key, model, base_url


def llm_action_decision(candidates, evidence):
    """Ask an explicitly enabled LLM to rank eligible action IDs only."""
    enabled = os.environ.get('ACTION_LLM_ENABLED', '').strip().lower() in {'1', 'true', 'yes'}
    if not enabled or not candidates:
        return None

    try:
        minimum_confidence = float(os.environ.get('ACTION_LLM_MIN_CONFIDENCE', '0.75'))
        timeout_seconds = float(os.environ.get('ACTION_LLM_TIMEOUT_SECONDS', str(DEFAULT_TIMEOUT_SECONDS)))
        api_key, model, base_url = _validated_provider_config()
        if not math.isfinite(minimum_confidence) or not 0 <= minimum_confidence <= 1:
            raise ValueError('LLM confidence threshold must be between 0 and 1')
        if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 10:
            raise ValueError('LLM timeout must be between 0.1 and 10 seconds')
        allowed_candidate_ids = {
            candidate['id'] for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get('id'), str)
        }
        if not allowed_candidate_ids:
            return None
    except (TypeError, ValueError) as exc:
        logger.warning('Invalid LLM action-ranking configuration; using deterministic policy (%s)', type(exc).__name__)
        return None

    payload = {
        'model': model,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
        'messages': [
            {
                'role': 'system',
                'content': (
                    'Choose the most suitable action from the provided candidate IDs, or abstain. '
                    'Never invent commands or parameters. Return JSON with action_id, confidence '
                    '(0 to 1), and a short rationale. Use null action_id to abstain.'
                ),
            },
            {
                'role': 'user',
                'content': json.dumps({'evidence': evidence, 'candidates': candidates}),
            },
        ],
    }
    request = Request(
        base_url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise ValueError('LLM provider response exceeded size limit')
        response_payload = json.loads(response_body.decode('utf-8'))
        content = response_payload['choices'][0]['message']['content']
        result = json.loads(content)
        action_id = result.get('action_id')
        confidence = result.get('confidence')
        rationale = result.get('rationale')
        if action_id is not None and action_id not in allowed_candidate_ids:
            return None
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            return None
        if action_id is None or confidence < minimum_confidence:
            return None
        if not isinstance(rationale, str):
            rationale = ''
        return {
            'action_id': action_id,
            'confidence': float(confidence),
            'rationale': rationale[:300],
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning('LLM action selection unavailable; using rule-based selection (%s)', type(exc).__name__)
        return None


def build_decision_evidence(anomaly, agent):
    """Build a small allowlisted, non-identifying evidence payload for ranking."""
    details = anomaly.get('details', {})
    context = anomaly.get('agent_context', {})
    feature_values = details.get('features', {}) if isinstance(details, dict) else {}
    metric_keys = (
        'latency', 'packet_loss', 'usage', 'threshold', 'latency_ms',
        'packet_loss_pct', 'bandwidth_mbps', 'cpu_percent', 'memory_percent',
        'max_interface_utilization_pct', 'average_utilization_pct', 'speed_mbps',
    )
    metrics = {}
    for key in metric_keys:
        value = feature_values.get(key, details.get(key) if isinstance(details, dict) else None)
        if value is None:
            value = context.get(key)
        if isinstance(value, (int, float, bool)):
            metrics[key] = value
    for key in ('dns_ok', 'connection_ok'):
        if isinstance(context.get(key), bool):
            metrics[key] = context[key]

    os_name = (agent.get('os') or context.get('os') or '').strip().lower()
    return {
        'anomaly_type': anomaly.get('type') if isinstance(anomaly.get('type'), str) else 'unknown',
        'anomaly_score': anomaly.get('score') if isinstance(anomaly.get('score'), (int, float)) else None,
        'os': os_name,
        'metrics': metrics,
    }
