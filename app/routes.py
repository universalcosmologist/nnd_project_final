from flask import Blueprint, render_template, jsonify, request, current_app
from app.network.monitor import get_network_status, scan_local_network
from app.models.anomaly_detector import AnomalyDetector
from app.healing.resolver import NetworkResolver
import os
import json
import time
import uuid
from datetime import datetime
from app import socketio
from flask_socketio import emit
import logging

main_bp = Blueprint('main', __name__)

# Initialize components as global variables
anomaly_detector = AnomalyDetector()
network_resolver = NetworkResolver()

# Track connected clients
connected_clients = {}

# Helper functions to emit Socket.IO events
def emit_active_issues():
    """Emit active issues to all clients"""
    try:
        print("Emitting active issues update...")
        issues = network_resolver.get_active_issues()
        print(f"Active issues count: {len(issues)}")
        socketio.emit('active_issues_update', issues, namespace='/')
        print("Active issues emission completed")
    except Exception as e:
        print(f"Error emitting active issues: {e}")

def emit_resolved_issues():
    """Emit resolved issues to all clients"""
    try:
        print("Emitting resolved issues update...")
        # Force reload the resolution history from file to ensure we have the latest data
        network_resolver._load_resolution_history()
        
        history = []
        if os.path.exists(os.path.join('data', 'healing', 'resolution_history.json')):
            try:
                with open(os.path.join('data', 'healing', 'resolution_history.json'), 'r') as f:
                    history = json.load(f)
            except Exception as e:
                print(f"Error loading resolution history: {e}")
        
        print(f"Resolved issues count: {len(history)}")
        socketio.emit('resolved_issues_update', history, namespace='/')
        print("Resolved issues emission completed")
    except Exception as e:
        print(f"Error emitting resolved issues: {e}")

def emit_logs():
    """Emit system logs to all clients"""
    logs = get_latest_logs_data()
    socketio.emit('logs_update', logs, namespace='/')

def emit_network_status():
    """Emit network status to all clients"""
    status = get_network_status()
    socketio.emit('network_status_update', status, namespace='/')

# Function to ensure callbacks are registered - now defined after the functions it uses
def register_socketio_callbacks():
    """Register all Socket.IO callbacks with the NetworkResolver"""
    print("Registering Socket.IO callbacks with NetworkResolver...")
    # Clear any existing callbacks to avoid duplicates
    network_resolver.update_callbacks = []
    network_resolver.resolution_callbacks = []
    
    # Register callbacks
    network_resolver.register_update_callback(emit_active_issues)
    network_resolver.register_resolution_callback(emit_resolved_issues)
    print(f"Registered {len(network_resolver.update_callbacks)} update callbacks")
    print(f"Registered {len(network_resolver.resolution_callbacks)} resolution callbacks")

# Socket.IO event handlers - moved after the function definitions
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    client_id = request.sid
    connected_clients[client_id] = {
        'id': client_id,
        'connected_at': datetime.now().isoformat()
    }
    # Broadcast updated count to all clients
    emit('client_connected', {'count': len(connected_clients)}, broadcast=True)
    logging.info(f'Client connected. Total clients: {len(connected_clients)}')

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    client_id = request.sid
    if client_id in connected_clients:
        del connected_clients[client_id]
        # Broadcast updated count to all clients
        emit('client_disconnected', {'count': len(connected_clients)}, broadcast=True)
        logging.info(f'Client disconnected. Total clients: {len(connected_clients)}')

@socketio.on('get_client_count')
def handle_get_client_count():
    """Send current client count to requesting client"""
    emit('client_count', {'count': len(connected_clients)})

@socketio.on('client_heartbeat')
def handle_heartbeat(data):
    """Handle client heartbeat to update last active time"""
    if request.sid in connected_clients:
        connected_clients[request.sid]['last_active'] = datetime.now().isoformat()
        
        # Update client info if provided
        if 'client_info' in data:
            if 'name' in data['client_info']:
                connected_clients[request.sid]['name'] = data['client_info']['name']
            if 'location' in data['client_info']:
                connected_clients[request.sid]['location'] = data['client_info']['location']

@socketio.on('request_data_refresh')
def handle_data_refresh():
    """Handle client request for data refresh"""
    print("Client requested data refresh")
    emit_active_issues()
    emit_resolved_issues()
    emit_logs()
    emit_network_status()

@socketio.on('ping')
def handle_ping():
    """Handle ping from client to keep connection alive"""
    # Just acknowledge the ping, no need to send data
    pass

# Register callbacks after all functions are defined
register_socketio_callbacks()

@main_bp.route('/')
def index():
    """Main dashboard view"""
    return render_template('index.html')

@main_bp.route('/api/network/status')
def network_status():
    """API endpoint to get current network status"""
    status = get_network_status()
    return jsonify(status)

@main_bp.route('/api/network_status')
def network_status_alt():
    """Alternative API endpoint for network status (for frontend compatibility)"""
    status = get_network_status()
    return jsonify(status)

@main_bp.route('/api/network/history')
def network_history():
    """API endpoint to get network metrics history"""
    status = get_network_status()
    history = status.get('metrics_history', [])
    return jsonify(history)

@main_bp.route('/api/network/devices')
def network_devices():
    """API endpoint to get list of network devices"""
    # Simplified for now - would fetch from actual network
    devices = [
        {'id': 1, 'name': 'Router-1', 'ip': '192.168.1.1', 'status': 'online'},
        {'id': 2, 'name': 'Switch-1', 'ip': '192.168.1.2', 'status': 'online'},
        {'id': 3, 'name': 'AP-1', 'ip': '192.168.1.3', 'status': 'warning'}
    ]
    return jsonify(devices)

@main_bp.route('/api/healing/issues')
def get_issues():
    """Get current detected issues"""
    # Force reload issues from file to ensure we have the latest state
    network_resolver._load_active_issues()
    issues = network_resolver.get_active_issues()
    return jsonify(issues)

@main_bp.route('/api/healing/resolved')
def get_resolved_issues():
    """Get history of resolved issues"""
    # Force reload of resolution history to ensure we have the latest data
    network_resolver._load_resolution_history()
    
    # Return a copy of the list to avoid potential concurrency issues
    history = network_resolver.resolution_history.copy() if network_resolver.resolution_history else []
    
    # Add debugging info
    print(f"Returning {len(history)} resolved issues")
    
    return jsonify(history)

@main_bp.route('/api/healing/resolve', methods=['POST'])
def resolve_issue():
    """Manually trigger resolution for an issue"""
    try:
        data = request.json
        if not data or 'issue_id' not in data:
            print(f"Invalid request data: {data}")
            return jsonify({'success': False, 'message': 'Missing issue_id in request'}), 400
            
        issue_id = data.get('issue_id')
        print(f"Attempting to resolve issue: {issue_id}")
        
        result = network_resolver.resolve_issue(issue_id)
        print(f"Resolution result: {result}")
        
        # Force reload active issues after resolution attempt
        network_resolver._load_active_issues()
        
        # Emit updates
        emit_active_issues()
        emit_resolved_issues()
        emit_logs()
        
        return jsonify(result)
    except Exception as e:
        print(f"Error resolving issue: {str(e)}")
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500

@main_bp.route('/api/logs')
def get_latest_logs():
    """Get latest system logs"""
    logs = get_latest_logs_data()
    return jsonify(logs)

def get_latest_logs_data():
    """Helper function to get the latest logs data"""
    import logging
    import time
    from datetime import datetime
    
    # Check if log file exists and read from it
    log_file = os.path.join('logs', 'app.log')
    logs = []
    
    # If log file exists, get the latest entries
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r') as f:
                log_lines = f.readlines()
                # Get the last 20 lines
                log_lines = log_lines[-50:] if len(log_lines) > 50 else log_lines
                
                for line in log_lines:
                    try:
                        # Parse log line (format: YYYY-MM-DD HH:MM:SS,MS - name - level - message)
                        parts = line.strip().split(' - ', 3)
                        if len(parts) >= 4:
                            timestamp_str, source, level, message = parts
                            logs.append({
                                "timestamp": timestamp_str,
                                "level": level,
                                "source": source,
                                "message": message
                            })
                    except Exception as e:
                        print(f"Error parsing log line: {e}")
        except Exception as e:
            print(f"Error reading log file: {e}")
    
    # If no logs found in file or file doesn't exist, use simulated logs
    if not logs:
        current_time = datetime.now().isoformat()
        logs = [
            {"timestamp": current_time, "level": "INFO", "source": "monitor", "message": "Network monitoring started"},
            {"timestamp": current_time, "level": "INFO", "source": "monitor", "message": "Network status updated: Health=good, Latency=45ms, Packet Loss=0.5%"},
            {"timestamp": current_time, "level": "WARNING", "source": "detector", "message": "Detected anomaly: high_latency (score: -0.75)"},
            {"timestamp": current_time, "level": "INFO", "source": "resolver", "message": "Created new issue for anomaly type high_latency"},
            {"timestamp": current_time, "level": "INFO", "source": "resolver", "message": "Executing command: Simulating: Flushing DNS cache..."},
            {"timestamp": current_time, "level": "INFO", "source": "resolver", "message": "Issue marked as resolved"}
        ]
    
    return logs