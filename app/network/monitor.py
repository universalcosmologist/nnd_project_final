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
# Default to simulation mode, can be overridden by environment variable
SIMULATION_MODE = os.environ.get('SIMULATION_MODE', 'true').lower() == 'true'
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

def measure_bandwidth():
    """Measure network bandwidth in container environment"""
    if SIMULATION_MODE:
        # Simulate bandwidth usage
        return random.uniform(5, 50)  # Mbps
    
    try:
        # Read network stats from /proc filesystem
        with open('/proc/net/dev', 'r') as f:
            lines = f.readlines()
        
        # Find the main interface (usually eth0 in containers)
        interface_data = None
        for line in lines[2:]:  # Skip header lines
            parts = line.strip().split()
            if len(parts) >= 17:
                interface = parts[0].strip(':')
                if interface == 'eth0' or interface == 'ens5':
                    interface_data = {
                        'bytes_recv': int(parts[1]),
                        'bytes_sent': int(parts[9])
                    }
                    break
        
        if not interface_data:
            return 0
            
        # Wait for a short period
        time.sleep(1)
        
        # Read updated stats
        with open('/proc/net/dev', 'r') as f:
            lines = f.readlines()
        
        # Find the interface again
        for line in lines[2:]:
            parts = line.strip().split()
            if len(parts) >= 17:
                interface = parts[0].strip(':')
                if interface == 'eth0' or interface == 'ens5':
                    # Calculate bytes per second
                    bytes_recv = int(parts[1]) - interface_data['bytes_recv']
                    bytes_sent = int(parts[9]) - interface_data['bytes_sent']
                    
                    # Convert to Mbps (megabits per second)
                    total_bytes = bytes_recv + bytes_sent
                    mbps = (total_bytes * 8) / 1000000
                    
                    return mbps
        
        return 0
    except Exception as e:
        logger.error(f"Error measuring bandwidth: {e}")
        return 0  # Default to zero on error

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
        from app.models.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        anomalies = detector.detect_anomalies(network_status)
        
        if anomalies:
            logger.warning(f"Anomalies detected: {anomalies}")
            # Trigger self-healing
            from app.healing.resolver import NetworkResolver
            resolver = NetworkResolver()
            for anomaly in anomalies:
                resolver.handle_anomaly(anomaly)
            
            # Explicitly emit active issues after handling anomalies
            try:
                # Use lazy import to avoid circular dependencies
                import importlib
                routes_module = importlib.import_module('app.routes')
                print("Explicitly emitting active issues after anomaly detection")
                routes_module.emit_active_issues()
                routes_module.emit_logs()
            except Exception as e:
                logger.error(f"Error explicitly emitting active issues: {e}")
                
    except Exception as e:
        logger.error(f"Error in anomaly detection: {e}")
    
    return network_status

def collect_metrics_real():
    """Collect real network metrics"""
    global network_status

    # Check connectivity to common hosts
    hosts_to_check = ['google.com', 'github.com', 'microsoft.com']
    
    # Find a host that responds
    responding_host = None
    for host in hosts_to_check:
        if ping_host(host):
            responding_host = host
            break
    
    if not responding_host:
        logger.warning("No hosts responding, using fallback values")
        latency = 1000
        packet_loss = 100
    else:
        # Measure latency and packet loss to the responding host
        latency = measure_latency(responding_host)
        packet_loss = measure_packet_loss(responding_host)
    
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
    try:
        from app.models.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        anomalies = detector.detect_anomalies(network_status)
        
        if anomalies:
            logger.warning(f"Anomalies detected: {anomalies}")
            # Trigger self-healing
            from app.healing.resolver import NetworkResolver
            resolver = NetworkResolver()
            for anomaly in anomalies:
                resolver.handle_anomaly(anomaly)
            
            # Explicitly emit active issues after handling anomalies
            try:
                # Use lazy import to avoid circular dependencies
                import importlib
                routes_module = importlib.import_module('app.routes')
                print("Explicitly emitting active issues after anomaly detection")
                routes_module.emit_active_issues()
                routes_module.emit_logs()
            except Exception as e:
                logger.error(f"Error explicitly emitting active issues: {e}")
                
    except Exception as e:
        logger.error(f"Error in anomaly detection: {e}")
    
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