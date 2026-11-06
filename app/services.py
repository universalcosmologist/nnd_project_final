"""Application-wide stateful services.

The anomaly detector and resolver must each have exactly one instance per
server process. Keeping them here prevents agent handlers, HTTP routes and
the monitor from silently operating on different in-memory issue lists.
"""
from app.models.anomaly_detector import AnomalyDetector
from app.healing.resolver import NetworkResolver


anomaly_detector = AnomalyDetector()
network_resolver = NetworkResolver()
