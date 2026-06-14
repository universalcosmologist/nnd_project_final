# Web-Based Self-Healing Network System

A Python-based network monitoring system with self-healing capabilities and ML-driven prediction.

## Overview

This project implements a real-time network monitoring system that can:

- Monitor network devices and traffic
- Detect anomalies and predict potential failures
- Automatically heal common network issues
- Provide a web interface for visualization and control

## Setup

1. Install requirements:

   ```
   pip install -r requirements.txt
   ```

2. Run the application:

   ```
   python run.py
   ```

3. Access the web interface at http://localhost:5000

## Features

- Real-time network monitoring
- ML-based anomaly detection
- Automated issue resolution
- Web dashboard for network status visualization
- Incremental model learning from detected issues

## Project Structure

- `app/`: Main application code
  - `models/`: ML model implementations
  - `network/`: Network monitoring components
  - `healing/`: Self-healing mechanisms
  - `templates/`: Web interface templates
  - `static/`: CSS, JS, and other static files
  - `utils/`: Utility functions
- `data/`: Storage for persistent data and ML models
