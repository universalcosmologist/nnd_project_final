"""Production WSGI entry point."""
from app import create_app, socketio

app = create_app()
