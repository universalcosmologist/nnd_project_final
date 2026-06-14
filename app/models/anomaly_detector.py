import os
import logging
import numpy as np
import pickle
import json
from datetime import datetime
from collections import deque
from sklearn.ensemble import IsolationForest
import joblib
import random

# Set up logging to both console and file
if not os.path.exists('logs'):
    os.makedirs('logs')

# Configure logger
logger = logging.getLogger('anomaly_detector')
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

# Define the model file paths
MODEL_DIR = os.path.join('data', 'models')
MODEL_FILE = os.path.join(MODEL_DIR, 'anomaly_model.pkl')
HISTORY_FILE = os.path.join(MODEL_DIR, 'training_history.json')

# Make sure model directory exists
os.makedirs(MODEL_DIR, exist_ok=True)

class AnomalyDetector:
    """Anomaly detection for network metrics using Isolation Forest"""
    
    def __init__(self):
        """Initialize the anomaly detector and load or create model"""
        # Keep a buffer of recent observations for retraining
        self.observations = deque(maxlen=1000)
        
        # Feature names to extract from network status
        self.features = ['latency', 'bandwidth_usage', 'packet_loss']
        
        # Initialize model accuracy
        self.current_accuracy = 95.0  # Start at 95%
        self.last_accuracy_update = datetime.now()
        
        # Create or load the model
        if os.path.exists(MODEL_FILE):
            logger.info(f"Loading existing anomaly detection model from {MODEL_FILE}")
            self.model = joblib.load(MODEL_FILE)
        else:
            logger.info("Creating new anomaly detection model")
            self.model = IsolationForest(
                n_estimators=100,
                contamination=0.05,  # Expect about 5% anomalies
                random_state=42
            )
            # We'll train on first batch of data
        
        # Load training history if it exists
        self.training_history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    self.training_history = json.load(f)
            except Exception as e:
                logger.error(f"Error loading training history: {e}")
    
    def _extract_features(self, network_status):
        """Extract relevant features from network status"""
        features = []
        for feature in self.features:
            features.append(network_status.get(feature, 0))
        return features
    
    def detect_anomalies(self, network_status):
        """Detect anomalies in network status"""
        # Extract features from network status
        features = self._extract_features(network_status)
        
        # Save observation for future training
        self.observations.append(features)
        
        # Check if model needs to be trained first
        is_model_fitted = hasattr(self.model, "offset_") and self.model.offset_ is not None
        
        # If model isn't fitted yet OR if we have enough data for initial training
        if not is_model_fitted:
            # Try to do initial training if we have enough observations
            if len(self.observations) >= 50:
                logger.info("Performing initial model training...")
                self._train_model()
                is_model_fitted = True
            else:
                logger.info(f"Not enough observations yet for training: {len(self.observations)}/50")
            
            # For demonstration - simulate some anomalies even before model is trained
            if random.random() < 0.90:  # Increased from 0.50 to 0.90 (90% chance to simulate anomaly)
                anomaly_type = random.choice([
                    'high_latency', 'packet_loss', 'general_anomaly',
                    'dns_resolution_failure', 'routing_loop', 'bandwidth_saturation',
                    'connection_timeout', 'security_breach', 'device_failure'
                ])
                
                anomalies = []
                if anomaly_type == 'high_latency':
                    anomalies.append({
                        'type': 'high_latency',
                        'score': -0.9,
                        'details': {
                            'latency': network_status['latency'],
                            'threshold': 200
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'packet_loss':
                    anomalies.append({
                        'type': 'packet_loss',
                        'score': -0.9,
                        'details': {
                            'packet_loss': network_status['packet_loss'],
                            'threshold': 10
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'dns_resolution_failure':
                    anomalies.append({
                        'type': 'dns_resolution_failure',
                        'score': -0.9,
                        'details': {
                            'domain': random.choice(['example.com', 'google.com', 'cloudflare.com']),
                            'error': 'Cannot resolve hostname'
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'routing_loop':
                    anomalies.append({
                        'type': 'routing_loop',
                        'score': -0.9,
                        'details': {
                            'hops': random.randint(3, 8),
                            'loop_ips': ['192.168.1.1', '10.0.0.1', '172.16.0.1']
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'bandwidth_saturation':
                    usage = random.randint(85, 99)
                    anomalies.append({
                        'type': 'bandwidth_saturation',
                        'score': -0.9,
                        'details': {
                            'usage': usage,
                            'threshold': 80,
                            'bandwidth': f"{random.randint(90, 110)} Mbps"
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'connection_timeout':
                    anomalies.append({
                        'type': 'connection_timeout',
                        'score': -0.9,
                        'details': {
                            'service': random.choice(['Web Server', 'Database', 'API Gateway', 'Mail Server']),
                            'timeout': f"{random.randint(30, 120)} seconds"
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'security_breach':
                    anomalies.append({
                        'type': 'security_breach',
                        'score': -0.9,
                        'details': {
                            'source': f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                            'ports': [random.randint(1, 65535) for _ in range(3)],
                            'traffic_type': random.choice(['Port Scan', 'DoS Attempt', 'Brute Force', 'Data Exfiltration'])
                        },
                        'detected_at': self._current_time()
                    })
                elif anomaly_type == 'device_failure':
                    anomalies.append({
                        'type': 'device_failure',
                        'score': -0.9,
                        'details': {
                            'device': random.choice(['Router-1', 'Switch-2', 'Firewall', 'Access Point-3']),
                            'error': random.choice(['Hardware Failure', 'Configuration Corruption', 'Firmware Issue', 'Power Problem'])
                        },
                        'detected_at': self._current_time()
                    })
                else:
                    anomalies.append({
                        'type': 'general_anomaly',
                        'score': -0.9,
                        'details': {
                            'features': dict(zip(self.features, features))
                        },
                        'detected_at': self._current_time()
                    })
                return anomalies
            
            return []  # Return no anomalies until model is trained if no simulation
        
        # Only make predictions if model is fitted
        if is_model_fitted:
            # Make prediction (1 = normal, -1 = anomaly)
            prediction = self.model.predict([features])[0]
            anomaly_score = self.model.decision_function([features])[0]
            
            # No anomaly detected
            if prediction == 1:
                # For demonstration - occasionally override with simulated anomalies
                if random.random() < 0.70:  # Increased from 0.30 to 0.70 (70% chance to override)
                    prediction = -1
                    anomaly_score = -0.75
            
            if prediction == 1:
                return []
            
            # Determine the anomaly type based on feature values and/or simulate various types
            anomalies = []
            
            # Randomly select from all possible anomaly types for more diverse demonstration
            # This makes the demo more interesting with different types of issues
            anomaly_type = random.choice([
                'high_latency', 'packet_loss', 'general_anomaly',
                'dns_resolution_failure', 'routing_loop', 'bandwidth_saturation',
                'connection_timeout', 'security_breach', 'device_failure'
            ])
            
            if anomaly_type == 'high_latency' or (network_status['latency'] > 200 and not anomalies):
                anomalies.append({
                    'type': 'high_latency',
                    'score': float(anomaly_score),
                    'details': {
                        'latency': network_status['latency'],
                        'threshold': 200
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'packet_loss' or (network_status['packet_loss'] > 10 and not anomalies):
                anomalies.append({
                    'type': 'packet_loss',
                    'score': float(anomaly_score),
                    'details': {
                        'packet_loss': network_status['packet_loss'],
                        'threshold': 10
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'dns_resolution_failure':
                anomalies.append({
                    'type': 'dns_resolution_failure',
                    'score': float(anomaly_score),
                    'details': {
                        'domain': random.choice(['example.com', 'google.com', 'cloudflare.com']),
                        'error': 'Cannot resolve hostname'
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'routing_loop':
                anomalies.append({
                    'type': 'routing_loop',
                    'score': float(anomaly_score),
                    'details': {
                        'hops': random.randint(3, 8),
                        'loop_ips': ['192.168.1.1', '10.0.0.1', '172.16.0.1']
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'bandwidth_saturation':
                usage = random.randint(85, 99)
                anomalies.append({
                    'type': 'bandwidth_saturation',
                    'score': float(anomaly_score),
                    'details': {
                        'usage': usage,
                        'threshold': 80,
                        'bandwidth': f"{random.randint(90, 110)} Mbps"
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'connection_timeout':
                anomalies.append({
                    'type': 'connection_timeout',
                    'score': float(anomaly_score),
                    'details': {
                        'service': random.choice(['Web Server', 'Database', 'API Gateway', 'Mail Server']),
                        'timeout': f"{random.randint(30, 120)} seconds"
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'security_breach':
                anomalies.append({
                    'type': 'security_breach',
                    'score': float(anomaly_score),
                    'details': {
                        'source': f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                        'ports': [random.randint(1, 65535) for _ in range(3)],
                        'traffic_type': random.choice(['Port Scan', 'DoS Attempt', 'Brute Force', 'Data Exfiltration'])
                    },
                    'detected_at': self._current_time()
                })
            
            elif anomaly_type == 'device_failure':
                anomalies.append({
                    'type': 'device_failure',
                    'score': float(anomaly_score),
                    'details': {
                        'device': random.choice(['Router-1', 'Switch-2', 'Firewall', 'Access Point-3']),
                        'error': random.choice(['Hardware Failure', 'Configuration Corruption', 'Firmware Issue', 'Power Problem'])
                    },
                    'detected_at': self._current_time()
                })
            
            elif len(anomalies) == 0 and prediction == -1:
                # General anomaly without specific cause
                anomalies.append({
                    'type': 'general_anomaly',
                    'score': float(anomaly_score),
                    'details': {
                        'features': dict(zip(self.features, features))
                    },
                    'detected_at': self._current_time()
                })
            
        else:
            # If model isn't fitted, simulate some anomalies
            if random.random() < 0.30:  # 30% chance to generate anomaly
                anomaly_type = random.choice([
                    'high_latency', 'packet_loss', 'general_anomaly',
                    'dns_resolution_failure', 'routing_loop', 'bandwidth_saturation',
                    'connection_timeout', 'security_breach', 'device_failure'
                ])
                
                anomalies = []
                anomalies.append({
                    'type': anomaly_type,
                    'score': -0.8,
                    'details': self._generate_details_for_anomaly_type(anomaly_type, network_status),
                    'detected_at': self._current_time()
                })
            else:
                return []
        
        # Log the anomaly
        if anomalies:
            logger.warning(f"Detected {len(anomalies)} anomalies: {anomalies}")
        
        # Check if we should retrain model
        if len(self.observations) >= 500:  # Retrain after collecting enough new data
            self._train_model()
        
        return anomalies
    
    def _train_model(self):
        """Train or retrain the model with current observations"""
        logger.info(f"Training anomaly detection model with {len(self.observations)} observations")
        
        # Convert observations to numpy array
        X = np.array(list(self.observations))
        
        # Fit the model
        self.model.fit(X)
        
        # Save the model
        joblib.dump(self.model, MODEL_FILE)
        logger.info(f"Model saved to {MODEL_FILE}")
        
        # Record training event
        training_event = {
            'timestamp': datetime.now().isoformat(),
            'samples': len(self.observations),
            'features': self.features
        }
        self.training_history.append(training_event)
        
        # Save training history
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.training_history, f, indent=2)
        
        logger.info("Model training completed")
    
    def record_feedback(self, anomaly_id, is_real_anomaly):
        """Record user feedback on anomaly detection results for model improvement"""
        # This would be connected to UI feedback
        # We could use this to adjust model parameters or create labeled data
        logger.info(f"Received feedback for anomaly {anomaly_id}: is_real_anomaly={is_real_anomaly}")
        # In a more sophisticated implementation, this would be used to improve the model

    def _current_time(self):
        """Helper to get current time in ISO format"""
        return datetime.now().isoformat()

    def _generate_details_for_anomaly_type(self, anomaly_type, network_status):
        """Generate appropriate details for a given anomaly type"""
        if anomaly_type == 'high_latency':
            return {
                'latency': network_status['latency'],
                'threshold': 200
            }
        elif anomaly_type == 'packet_loss':
            return {
                'packet_loss': network_status['packet_loss'],
                'threshold': 10
            }
        elif anomaly_type == 'dns_resolution_failure':
            return {
                'domain': random.choice(['example.com', 'google.com', 'cloudflare.com']),
                'error': 'Cannot resolve hostname'
            }
        elif anomaly_type == 'routing_loop':
            return {
                'hops': random.randint(3, 8),
                'loop_ips': ['192.168.1.1', '10.0.0.1', '172.16.0.1']
            }
        elif anomaly_type == 'bandwidth_saturation':
            usage = random.randint(85, 99)
            return {
                'usage': usage,
                'threshold': 80,
                'bandwidth': f"{random.randint(90, 110)} Mbps"
            }
        elif anomaly_type == 'connection_timeout':
            return {
                'service': random.choice(['Web Server', 'Database', 'API Gateway', 'Mail Server']),
                'timeout': f"{random.randint(30, 120)} seconds"
            }
        elif anomaly_type == 'security_breach':
            return {
                'source': f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                'ports': [random.randint(1, 65535) for _ in range(3)],
                'traffic_type': random.choice(['Port Scan', 'DoS Attempt', 'Brute Force', 'Data Exfiltration'])
            }
        elif anomaly_type == 'device_failure':
            return {
                'device': random.choice(['Router-1', 'Switch-2', 'Firewall', 'Access Point-3']),
                'error': random.choice(['Hardware Failure', 'Configuration Corruption', 'Firmware Issue', 'Power Problem'])
            }
        else:  # general_anomaly
            return {
                'features': dict(zip(self.features, self._extract_features(network_status)))
            }

    def get_model_accuracy(self):
        """Get current model accuracy (simulated)"""
        current_time = datetime.now()
        
        # Update accuracy every 10 seconds
        if (current_time - self.last_accuracy_update).total_seconds() >= 10:
            # Generate a small random change (-0.3 to +0.3)
            change = (random.random() - 0.5) * 0.6
            
            # Update accuracy ensuring it stays between 93 and 97
            self.current_accuracy = max(93.0, min(97.0, self.current_accuracy + change))
            self.last_accuracy_update = current_time
        
        return round(self.current_accuracy, 2)

# For testing
if __name__ == "__main__":
    detector = AnomalyDetector()
    # Test with simulated data
    for _ in range(100):
        status = {
            'latency': np.random.normal(50, 20),
            'bandwidth_usage': np.random.normal(20, 5),
            'packet_loss': np.random.normal(1, 0.5)
        }
        anomalies = detector.detect_anomalies(status)
        if anomalies:
            print(f"Detected anomalies: {anomalies}") 