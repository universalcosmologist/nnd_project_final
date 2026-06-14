import socket
import platform
import subprocess
import logging
import json
import datetime

# Set up logging
logger = logging.getLogger('utils.helpers')

class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects"""
    def default(self, obj):
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return super().default(obj)

def is_valid_ip(ip):
    """Check if a string is a valid IP address"""
    try:
        socket.inet_aton(ip)
        return True
    except socket.error:
        return False

def get_hostname(ip):
    """Get hostname from IP address"""
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror):
        return None

def ping(host, count=1):
    """Ping a host and return True if reachable"""
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, str(count), host]
    try:
        subprocess.check_output(command, stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError:
        return False

def format_bytes(bytes, suffix="B"):
    """Format bytes to human-readable form"""
    for unit in ["", "K", "M", "G", "T", "P"]:
        if bytes < 1024:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= 1024
    return f"{bytes:.2f}E{suffix}"

def safe_execute(func, default=None, log_error=True):
    """Safely execute a function and handle exceptions"""
    try:
        return func()
    except Exception as e:
        if log_error:
            logger.error(f"Error executing function: {e}")
        return default

def save_json(data, filepath):
    """Save data to JSON file with datetime handling"""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, cls=DateTimeEncoder, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving JSON to {filepath}: {e}")
        return False

def load_json(filepath, default=None):
    """Load data from JSON file with error handling"""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {e}")
        return default if default is not None else {} 