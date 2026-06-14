from flask import Flask
import os
from flask_socketio import SocketIO

# Create the SocketIO instance outside the factory function
# Enable async_mode='eventlet' for better performance and configure CORS
socketio = SocketIO(cors_allowed_origins="*", ping_timeout=60, ping_interval=25, async_mode='threading')

def create_app():
    """Application factory function"""
    app = Flask(__name__)
    
    # Ensure data directory exists
    os.makedirs('data', exist_ok=True)
    
    # Register blueprints
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    # Initialize SocketIO with the app
    socketio.init_app(app)
    
    return app 