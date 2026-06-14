import os
import logging
import json
import uuid
import subprocess
import platform
import time
from datetime import datetime
import threading
import random

# Set up logging to both console and file
if not os.path.exists('logs'):
    os.makedirs('logs')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('network_resolver')

# Add a special handler for Socket.IO
class SocketIOLogHandler(logging.Handler):
    """Custom log handler that emits log entries via Socket.IO"""
    def emit(self, record):
        try:
            # Lazy import to avoid circular import
            from app import socketio
            
            # Format the log message
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "level": record.levelname,
                "source": record.name,
                "message": record.getMessage()
            }
            
            # Emit via Socket.IO (without broadcast parameter)
            socketio.emit('log_entry', log_entry, namespace='/')
        except Exception:
            # If there's an error, we don't want to interrupt the logging process
            pass

# Add Socket.IO handler to root logger
try:
    root_logger = logging.getLogger()
    socketio_handler = SocketIOLogHandler()
    socketio_handler.setLevel(logging.INFO)
    root_logger.addHandler(socketio_handler)
except Exception as e:
    logger.error(f"Error setting up SocketIO log handler: {e}")

# Define file paths
DATA_DIR = os.path.join('data', 'healing')
ISSUES_FILE = os.path.join(DATA_DIR, 'active_issues.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'resolution_history.json')

# Make sure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

class NetworkResolver:
    """Network issue resolution and self-healing"""
    
    def __init__(self):
        """Initialize the network resolver"""
        # Load active issues
        self.active_issues = {}
        self._load_active_issues()
        
        # Load resolution history
        self.resolution_history = []
        self._load_resolution_history()
        
        # Initialize callback functions
        self.update_callbacks = []
        self.resolution_callbacks = []
        
        # Start the pending issues checker thread
        self.pending_checker_thread = threading.Thread(target=self._check_pending_issues, daemon=True)
        self.pending_checker_thread.start()
    
    def register_update_callback(self, callback):
        """Register a callback function to be called when active issues change"""
        if callback not in self.update_callbacks:
            self.update_callbacks.append(callback)
    
    def register_resolution_callback(self, callback):
        """Register a callback function to be called when issues are resolved"""
        if callback not in self.resolution_callbacks:
            self.resolution_callbacks.append(callback)
    
    def _trigger_update_callbacks(self):
        """Trigger all registered update callbacks"""
        logger.info(f"Triggering {len(self.update_callbacks)} update callbacks...")
        for callback in self.update_callbacks:
            try:
                logger.info(f"Executing update callback: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
                callback(self.get_model_accuracy())
                logger.info(f"Successfully executed update callback")
            except Exception as e:
                logger.error(f"Error in update callback: {e}")
    
    def _trigger_resolution_callbacks(self):
        """Trigger all registered resolution callbacks"""
        logger.info(f"Triggering {len(self.resolution_callbacks)} resolution callbacks...")
        for callback in self.resolution_callbacks:
            try:
                logger.info(f"Executing resolution callback: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
                callback()
                logger.info(f"Successfully executed resolution callback")
            except Exception as e:
                logger.error(f"Error in resolution callback: {e}")
    
    def _load_active_issues(self):
        """Force reload active issues from file"""
        if os.path.exists(ISSUES_FILE):
            try:
                with open(ISSUES_FILE, 'r') as f:
                    self.active_issues = json.load(f)
            except Exception as e:
                logger.error(f"Error loading active issues: {e}")
    
    def _save_active_issues(self):
        """Save active issues to file"""
        with open(ISSUES_FILE, 'w') as f:
            json.dump(self.active_issues, f, indent=2)
        # Trigger update callbacks
        self._trigger_update_callbacks()
    
    def _save_resolution_history(self):
        """Save resolution history to file"""
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            
            # Only save non-empty history
            if self.resolution_history:
                with open(HISTORY_FILE, 'w') as f:
                    json.dump(self.resolution_history, f, indent=2)
                logger.info(f"Saved {len(self.resolution_history)} items to resolution history")
                # Always trigger resolution callbacks after saving
                self._trigger_resolution_callbacks()
            else:
                logger.warning("No resolution history to save")
        except Exception as e:
            logger.error(f"Error saving resolution history: {e}")
    
    def _load_resolution_history(self):
        """Force reload resolution history from file"""
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    loaded_history = json.load(f)
                    if isinstance(loaded_history, list):
                        self.resolution_history = loaded_history
                        logger.info(f"Loaded {len(self.resolution_history)} items from resolution history")
                    else:
                        logger.error(f"Invalid resolution history format: expected list, got {type(loaded_history)}")
            except Exception as e:
                logger.error(f"Error loading resolution history: {e}")
                # Create backup of corrupted file
                if os.path.getsize(HISTORY_FILE) > 0:
                    backup_file = f"{HISTORY_FILE}.bak.{int(time.time())}"
                    try:
                        import shutil
                        shutil.copy2(HISTORY_FILE, backup_file)
                        logger.info(f"Created backup of corrupted history file: {backup_file}")
                    except Exception as backup_error:
                        logger.error(f"Failed to create backup: {backup_error}")
        else:
            logger.info("No resolution history file found, starting with empty history")
            self.resolution_history = []
    
    def handle_anomaly(self, anomaly):
        """Handle detected anomaly by creating an issue and attempting resolution"""
        # Generate a unique ID for this issue
        issue_id = str(uuid.uuid4())
        
        # Create issue from anomaly
        issue = {
            'id': issue_id,
            'title': self._get_title_for_anomaly_type(anomaly['type']),
            'description': self._get_description_for_anomaly(anomaly),
            'status': 'new',
            'anomaly': anomaly,
            'detected_at': anomaly['detected_at'],
            'resolution_attempts': 0,
            'resolution_actions': []
        }
        
        # Add to active issues
        self.active_issues[issue_id] = issue
        self._save_active_issues()
        
        logger.info(f"Created new issue {issue_id} for anomaly type {anomaly['type']}")
        
        # Try to auto-resolve
        threading.Thread(target=self.auto_resolve, args=(issue_id,)).start()
        
        return issue_id
    
    def _get_title_for_anomaly_type(self, anomaly_type):
        """Get user-friendly title for anomaly type"""
        titles = {
            'high_latency': 'High Network Latency Detected',
            'packet_loss': 'Packet Loss Issues Detected',
            'general_anomaly': 'Network Anomaly Detected',
            'dns_resolution_failure': 'DNS Resolution Failure',
            'routing_loop': 'Network Routing Loop Detected',
            'bandwidth_saturation': 'Bandwidth Saturation Warning',
            'connection_timeout': 'Network Connection Timeout',
            'security_breach': 'Potential Security Breach Detected',
            'device_failure': 'Network Device Failure'
        }
        return titles.get(anomaly_type, 'Network Issue Detected')
    
    def _get_description_for_anomaly(self, anomaly):
        """Generate user-friendly description for anomaly"""
        if anomaly['type'] == 'high_latency':
            latency = anomaly['details']['latency']
            threshold = anomaly['details']['threshold']
            return f"Network latency is high ({latency}ms), exceeding the threshold of {threshold}ms."
        
        elif anomaly['type'] == 'packet_loss':
            packet_loss = anomaly['details']['packet_loss']
            return f"Packet loss of {packet_loss}% detected on the network."
        
        elif anomaly['type'] == 'dns_resolution_failure':
            domain = anomaly['details'].get('domain', 'multiple domains')
            return f"Failed to resolve {domain}. DNS resolution errors can cause connectivity issues."
        
        elif anomaly['type'] == 'routing_loop':
            hops = anomaly['details'].get('hops', 'multiple hops')
            return f"Detected a routing loop across {hops} nodes. This can cause packet loss and latency."
        
        elif anomaly['type'] == 'bandwidth_saturation':
            usage = anomaly['details'].get('usage', '?')
            threshold = anomaly['details'].get('threshold', '?')
            return f"Network bandwidth usage ({usage}%) exceeds normal threshold ({threshold}%). This may cause slowdowns."
        
        elif anomaly['type'] == 'connection_timeout':
            service = anomaly['details'].get('service', 'network service')
            return f"Connection to {service} has timed out. Service may be unavailable."
        
        elif anomaly['type'] == 'security_breach':
            source = anomaly['details'].get('source', 'unknown source')
            return f"Detected potentially suspicious traffic from {source}. Security measures activated."
        
        elif anomaly['type'] == 'device_failure':
            device = anomaly['details'].get('device', 'network device')
            return f"Network device {device} has failed or is not responding."
        
        elif anomaly['type'] == 'general_anomaly':
            feature_str = ', '.join([f"{k}: {v:.2f}" for k, v in anomaly['details']['features'].items()])
            return f"Unusual network behavior detected. Metrics: {feature_str}"
        
        return "Unknown network issue detected."
    
    def get_active_issues(self):
        """Return list of active issues"""
        return list(self.active_issues.values())
    
    def auto_resolve(self, issue_id):
        """Attempt to automatically resolve an issue"""
        if issue_id not in self.active_issues:
            logger.error(f"Cannot resolve issue {issue_id} - not found")
            return {'success': False, 'message': 'Issue not found'}
        
        issue = self.active_issues[issue_id]
        logger.info(f"Starting resolution for issue {issue_id} with status {issue['status']}")
        
        if issue['status'] in ['resolved', 'resolving']:
            logger.info(f"Issue {issue_id} is already {issue['status']}")
            return {'success': True, 'message': f"Issue is already {issue['status']}"}
        
        # For demo - randomly decide if we should defer auto-resolution to keep issues visible
        # This gives user time to see issues on the dashboard
        if issue['status'] == 'new' and random.random() < 0.7 and not issue.get('manual_resolution'):  # 70% chance to defer immediate resolution
            logger.info(f"Deferring immediate resolution of issue {issue_id} for demonstration")
            issue['status'] = 'pending'
            self._save_active_issues()  # This will trigger update callbacks
            return {'success': True, 'message': "Resolution deferred for demonstration"}
        
        logger.info(f"Attempting to resolve issue {issue_id}")
        
        # Update issue status
        issue['status'] = 'resolving'
        issue['resolution_attempts'] += 1
        self._save_active_issues()  # This will trigger update callbacks
        
        # Get resolution strategy based on anomaly type
        resolution_strategy = self._get_resolution_strategy(issue)
        
        # Record the resolution action
        action = {
            'timestamp': datetime.now().isoformat(),
            'strategy': resolution_strategy['name'],
            'commands': resolution_strategy['commands'].copy() if 'commands' in resolution_strategy else []
        }
        issue['resolution_actions'].append(action)
        self._save_active_issues()  # This will trigger update callbacks to show real-time command execution
        
        # Execute resolution commands
        success = True
        result_messages = []
        
        if 'commands' in resolution_strategy:
            for cmd in resolution_strategy['commands']:
                try:
                    logger.info(f"Executing command: {cmd}")
                    if not os.environ.get('SIMULATION_MODE'):
                        # In real mode, execute the actual command
                        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                        exit_code = result.returncode
                        output = result.stdout
                    else:
                        # In simulation mode, just pretend to execute
                        exit_code = 0
                        output = "Command simulated successfully"
                    
                    result_messages.append(f"Command '{cmd}' completed with exit code {exit_code}")
                    
                    if exit_code != 0:
                        success = False
                        result_messages.append(f"Command failed: {output}")
                except Exception as e:
                    # For demo purposes, don't mark as failed if we have simulation commands that are working
                    if 'Simulating:' in cmd:
                        logger.info(f"Simulated command '{cmd}' assumed successful despite error: {str(e)}")
                    else:
                        success = False
                        error_msg = f"Error executing '{cmd}': {str(e)}"
                        result_messages.append(error_msg)
                        logger.error(error_msg)
        
        # Update issue with results
        action['result'] = {
            'success': success,
            'messages': result_messages
        }
        
        # Wait for verification period
        time.sleep(resolution_strategy.get('verification_wait', 1))
        
        # For simulation/demo, adjust success rates
        simulation_success_boost = 0.3  # Boost success rate by 30% for demo
        
        # Check if resolution was successful
        if success:
            # In a real implementation, we would verify the issue is actually resolved
            # For this demo, we'll assume success for some strategies
            strategy_success_rate = resolution_strategy.get('success_rate', 0.5) + simulation_success_boost
            
            # For manual resolutions, increase success rate significantly
            if issue.get('manual_resolution'):
                strategy_success_rate = min(1.0, strategy_success_rate + 0.3)  # Add 30% but cap at 100%
                logger.info(f"Manual resolution in progress - using boosted success rate: {strategy_success_rate}")
            
            # For demonstration - occasionally leave issues pending for user interaction
            if random.random() < 0.6 and not issue.get('manual_resolution'):  # 60% chance to mark as pending for automatic resolutions
                issue['status'] = 'pending'
                logger.info(f"Issue {issue_id} marked as pending for user demonstration - needs manual resolution")
                self._save_active_issues()  # Trigger update callbacks
            elif strategy_success_rate > random.random():
                issue['status'] = 'resolved'
                logger.info(f"Issue {issue_id} marked as resolved")
                # Remove resolved issue immediately
                self.active_issues.pop(issue_id)
                self._save_active_issues()  # Trigger update callbacks
                self._trigger_update_callbacks()  # Trigger update callbacks
                logger.info(f"Issue {issue_id} removed from active issues")
            else:
                issue['status'] = 'pending'
                logger.info(f"Issue {issue_id} marked as pending - needs verification")
                self._save_active_issues()  # Trigger update callbacks
        else:
            # For demo, still give a chance of success even if commands failed
            if random.random() < 0.2:  # Reduced from 0.3 to keep more issues visible
                issue['status'] = 'resolved'
                logger.info(f"Issue {issue_id} eventually resolved despite command failures")
                # Remove resolved issue immediately
                self.active_issues.pop(issue_id)
                self._save_active_issues()  # Trigger update callbacks
                self._trigger_update_callbacks()  # Trigger update callbacks
                logger.info(f"Issue {issue_id} removed from active issues")
            else:
                issue['status'] = 'failed'
                logger.warning(f"Issue {issue_id} resolution failed")
                self._save_active_issues()  # Trigger update callbacks
        
        # Save changes
        self._save_active_issues()
        
        # Create a copy of the issue for history before any modifications
        # This ensures we don't lose any data if the issue is removed from active_issues
        issue_copy = json.loads(json.dumps(issue))
        
        # Add to resolution history
        history_entry = {
            'issue_id': issue_id,
            'anomaly_type': issue['anomaly']['type'],
            'detected_at': issue['detected_at'],
            'resolved_at': datetime.now().isoformat() if issue['status'] == 'resolved' else None,
            'resolution_success': issue['status'] == 'resolved',
            'resolution_actions': issue['resolution_actions']
        }
        
        # Log details about the history entry
        logger.info(f"Adding issue to history: ID={history_entry['issue_id']}, Type={history_entry['anomaly_type']}, Success={history_entry['resolution_success']}")
        
        # Make sure we have the latest history before appending
        self._load_resolution_history()
        
        # Add to resolution history
        self.resolution_history.append(history_entry)
        
        # Save immediately to ensure it's persisted
        self._save_resolution_history()
        
        # Explicitly trigger resolution callbacks to ensure real-time updates
        self._trigger_resolution_callbacks()
        
        # Ensure active issues update is also triggered
        self._trigger_update_callbacks()
        
        return {'success': success, 'message': '\n'.join(result_messages)}
    
    def resolve_issue(self, issue_id):
        """Manually resolve an issue (triggered from UI)"""
        if issue_id not in self.active_issues:
            logger.error(f"Manual resolution failed: Issue {issue_id} not found")
            return {'success': False, 'message': 'Issue not found'}
            
        logger.info(f"Manual resolution triggered for issue {issue_id}")
        
        # Set a higher success rate for manual resolutions
        # This will bypass the random checks that might defer resolution
        issue = self.active_issues[issue_id]
        issue['manual_resolution'] = True
        
        # Call the auto_resolve but with a bypass for random checks
        result = self.auto_resolve(issue_id)
        logger.info(f"Manual resolution result: {result}")
        return result
    
    def _get_resolution_strategy(self, issue):
        """Determine the appropriate resolution strategy based on the issue type"""
        anomaly_type = issue['anomaly']['type']
        
        # Use SIMULATION_MODE to determine if we should use real commands or simulated ones
        simulation_mode = True  # Always use simulation for demo
        
        if anomaly_type == 'high_latency':
            if simulation_mode:
                return {
                    'name': 'Flush DNS and Reset Network (Simulation)',
                    'description': 'Simulated network configuration reset to resolve latency issues',
                    'commands': [
                        'echo "Simulating: Flushing DNS cache..."',
                        'echo "Simulating: Releasing network configuration..."',
                        'echo "Simulating: Renewing network configuration..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.8
                }
            else:
                return {
                    'name': 'Flush DNS and Reset Network',
                    'description': 'Flush DNS cache and reset network configuration to resolve latency issues',
                    'commands': [
                        'ipconfig /flushdns' if platform.system() == 'Windows' else 'sudo systemd-resolve --flush-caches',
                        'ipconfig /release' if platform.system() == 'Windows' else 'sudo dhclient -r',
                        'ipconfig /renew' if platform.system() == 'Windows' else 'sudo dhclient'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.8
                }
        
        elif anomaly_type == 'packet_loss':
            if simulation_mode:
                return {
                    'name': 'Reset Network Adapter (Simulation)',
                    'description': 'Simulated network adapter reset to resolve packet loss issues',
                    'commands': [
                        'echo "Simulating: Disabling network adapter..."',
                        'echo "Simulating: Waiting for adapter to fully disable..."',
                        'echo "Simulating: Enabling network adapter..."',
                        'echo "Simulating: Adapter successfully reset"'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.9
                }
            else:
                return {
                    'name': 'Reset Network Adapter',
                    'description': 'Disable and re-enable network adapter to resolve packet loss issues',
                    'commands': [
                        'echo "Disabling network adapter..."',
                        'sleep 2',
                        'echo "Enabling network adapter..."'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.7
                }
        
        elif anomaly_type == 'dns_resolution_failure':
            if simulation_mode:
                return {
                    'name': 'DNS Configuration Repair (Simulation)',
                    'description': 'Simulated DNS repair process to fix resolution issues',
                    'commands': [
                        'echo "Simulating: Flushing DNS cache..."',
                        'echo "Simulating: Checking DNS server configuration..."',
                        'echo "Simulating: Setting alternative DNS servers..."',
                        'echo "Simulating: Testing DNS resolution..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.85
                }
            else:
                return {
                    'name': 'DNS Configuration Repair',
                    'description': 'Fix DNS resolution by updating DNS settings and testing connectivity',
                    'commands': [
                        'ipconfig /flushdns' if platform.system() == 'Windows' else 'sudo systemd-resolve --flush-caches',
                        'echo "Setting Google DNS as alternative..."',
                        'nslookup google.com 8.8.8.8'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.75
                }
                
        elif anomaly_type == 'routing_loop':
            if simulation_mode:
                return {
                    'name': 'Routing Table Repair (Simulation)',
                    'description': 'Simulated repair of routing tables to fix network loops',
                    'commands': [
                        'echo "Simulating: Analyzing current routing table..."',
                        'echo "Simulating: Identifying routing loop..."',
                        'echo "Simulating: Clearing problematic routes..."',
                        'echo "Simulating: Reinstating correct routing paths..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.7
                }
            else:
                return {
                    'name': 'Routing Table Repair',
                    'description': 'Fix routing loops by resetting network routes',
                    'commands': [
                        'netstat -r' if platform.system() == 'Windows' else 'ip route show',
                        'route -f' if platform.system() == 'Windows' else 'sudo ip route flush table main',
                        'ipconfig /release && ipconfig /renew' if platform.system() == 'Windows' else 'sudo dhclient -r && sudo dhclient'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.65
                }
                
        elif anomaly_type == 'bandwidth_saturation':
            if simulation_mode:
                return {
                    'name': 'Traffic Optimization (Simulation)',
                    'description': 'Simulated QoS management to optimize bandwidth usage',
                    'commands': [
                        'echo "Simulating: Identifying bandwidth-intensive applications..."',
                        'echo "Simulating: Applying QoS traffic shaping..."',
                        'echo "Simulating: Prioritizing critical network traffic..."',
                        'echo "Simulating: Monitoring bandwidth utilization..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.75
                }
            else:
                return {
                    'name': 'Traffic Optimization',
                    'description': 'Manage bandwidth usage through traffic prioritization',
                    'commands': [
                        'netstat -b' if platform.system() == 'Windows' else 'netstat -p',
                        'echo "Applying traffic shaping policies..."',
                        'echo "Throttling non-essential traffic..."'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.65
                }
                
        elif anomaly_type == 'connection_timeout':
            if simulation_mode:
                return {
                    'name': 'Service Connection Repair (Simulation)',
                    'description': 'Simulated service connection troubleshooting',
                    'commands': [
                        'echo "Simulating: Testing connection to service..."',
                        'echo "Simulating: Checking firewall rules..."',
                        'echo "Simulating: Verifying service availability..."',
                        'echo "Simulating: Reestablishing connection..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.8
                }
            else:
                return {
                    'name': 'Service Connection Repair',
                    'description': 'Diagnose and fix connection timeout issues',
                    'commands': [
                        'ping -n 4 1.1.1.1' if platform.system() == 'Windows' else 'ping -c 4 1.1.1.1',
                        'tracert 1.1.1.1' if platform.system() == 'Windows' else 'traceroute 1.1.1.1',
                        'echo "Clearing connection cache..."'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.7
                }
                
        elif anomaly_type == 'security_breach':
            if simulation_mode:
                return {
                    'name': 'Security Countermeasures (Simulation)',
                    'description': 'Simulated security response to potential threats',
                    'commands': [
                        'echo "Simulating: Identifying suspicious traffic source..."',
                        'echo "Simulating: Temporarily blocking suspicious IP addresses..."',
                        'echo "Simulating: Updating firewall rules..."',
                        'echo "Simulating: Scanning for malware or intrusions..."',
                        'echo "Simulating: Generating security incident report..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.85
                }
            else:
                return {
                    'name': 'Security Countermeasures',
                    'description': 'Respond to security threats with protective measures',
                    'commands': [
                        'netstat -an' if platform.system() == 'Windows' else 'netstat -tuln',
                        'echo "Analyzing connection patterns..."',
                        'echo "Updating firewall rules to block suspicious traffic..."'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.75
                }
                
        elif anomaly_type == 'device_failure':
            if simulation_mode:
                return {
                    'name': 'Network Device Recovery (Simulation)',
                    'description': 'Simulated recovery of failed network devices',
                    'commands': [
                        'echo "Simulating: Diagnosing device status..."',
                        'echo "Simulating: Attempting device reboot..."',
                        'echo "Simulating: Checking device connectivity..."',
                        'echo "Simulating: Restoring device configuration..."',
                        'echo "Simulating: Verifying device operation..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.7
                }
            else:
                return {
                    'name': 'Network Device Recovery',
                    'description': 'Recover failed network devices through restart and reconfiguration',
                    'commands': [
                        'ping -n 4 192.168.1.1' if platform.system() == 'Windows' else 'ping -c 4 192.168.1.1',
                        'echo "Attempting device restart via management interface..."',
                        'echo "Restoring default device configuration..."'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.6
                }
        
        else:  # general_anomaly or unknown
            if simulation_mode:
                return {
                    'name': 'Basic Network Troubleshooting (Simulation)',
                    'description': 'Simulated basic network troubleshooting steps',
                    'commands': [
                        'echo "Simulating: Flushing DNS cache..."',
                        'echo "Simulating: Resetting Winsock catalog..."',
                        'echo "Simulating: Clearing ARP cache..."',
                        'echo "Simulating: Resetting TCP/IP stack..."'
                    ],
                    'verification_wait': 1,
                    'success_rate': 0.75
                }
            else:
                return {
                    'name': 'Basic Network Troubleshooting',
                    'description': 'Basic network troubleshooting steps for general issues',
                    'commands': [
                        'ipconfig /flushdns' if platform.system() == 'Windows' else 'sudo systemd-resolve --flush-caches',
                        'netsh winsock reset' if platform.system() == 'Windows' else 'echo "Not applicable on this platform"',
                        'arp -d *' if platform.system() == 'Windows' else 'sudo ip neigh flush all',
                        'netsh int ip reset' if platform.system() == 'Windows' else 'sudo systemctl restart NetworkManager'
                    ],
                    'verification_wait': 2,
                    'success_rate': 0.5
                }
    
    def get_model_accuracy(self):
        """Get the current model accuracy"""
        # Import here to avoid circular imports
        from app.models.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        return detector.get_model_accuracy()
    
    def _check_pending_issues(self):
        """Background thread to check and retry pending issues"""
        while True:
            try:
                current_time = datetime.now()
                for issue_id, issue in list(self.active_issues.items()):
                    if issue['status'] == 'pending':
                        # Check if issue has been pending for more than 30 seconds
                        detected_time = datetime.fromisoformat(issue['detected_at'])
                        if (current_time - detected_time).total_seconds() > 30:  # Changed from 60 (1 minute) to 30 seconds
                            logger.info(f"Retrying resolution for pending issue {issue_id} after 30 seconds")
                            result = self.auto_resolve(issue_id)
                            logger.info(f"Resolution result: {result}")
                
                # Log count of active and resolved issues
                logger.info(f"Active issues: {len(self.active_issues)}, Resolved issues: {len(self.resolution_history)}")
                
                # Emit status updates
                self._trigger_update_callbacks()
                self._trigger_resolution_callbacks()
                
            except Exception as e:
                logger.error(f"Error checking pending issues: {e}")
            
            # Check every 30 seconds instead of every minute
            time.sleep(30)
    
    def get_network_status(self):
        """Get the current network status"""
        from app.models.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        
        status = {
            'latency': self._get_latency(),
            'bandwidth_usage': self._get_bandwidth_usage(),
            'packet_loss': self._get_packet_loss(),
            'connected_devices': self._get_connected_devices(),
            'simulation_mode': self.simulation_mode,
            'model_accuracy': detector.get_model_accuracy()
        }
        return status

# For testing
if __name__ == "__main__":
    resolver = NetworkResolver()
    
    # Test with simulated anomaly
    anomaly = {
        'type': 'high_latency',
        'score': -0.5,
        'details': {
            'latency': 250,
            'threshold': 200
        },
        'detected_at': datetime.now().isoformat()
    }
    
    issue_id = resolver.handle_anomaly(anomaly)
    print(f"Created issue {issue_id}")
    
    # Get active issues
    issues = resolver.get_active_issues()
    print(f"Active issues: {len(issues)}")
    
    # Resolve an issue
    result = resolver.resolve_issue(issue_id)
    print(f"Resolution result: {result}") 