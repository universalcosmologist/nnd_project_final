from app import create_app, socketio
import threading
from app.network.monitor import NetworkMonitor

if __name__ == "__main__":
    app = create_app()
    
    # Start network monitoring in a separate thread
    monitor = NetworkMonitor(interval=5)  # Decreased from default 10 to 5 seconds
    monitor_thread = threading.Thread(target=monitor.start)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    print("Network monitoring started in background")
    print("Starting web server with Socket.IO. Access the dashboard at http://localhost:5000")
    
    # Start Flask application with Socket.IO
    socketio.run(app, debug=True, host='0.0.0.0', allow_unsafe_werkzeug=True) 