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


if issue['status'] == 'new' and random.random() < 0.7 and not issue.get('manual_resolution'):  # 70% chance to defer immediate resolution
            logger.info(f"Deferring immediate resolution of issue {issue_id} for demonstration")
            issue['status'] = 'pending'
            self._save_active_issues()  # This will trigger update callbacks
            return {'success': True, 'message': "Resolution deferred for demonstration"}




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
                routes_module.emit_active_issues()
                routes_module.emit_logs()
            except Exception as e:
                logger.error(f"Error explicitly emitting active issues: {e}")
                
except Exception as e:
        logger.error(f"Error in anomaly detection: {e}")
    