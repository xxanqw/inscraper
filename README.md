# 📥 InScraper

**InScraper** is a high-performance Instagram media downloader with automated **VPN rotation**. It provides a FastAPI-based REST service that scrapes post metadata and downloads media (images/videos) through a secure VPN tunnel, serving them from a local cache.

Built for reliability and used in production by [Pinchana](https://s.co.ua/pinchana) (Telegram bot).

---

## ✨ Key Features

- **🚀 Dual Scraping Strategy**
  - **GraphQL Scraper (Primary):** High-speed direct queries to Instagram's internal API using `curl-cffi` with JA3/TLS fingerprint impersonation.
  - **Playwright Fallback:** Headless Chromium extraction used when GraphQL is blocked. Supports intercepted JSON, DOM parsing, and Meta tags.
- **🔄 Smart VPN Rotation**
  - Automatically rotates NordVPN IPs via **Gluetun** when 403 or 429 (Rate Limit) responses are detected.
  - Zero-Trust Networking: The scraper has no direct internet access; all traffic *must* pass through the VPN tunnel.
- **💾 Intelligent Storage**
  - Persistent on-disk cache with automatic **LRU (Least Recently Used) eviction**.
  - Default 10GB limit (configurable) to prevent disk exhaustion.
- **🐳 Production Ready**
  - Dockerized with a lean Python 3.13 image.
  - CI/CD pipeline publishing to GitHub Container Registry (GHCR).

---

## 🛠 Tech Stack

- **Core:** Python 3.13, FastAPI, Uvicorn
- **Networking:** `curl-cffi` (HTTP/2 + TLS impersonation), `httpx`
- **Automation:** Playwright + `playwright-stealth`
- **Logic:** Pydantic v2 (Validation), `tenacity` (Retry & Rotation logic)
- **Infrastructure:** Gluetun (VPN Orchestration), Docker

---

## 🚦 Getting Started

### 1. Prerequisites
- Docker & Docker Compose
- NordVPN account (or any provider supported by Gluetun)

### 2. Configure Environment
Create a `.env` file in the root directory:
```bash
cp .env.example .env
```
Fill in your credentials:
```env
WIREGUARD_PRIVATE_KEY=your_private_key
GLUETUN_API_KEY=your_secret_key
```

#### 🛠 Extracting Private Key (Arch Linux)
If you are using Arch Linux, you can extract your NordVPN WireGuard (NordLynx) private key using the following steps:

1. **Install Prerequisites**:
   ```bash
   # Install NordVPN CLI from AUR (using yay or any AUR helper)
   yay -S nordvpn-bin
   # Install WireGuard tools
   sudo pacman -S wireguard-tools
   ```
2. **Login & Connect**:
   ```bash
   sudo systemctl start nordvpnd
   nordvpn login
   nordvpn set technology nordlynx
   nordvpn connect
   ```
3. **Extract Key**:
   Run this while the VPN is connected:
   ```bash
   sudo wg show nordlynx private-key
   ```
4. **Cleanup**:
   ```bash
   nordvpn disconnect
   ```

### 3. Launch with Docker Compose
The recommended way to run InScraper is via Docker Compose, which sets up both the VPN sidecar and the scraper.

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    container_name: gluetun
    cap_add: [NET_ADMIN]
    devices: [/dev/net/tun:/dev/net/tun]
    environment:
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=wireguard
      - WIREGUARD_PRIVATE_KEY=${WIREGUARD_PRIVATE_KEY}
      - SERVER_COUNTRIES=Ukraine,Poland,Germany
      - HTTP_CONTROL_SERVER_ADDRESS=:8000
      - HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE={"auth":"apikey","apikey":"${GLUETUN_API_KEY:-secret-key}"}
    ports:
      - "8000:8000/tcp" # Gluetun API
      - "8080:8080/tcp" # Scraper API
    restart: unless-stopped

  scraper:
    image: ghcr.io/xxanqw/inscraper:latest
    container_name: scraper
    network_mode: "service:gluetun"
    volumes:
      - ./cache:/app/cache
    environment:
      - GLUETUN_CONTROL_URL=http://localhost:8000
      - GLUETUN_API_KEY=${GLUETUN_API_KEY:-secret-key}
    depends_on:
      gluetun:
        condition: service_started
    restart: always
```

Run the stack:
```bash
docker compose up -d
```

---

## 📡 API Reference

### `POST /scrape`
Primary endpoint. Attempts GraphQL scraping first, downloads media, and returns metadata.

**Request:**
```bash
curl -X POST http://localhost:8080/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.instagram.com/p/C6_abcdefg/"}'
```

**Response Sample:**
```json
{
  "shortcode": "C6_abcdefg",
  "author": "username",
  "media_type": "XDTGraphImage",
  "thumbnail_url": "/media/C6_abcdefg/thumbnail.jpg",
  "carousel": null
}
```

### `POST /scrape/playwright`
Force use of the Playwright fallback scraper.

### `GET /media/{shortcode}/{filename}`
Serves cached media files (images, videos, thumbnails).

### `GET /health`
Returns VPN connection status and scraper health.

---

## ⚙️ Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `WIREGUARD_PRIVATE_KEY` | NordVPN WireGuard Private Key | — |
| `GLUETUN_API_KEY` | API key for Gluetun control server | `secret-key` |
| `CACHE_PATH` | Path to store media | `./cache` |
| `CACHE_MAX_SIZE_GB` | Max cache size before LRU eviction | `10.0` |

---

## 🧪 Testing

Run integration tests (requires `uv`):
```bash
uv run pytest tests/test_scrapers.py -s
```
*Note: Tests may skip if your local IP is rate-limited and you aren't running through the VPN tunnel.*

---

## 🛠 Troubleshooting

### `AUTH_ERROR` on Restart
Gluetun clears credentials from memory after startup. **Do not use `docker restart gluetun`**. 

Instead, recreate the container to re-inject environment variables:
```bash
docker compose up -d --force-recreate gluetun
```

*Note: Automatic IP rotation (handled by the app) does NOT require a container restart and is safe from this issue.*

---

## 📦 Deployment & Images

Images are hosted on **GitHub Container Registry (GHCR)**.

- `ghcr.io/xxanqw/inscraper:latest` — Stable release.
- `ghcr.io/xxanqw/inscraper:main` — Bleeding edge (latest commit).
- `ghcr.io/xxanqw/inscraper:vX.Y.Z` — Specific versions.
