# Deployment

## Docker (recommended)

1. Install Docker Engine or Docker Desktop on the server.
2. Copy `.env.example` to `.env` and set a long, random `API_KEY`.
3. From the project directory, run `docker compose up -d --build`.
4. Allow TCP port 5000 through the server firewall, or put a TLS reverse proxy
   (for example Nginx or Caddy) in front of it.

The `data/` and `logs/` directories are mounted as volumes, so model and issue
state survives a container replacement. Use one Gunicorn worker: active agents
and Socket.IO command state are in process memory. For horizontal scaling, move
agent/issue state to a database and configure Socket.IO with a shared message
queue before raising the worker count.

## Agent setup

On each client set `SERVER_URL` to the externally reachable HTTPS URL and set
the same `API_KEY` used by the server. The agent always requires local user
consent for every command. Start each agent with:

```powershell
$env:SERVER_URL = "https://network-healer.example.com"
$env:API_KEY = "your-shared-secret"
python agent.py
```

Each agent has a persistent ID, so the server routes a detected issue back to
the client that supplied the anomalous reading.
