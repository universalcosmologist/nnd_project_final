import time
import random
import os
import logging
import socket
import threading
import subprocess
import platform
from datetime import datetime

# Set up logging to both console and file
if not os.path.exists('logs'):
    os.makedirs('logs')

# Configure logger
logger = logging.getLogger('network_monitor')
logger.setLevel(logging.INFO)

# Only add handlers if none exist to avoid duplicate logs
if not logger.handlers:
    # Create handlers
    file_handler = logging.FileHandler('logs/app.log')
    console_handler = logging.StreamHandler()
    
    # Create formatter and add to handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Configuration for network monitoring
# Default to simulation mode, can be overridden by environment variable we changing it now 
SIMULATION_MODE = os.environ.get('SIMULATION_MODE', 'false').lower() == 'true'
NETWORK_TEST_HOST = os.environ.get('NETWORK_TEST_HOST', '8.8.8.8')
logger.info(f"Network monitoring mode: {'SIMULATION' if SIMULATION_MODE else 'REAL'}")

# Global network status
network_status = {
    'last_updated': None,
    'overall_health': 'good',
    'bandwidth_usage': 0,
    'latency': 0,
    'packet_loss': 0,
    'connected_devices': 0,
    'simulation_mode': SIMULATION_MODE,
    'metrics_history': []
}

def get_network_status():
    """Return the current network status"""
    return network_status

def ping_host(host):
    """Check if a host is reachable using socket connection"""
    if SIMULATION_MODE:
        return ping_host_simulated(host)
    else:
        return ping_host_real(host)

def ping_host_simulated(host):
    """Simulated ping to a host"""
    try:
        # For demonstration - simulate connection failures more frequently
        if random.random() < 0.4:  # 40% chance of connection failure
            logger.info(f"Simulating connection failure to {host}")
            return None

        # Use socket instead of ping command
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex((host, 80))
        s.close()
        
        if result == 0:
            return f"Socket connection to {host} successful"
        else:
            return None
    except Exception as e:
        logger.warning(f"Socket connection to {host} failed: {e}")
        return None

def ping_host_real(host):
    """Real ping to a host using HTTP requests"""
    try:
        import requests
        response = requests.get(f"http://{host}", timeout=2)
        if response.status_code < 400:
            return f"HTTP connection to {host} successful"
        else:
            return None
    except Exception as e:
        logger.warning(f"HTTP connection to {host} failed: {e}")
        return None

def measure_latency(host, count=4):
    """Measure network latency using HTTP requests"""
    if SIMULATION_MODE:
        return random.uniform(10, 100)
    
    try:
        import requests
        
        latencies = []
        for _ in range(count):
            start_time = time.time()
            try:
                # Use HTTP GET request instead of ICMP ping
                response = requests.get(f"http://{host}", timeout=2)
                end_time = time.time()
                latency = (end_time - start_time) * 1000  # Convert to ms
                latencies.append(latency)
            except requests.exceptions.RequestException:
                latencies.append(1000)  # Default high latency on error
            
            time.sleep(0.2)  # Small delay between requests
            
        # Calculate average latency
        return sum(latencies) / len(latencies) if latencies else 1000
    except Exception as e:
        logger.error(f"Error measuring latency: {e}")
        return 1000  # Default high latency on error

def measure_packet_loss(host, count=4):
    """Measure packet loss percentage"""
    if SIMULATION_MODE:
        # Simulate packet loss
        return random.uniform(0, 10)
    
    try:
        import requests
        
        successes = 0
        for _ in range(count):
            try:
                # Use HTTP GET request
                response = requests.get(f"http://{host}", timeout=2)
                if response.status_code < 400:
                    successes += 1
            except requests.exceptions.RequestException:
                pass  # Count as a failure
            
            time.sleep(0.2)  # Small delay between requests
            
        # Calculate packet loss percentage
        return 100 - (successes / count * 100)
    except Exception as e:
        logger.error(f"Error measuring packet loss: {e}")
        return 100  # Default high packet loss on error

def measure_ping_stats(host, count=4):
    """Measure ICMP latency and packet loss using the platform ping command."""
    if SIMULATION_MODE:
        return random.uniform(10, 100), random.uniform(0, 10)

    try:
        command = ['ping', '-n' if platform.system() == 'Windows' else '-c', str(count), host]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        output = f"{result.stdout}\n{result.stderr}"

        import re
        loss_match = re.search(r'(\d+(?:\.\d+)?)%\s*(?:loss|packet loss)', output, re.IGNORECASE)
        average_match = re.search(r'(?:Average|avg)[^=]*=\s*(\d+(?:\.\d+)?)\s*ms', output, re.IGNORECASE)
        if not average_match:
            average_match = re.search(
                r'=\s*\d+(?:\.\d+)?/(\d+(?:\.\d+)?)/', output, re.IGNORECASE
            )
        if not average_match:
            average_match = re.search(r'=\s*(\d+(?:\.\d+)?)\s*ms', output, re.IGNORECASE)

        packet_loss = float(loss_match.group(1)) if loss_match else 100.0
        latency = float(average_match.group(1)) if average_match else 1000.0
        return latency, packet_loss
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Unable to measure ICMP stats for %s: %s", host, exc)
        return 1000.0, 100.0

def measure_bandwidth():
    """Measure network bandwidth"""
    if SIMULATION_MODE:
        return random.uniform(5, 50)
    
    try:
        import psutil
        
        # Get initial counters
        net1 = psutil.net_io_counters()
        time.sleep(1)
        net2 = psutil.net_io_counters()
        
        # Calculate bytes per second
        bytes_sent = net2.bytes_sent - net1.bytes_sent
        bytes_recv = net2.bytes_recv - net1.bytes_recv
        total_bytes = bytes_sent + bytes_recv
        
        # Convert to Mbps
        mbps = (total_bytes * 8) / 1000000
        return mbps
        
    except Exception as e:
        logger.error(f"Error measuring bandwidth: {e}")
        # Fallback: simulate
        return random.uniform(5, 50)

def scan_local_network():
    """Scan local network for devices"""
    if SIMULATION_MODE:
        return scan_local_network_simulated()
    else:
        return scan_local_network_real()

def scan_local_network_simulated():
    """Simulated network scan"""
    devices = []
    for i in range(1, 4):  # Simulating 3 devices
        # For demonstration - generate more device issues
        status_chance = random.random()
        if status_chance < 0.5:  # 50% chance to be up
            status = 'up'
        elif status_chance < 0.8:  # 30% chance to be down
            status = 'down'
        else:  # 20% chance to be in warning state
            status = 'warning'
            
        devices.append({
            'ip': f'192.168.1.{i}',
            'mac': f'00:00:00:00:00:0{i}',
            'hostname': f'device-{i}',
            'status': status
        })
    return devices

def scan_local_network_real():
    """Scan accessible network in Codespaces"""
    devices = []
    try:
        # Get container's network information
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        # Parse the subnet from the local IP
        subnet_base = '.'.join(local_ip.split('.')[:3]) + '.'
        
        # Add the container itself
        devices.append({
            'ip': local_ip,
            'mac': 'container',
            'hostname': hostname,
            'status': 'up'
        })
        
        # Scan a limited range in the container's subnet
        for i in range(1, 20):  # Limit to 20 addresses to avoid performance issues
            target_ip = f"{subnet_base}{i}"
            if target_ip == local_ip:
                continue  # Skip self, already added
                
            # Check if port 22 (SSH) is open - a common service
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((target_ip, 22))
            sock.close()
            status = 'up' if result == 0 else 'down'
            
            devices.append({
                'ip': target_ip,
                'mac': 'unknown',  # MAC address discovery requires ARP, which may be limited
                'hostname': f"host-{i}",
                'status': status
            })
            
        logger.info(f"Discovered {len(devices)} devices on network")
    except Exception as e:
        logger.error(f"Error scanning network: {e}")
    
    return devices

def collect_metrics():
    """Collect network metrics"""
    if SIMULATION_MODE:
        return collect_metrics_simulated()
    else:
        return collect_metrics_real()

def collect_metrics_simulated():
    """Collect simulated network metrics"""
    global network_status

    # Simulate collecting network metrics
    # Check connectivity to a common host
    try:
        ping_result = ping_host('8.8.8.8')
        latency = random.uniform(10, 100) if ping_result else 1000
    except Exception as e:
        logger.warning(f"Connectivity check failed, using simulated data: {e}")
        ping_result = random.random() > 0.2  # 80% chance of success in simulation
        latency = random.uniform(10, 100) if ping_result else 1000
    
    # Get devices on network
    devices = scan_local_network()
    connected_devices = len([d for d in devices if d['status'] == 'up'])
    
    # Simulate bandwidth usage
    bandwidth_usage = random.uniform(5, 50)  # Mbps
    
    # Calculate packet loss
    packet_loss = 0 if ping_result else random.uniform(10, 50)
    
    # Update network status
    network_status['last_updated'] = datetime.now().isoformat()
    network_status['latency'] = round(latency, 2)
    network_status['bandwidth_usage'] = round(bandwidth_usage, 2)
    network_status['packet_loss'] = round(packet_loss, 2)
    network_status['connected_devices'] = connected_devices
    
    # Determine overall health
    if latency > 500 or packet_loss > 20:
        network_status['overall_health'] = 'critical'
    elif latency > 200 or packet_loss > 5:
        network_status['overall_health'] = 'warning'
    else:
        network_status['overall_health'] = 'good'
    
    # Store metrics in history (keep last 100 entries)
    network_status['metrics_history'].append({
        'timestamp': network_status['last_updated'],
        'latency': network_status['latency'],
        'bandwidth_usage': network_status['bandwidth_usage'],
        'packet_loss': network_status['packet_loss']
    })
    
    if len(network_status['metrics_history']) > 100:
        network_status['metrics_history'] = network_status['metrics_history'][-100:]
    
    # Log status
    logger.info(f"Network status updated (SIMULATION): Health={network_status['overall_health']}, " +
                f"Latency={network_status['latency']}ms, " +
                f"Packet Loss={network_status['packet_loss']}%")
    
    # Emit network status update via Socket.IO
    try:
        from app import socketio
        socketio.emit('network_status_update', network_status, namespace='/')
        socketio.emit('devices_update', devices, namespace='/')
    except ImportError:
        # If unable to import socketio (like in testing), just continue
        pass
    
    # Check for anomalies
    try:
        from app.services import anomaly_detector as detector
        anomalies = detector.detect_anomalies(network_status)
        
        if anomalies:
            logger.warning(
                "Server-only simulation anomalies are diagnostic; no corrective issue created: %s",
                anomalies,
            )
                
    except Exception as e:
        logger.error(f"Error in anomaly detection: {e}")
    
    return network_status

def collect_metrics_real():
    """Collect real network metrics"""
    global network_status

    # Match the agent's measurement contract: ICMP to one shared target.
    latency, packet_loss = measure_ping_stats(NETWORK_TEST_HOST)
    
    # Measure bandwidth
    bandwidth_usage = measure_bandwidth()
    
    # Get devices on network
    devices = scan_local_network()
    connected_devices = len([d for d in devices if d['status'] == 'up'])
    
    # Update network status
    network_status['last_updated'] = datetime.now().isoformat()
    network_status['latency'] = round(latency, 2)
    network_status['bandwidth_usage'] = round(bandwidth_usage, 2)
    network_status['packet_loss'] = round(packet_loss, 2)
    network_status['connected_devices'] = connected_devices
    
    # Determine overall health
    if latency > 500 or packet_loss > 20:
        network_status['overall_health'] = 'critical'
    elif latency > 200 or packet_loss > 5:
        network_status['overall_health'] = 'warning'
    else:
        network_status['overall_health'] = 'good'
    
    # Store metrics in history (keep last 100 entries)
    network_status['metrics_history'].append({
        'timestamp': network_status['last_updated'],
        'latency': network_status['latency'],
        'bandwidth_usage': network_status['bandwidth_usage'],
        'packet_loss': network_status['packet_loss']
    })
    
    if len(network_status['metrics_history']) > 100:
        network_status['metrics_history'] = network_status['metrics_history'][-100:]
    
    # Log status
    logger.info(f"Network status updated (REAL): Health={network_status['overall_health']}, " +
                f"Latency={network_status['latency']}ms, " +
                f"Packet Loss={network_status['packet_loss']}%")
    
    # Emit network status update via Socket.IO
    try:
        from app import socketio
        socketio.emit('network_status_update', network_status, namespace='/')
        socketio.emit('devices_update', devices, namespace='/')
    except ImportError:
        # If unable to import socketio (like in testing), just continue
        pass
    
    # Check for anomalies
    # we are not gonna give model the server side observations
    return network_status

class NetworkMonitor:
    """Network monitoring class to run in a background thread"""
    
    def __init__(self, interval=10):
        """Initialize monitor with collection interval in seconds"""
        self.interval = interval
        self.running = False
        self.thread = None
    
    def start(self):
        """Start the monitoring process"""
        if self.thread and self.thread.is_alive():
            logger.warning("Monitor already running")
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info(f"Network monitoring started with {self.interval}s interval")
        logger.info(f"Mode: {'SIMULATION' if SIMULATION_MODE else 'REAL'}")
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                collect_metrics()
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
            
            time.sleep(self.interval)
    
    def stop(self):
        """Stop the monitoring process"""
        self.running = False
        logger.info("Network monitoring stopped")

# For testing
if __name__ == "__main__":
    monitor = NetworkMonitor(interval=5)
    monitor.start()
