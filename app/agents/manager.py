"""
Manages connected agents, stores metrics, and handles command dispatching.
"""
import logging
import json
import os
import threading
from datetime import datetime

logger = logging.getLogger('agent_manager')
METRICS_HISTORY_FILE = os.path.join('data', 'healing', 'agent_metrics_history.json')


def get_expected_api_keys():
    """Return all accepted API keys from the environment."""
    keys = []
    for key_name in ('AGENT_API_KEY', 'API_KEY'):
        value = os.environ.get(key_name)
        if value:
            keys.append(value)
    if not keys:
        keys.append('your-secret-key')
    return keys


class AgentManager:
    def __init__(self, metrics_history_file=METRICS_HISTORY_FILE):
        self.agents = {}  # agent_id -> {sid, hostname, os, ip, connected_at, last_heartbeat, metrics, ...}
        self.metrics_history_file = metrics_history_file
        self.metrics_history = self._load_metrics_history()
        self.agent_sid_map = {}  # sid -> agent_id
        self.last_command_dispatch = {}  # agent_id -> datetime
        self.command_lock = threading.Lock()
        self.metrics_lock = threading.Lock()

    def _load_metrics_history(self):
        if not os.path.exists(self.metrics_history_file):
            return {}
        try:
            with open(self.metrics_history_file, 'r', encoding='utf-8') as history_file:
                history = json.load(history_file)
            if not isinstance(history, dict):
                logger.warning("Persisted agent metrics history is not an object; starting empty")
                return {}
            return {
                agent_id: samples[-100:]
                for agent_id, samples in history.items()
                if isinstance(agent_id, str) and isinstance(samples, list)
            }
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load persisted agent metrics history: %s", exc)
            return {}

    def _save_metrics_history(self):
        try:
            directory = os.path.dirname(self.metrics_history_file)
            if directory:
                os.makedirs(directory, exist_ok=True)
            temporary_file = f'{self.metrics_history_file}.tmp'
            with open(temporary_file, 'w', encoding='utf-8') as history_file:
                json.dump(self.metrics_history, history_file)
            os.replace(temporary_file, self.metrics_history_file)
        except OSError:
            logger.exception("Could not persist agent metrics history")

    def register_agent(self, sid, data):
        """Register a new agent."""
        agent_id = data.get('agent_id')
        if not agent_id:
            return False

        # Check API key (optional)
        api_key = data.get('api_key')
        expected_keys = get_expected_api_keys()
        if api_key not in expected_keys:
            logger.warning(
                f"Invalid API key from agent {agent_id}: got '{api_key}', expected one of {expected_keys}"
            )
            return False

        previous_agent = self.agents.get(agent_id)
        if previous_agent and previous_agent.get('sid') != sid:
            self.agent_sid_map.pop(previous_agent.get('sid'), None)

        self.agents[agent_id] = {
            'agent_id': agent_id,
            'sid': sid,
            'hostname': data.get('hostname'),
            'os': data.get('os'),
            'os_version': data.get('os_version'),
            'ip': data.get('ip'),
            'connected_at': datetime.now().isoformat(),
            'last_heartbeat': datetime.now().isoformat(),
            'status': 'online'
        }
        self.agent_sid_map[sid] = agent_id
        self.metrics_history.setdefault(agent_id, [])
        logger.info(f"Agent registered: {agent_id} ({data.get('hostname')})")
        return True

    def update_metrics(self, agent_id, metrics):
        """Store metrics for an agent."""
        if agent_id not in self.agents:
            return False
        self.agents[agent_id]['last_heartbeat'] = datetime.now().isoformat()
        # Store last N metrics (e.g., 100)
        with self.metrics_lock:
            history = self.metrics_history.get(agent_id, [])
            history.append(dict(metrics))
            self.metrics_history[agent_id] = history[-100:]
            self._save_metrics_history()
        return True

    def attach_detection_id(self, agent_id, detection_id):
        """Associate the most recent stored metric sample with its model observation."""
        if not isinstance(detection_id, str) or not detection_id:
            return False
        with self.metrics_lock:
            history = self.metrics_history.get(agent_id)
            if not history:
                return False
            history[-1]['detection_id'] = detection_id
            self._save_metrics_history()
        return True

    def get_agent_sid(self, agent_id):
        """Get Socket.IO session ID for an agent."""
        agent = self.agents.get(agent_id)
        if not agent:
            return None
        sid = agent.get('sid')
        return sid if sid and self.agent_sid_map.get(sid) == agent_id else None

    def get_all_agents(self):
        """Return list of all agents."""
        return [dict(agent) for agent in self.agents.values()]

    def remove_agent(self, sid):
        """Remove agent on disconnect."""
        agent_id = self.agent_sid_map.pop(sid, None)
        if agent_id:
            self.agents.pop(agent_id, None)
            logger.info(f"Agent removed: {agent_id}")

    def reserve_command_slot(self, agent_id, interval_seconds=60):
        """Reserve this agent's one command slot for the configured interval."""
        now = datetime.now()
        with self.command_lock:
            previous = self.last_command_dispatch.get(agent_id)
            if previous:
                elapsed = (now - previous).total_seconds()
                if elapsed < interval_seconds:
                    return False, int(interval_seconds - elapsed)
            self.last_command_dispatch[agent_id] = now
            return True, 0

    def release_command_slot(self, agent_id, reserved_at):
        """Release a reservation only when it still belongs to this dispatch."""
        with self.command_lock:
            if self.last_command_dispatch.get(agent_id) == reserved_at:
                self.last_command_dispatch.pop(agent_id, None)

# Global instance
agent_manager = AgentManager()
