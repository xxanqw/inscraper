# InstaCaper

Instagram media downloader with VPN rotation. Built with FastAPI, `curl-cffi`, Playwright, and Gluetun.

## Features

- **Auto-Download** — Every scrape request downloads media (images/videos) through the VPN tunnel and stores them locally.
- **GraphQL Scraper** — Directly queries Instagram's internal API with JA3/TLS impersonation via `curl-cffi`.
- **Playwright Fallback** — DOM-based extraction when GraphQL is blocked or rate-limited.
- **VPN Rotation** — Automatically rotates NordVPN IPs via Gluetun on 403/429 responses.
- **On-Disk Storage** — Persistent filesystem storage with automatic 10GB size limit and LRU eviction.
- **Zero-Trust Networking** — Scraper container runs inside Gluetun's network namespace; all outbound traffic is tunneled.

## API

### `POST /scrape`

Scrape metadata and download media via the GraphQL scraper.

```bash
curl -X POST http://localhost:8080/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/reel/DX9no32y8K1/"}'
```

**Response:**

```json
{
  "shortcode": "DX9no32y8K1",
  "caption": "...",
  "author": "username",
  "media_type": "XDTGraphVideo",
  "thumbnail_url": "/media/DX9no32y8K1/thumbnail.jpg",
  "video_url": "/media/DX9no32y8K1/video.mp4",
  "carousel": null
}
```

### `POST /scrape/playwright`

Fallback scraper using Playwright.

```bash
curl -X POST http://localhost:8080/scrape/playwright \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/p/C6_abcdefg/"}'
```

### `GET /media/{shortcode}/{filename}`

Serve locally stored media files.

```bash
curl -o video.mp4 "http://localhost:8080/media/DX9no32y8K1/video.mp4"
```

### `GET /health`

Health check.

## Setup

Create `.env` from `.env.example` and fill in your NordVPN service credentials.

```bash
cp .env.example .env
```

## Launch

### Docker Compose (recommended)

Save as `docker-compose.yml`:

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    container_name: gluetun
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=openvpn
      - OPENVPN_USER=${NORDVPN_USER}
      - OPENVPN_PASSWORD=${NORDVPN_PASSWORD}
      - OPENVPN_PROTO=tcp
      - SERVER_COUNTRIES=Ukraine,Poland,Germany
      - FIREWALL=on
      - HTTP_CONTROL_SERVER_ADDRESS=:8000
      - HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE={"auth":"apikey","apikey":"${GLUETUN_API_KEY:-secret-key}"}
    ports:
      - "8000:8000/tcp"
      - "8080:8080/tcp"
    restart: unless-stopped
    stop_grace_period: 7s

  scraper:
    image: ghcr.io/xxanqw/inscraper:main
    container_name: scraper
    network_mode: "service:gluetun"
    volumes:
      - ./cache:/app/cache
    depends_on:
      gluetun:
        condition: service_started
    environment:
      - GLUETUN_CONTROL_URL=http://localhost:8000
      - GLUETUN_API_KEY=${GLUETUN_API_KEY:-secret-key}
    restart: always
```

Then run:

```bash
docker compose up -d
```

### Direct Docker

```bash
# Gluetun
docker run -d \
  --name gluetun \
  --cap-add NET_ADMIN \
  --device /dev/net/tun:/dev/net/tun \
  -e VPN_SERVICE_PROVIDER=nordvpn \
  -e VPN_TYPE=openvpn \
  -e OPENVPN_USER="$NORDVPN_USER" \
  -e OPENVPN_PASSWORD="$NORDVPN_PASSWORD" \
  -e OPENVPN_PROTO=tcp \
  -e SERVER_COUNTRIES=Ukraine,Poland,Germany \
  -e FIREWALL=on \
  -e HTTP_CONTROL_SERVER_ADDRESS=:8000 \
  -p 8000:8000/tcp \
  -p 8080:8080/tcp \
  --restart unless-stopped \
  qmcgaw/gluetun:latest

# Scraper (pull from GHCR)
docker run -d \
  --name scraper \
  --network container:gluetun \
  -v "$(pwd)/cache:/app/cache" \
  -e GLUETUN_CONTROL_URL=http://localhost:8000 \
  -e GLUETUN_API_KEY="${GLUETUN_API_KEY:-secret-key}" \
  --restart always \
  ghcr.io/xxanqw/inscraper:main
```

### Build locally

```bash
git clone <repo-url>
cd instcaper
docker build -t instcaper .
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NORDVPN_USER` | NordVPN service username | — |
| `NORDVPN_PASSWORD` | NordVPN service password | — |
| `GLUETUN_API_KEY` | API key for Gluetun control server | `secret-key` |
| `CACHE_PATH` | Cache directory path | `./cache` |
| `CACHE_MAX_SIZE_GB` | Max cache size before eviction | `10.0` |

## Testing

```bash
uv run pytest tests/test_scrapers.py -s
```

Tests may skip if your local IP is rate-limited. For full fidelity, run inside the Docker Compose stack.
