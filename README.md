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

## Optional LLM Action Ranking

Deterministic action rules are used by default. To enable an OpenAI-compatible
LLM to rank only the actions already allowed by those rules, configure the
server environment:

```text
ACTION_LLM_ENABLED=true
OPENAI_API_KEY=your_api_key
OPENAI_MODEL=gpt-4o-mini
ACTION_LLM_MIN_CONFIDENCE=0.75
ACTION_LLM_TIMEOUT_SECONDS=3
# Optional OpenAI-compatible HTTPS endpoint:
# OPENAI_BASE_URL=https://provider.example/v1/chat/completions
```

The API key stays on the server. The selector sends only a small allowlisted
set of anomaly metrics, the operating system, and candidate action IDs. It does
not send host identity, IP addresses, free-text issue descriptions, or shell
commands. Remote provider URLs must use HTTPS; plain HTTP is permitted only for
loopback development. URLs containing user information, query strings, or
fragments are rejected. Responses are size-limited, and the model must return
one of the supplied action IDs above the configured confidence threshold.
Unknown actions, abstentions, low-confidence responses, invalid configuration,
timeouts, and provider errors fall back to deterministic rules. The LLM only
ranks a closed action set: it cannot invent commands, parameters, mark issues
resolved, bypass metric verification, or determine training labels. The agent
remains limited to its local action allowlist and permission prompt.
This setting enables action ranking only. It does not enable an LLM anomaly
detector or model retraining.

## Deployment

For a Docker deployment and multi-agent setup, see [DEPLOYMENT.md](DEPLOYMENT.md).
Set the same strong `API_KEY` on the server and every agent. Do not deploy with
the default development key.

## Deployment Quickstart

 - **Build image:** `docker build -t network-healer .`
 - **Run with Docker:** `docker run -e API_KEY=your_key -p 5000:5000 network-healer`
 - **Run with docker-compose:** copy `.env.example` to `.env`, set `API_KEY`, then `docker-compose up --build -d`
 - **Platform (Heroku) / Procfile:** a `Procfile` is included; set `web: gunicorn -k eventlet -w 1 wsgi:app`.

Notes:
 - The Docker image installs `tshark` and related system deps for packet capture.
 - Use the `eventlet` worker for WebSocket support (already in `requirements.txt`).

## Project Structure

- `app/`: Main application code
  - `models/`: ML model implementations
  - `network/`: Network monitoring components
  - `healing/`: Self-healing mechanisms
  - `templates/`: Web interface templates
  - `static/`: CSS, JS, and other static files
  - `utils/`: Utility functions
- `data/`: Storage for persistent data and ML models
