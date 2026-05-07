import httpx
import logging
import asyncio
import os
from typing import Optional

logger = logging.getLogger(__name__)


class GluetunController:
    """Manage programmatic IP rotation via the Gluetun sidecar API."""

    def __init__(self, control_url: Optional[str] = None, api_key: Optional[str] = None):
        self.control_url = control_url or os.getenv("GLUETUN_CONTROL_URL", "http://localhost:8000")
        self.api_key = api_key or os.getenv("GLUETUN_API_KEY", "secret-key")
        self.headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.client = httpx.AsyncClient(base_url=self.control_url, headers=self.headers, timeout=15.0)

    async def rotate_ip(self):
        """Teardown and rebuild the VPN tunnel to get a new IP."""
        logger.info("Rate limit hit. Rotating VPN IP...")
        try:
            stop_resp = await self.client.put("/v1/vpn/status", json={"status": "stopped"})
            stop_resp.raise_for_status()

            await asyncio.sleep(3.0)

            start_resp = await self.client.put("/v1/vpn/status", json={"status": "running"})
            start_resp.raise_for_status()

            await asyncio.sleep(7.0)
            logger.info("VPN IP rotation completed.")
        except httpx.HTTPError as e:
            logger.error(f"Failed to communicate with Gluetun control server: {e}")
            raise

    async def close(self):
        await self.client.aclose()
