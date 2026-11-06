#!/usr/bin/env python3
"""
Test script to debug agent-server connection.
"""
import socketio
import time
import logging

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create client
sio = socketio.Client(logger=True, engineio_logger=True)

@sio.on('connect', namespace='/agent')
def on_connect():
    logger.info("✅ CONNECTED to server")
    logger.info(f"SID: {sio.sid}")
    
    # Emit registration
    reg_data = {
        'agent_id': 'test-agent-001',
        'api_key': 'your-secret-key',
        'hostname': 'TEST-HOST',
        'os': 'Windows',
        'os_version': '10.0',
        'ip': '192.168.1.100'
    }
    logger.info(f"Emitting agent_register: {reg_data}")
    sio.emit('agent_register', reg_data, namespace='/agent')

@sio.on('registration_ack', namespace='/agent')
def on_registration_ack(data):
    logger.info(f"✅ Received registration_ack: {data}")

@sio.on('disconnect', namespace='/agent')
def on_disconnect():
    logger.warning("❌ DISCONNECTED from server")

# Connect
try:
    logger.info("Attempting to connect to http://localhost:5000...")
    sio.connect('http://localhost:5000', namespaces=['/agent'])
    logger.info("Connected, waiting for events...")
    time.sleep(5)
    logger.info("Test complete")
except Exception as e:
    logger.error(f"Error: {e}", exc_info=True)
finally:
    if sio.connected:
        sio.disconnect()
