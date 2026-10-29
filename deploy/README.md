# Deploy from GHCR

Images (linux/amd64 + arm64):

- `ghcr.io/deziss/llm-secure-gateway:<version|latest>`: the gateway
- `ghcr.io/deziss/llm-secure-gateway-gpu-agent:<version|latest>`: optional GPU host agent

## Gateway

```bash
mkdir llm-gateway && cd llm-gateway
curl -fsSLO https://raw.githubusercontent.com/deziss/llm-secure-gateway/main/deploy/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/deziss/llm-secure-gateway/main/deploy/.env.example -o .env
# edit .env: fill POSTGRES_PASSWORD, AUTH_SECRET, ENCRYPTION_KEY, DEFAULT_ADMIN_PASSWORD, OBS_METRICS_TOKEN
docker compose pull && docker compose up -d
```

The `migrate` service applies database migrations on every start, then the gateway starts on port 6130.
Open `http://<host>:6130/admin` to reach the admin UI.

To upgrade, run `docker compose pull && docker compose up -d`. To pin a release, set `GATEWAY_TAG=0.14.0` in `.env`.

## GPU agent (on each GPU host)

```bash
curl -fsSLO https://raw.githubusercontent.com/deziss/llm-secure-gateway/main/agent/docker-compose.yml
GPU_AGENT_TOKEN=<same as gateway> docker compose up -d
```

Then add the host on **Admin → Observability → GPUs**, using the URL `http://<gpu-host>:9835` and the source `agent`.
See [agent/README.md](../agent/README.md) for AMD and systemd installs.
