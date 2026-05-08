import httpx
import logging
import asyncio
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)


class VpnRotationError(Exception):
    """Raised when the VPN tunnel fails to come up after rotation."""
    pass


class GluetunController:
    """Manage programmatic IP rotation via the Gluetun sidecar API."""

    ROTATION_COOLDOWN = 90  # seconds

    def __init__(self, control_url: Optional[str] = None, api_key: Optional[str] = None):
        self.control_url = control_url or os.getenv("GLUETUN_CONTROL_URL", "http://localhost:8000")
        self.api_key = api_key or os.getenv("GLUETUN_API_KEY", "secret-key")
        self._last_rotation = 0.0

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.control_url,
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15.0,
        )

    async def get_vpn_status(self) -> dict:
        """Fetch current VPN status from Gluetun."""
        client = self._client()
        try:
            resp = await client.get("/v1/vpn/status")
            resp.raise_for_status()
            return resp.json()
        finally:
            await client.aclose()

    async def wait_for_connection(self, timeout: float = 60.0, interval: float = 2.0) -> dict:
        """Poll Gluetun until the VPN is connected or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                status = await self.get_vpn_status()
                state = status.get("state", "").lower()
                vpn_status = status.get("status", "").lower()
                if state == "running" and vpn_status == "connected":
                    logger.info(f"VPN connected (status={status}).")
                    return status
                logger.debug(f"VPN not ready yet: state={state}, status={vpn_status}")
            except httpx.HTTPStatusError as e:
                logger.debug(f"HTTP error polling VPN status: {e.response.status_code}")
            except httpx.ConnectError:
                logger.debug("Gluetun control server not reachable yet.")
            except httpx.HTTPError as e:
                logger.debug(f"Error polling VPN status: {e}")
            await asyncio.sleep(interval)
        raise VpnRotationError(
            f"VPN failed to connect within {timeout}s after rotation — possible transient AUTH_FAILED"
        )

    async def rotate_ip(self):
        """Teardown and rebuild the VPN tunnel, verifying it actually comes up."""
        now = time.time()
        elapsed = now - self._last_rotation
        if elapsed < self.ROTATION_COOLDOWN:
            logger.info(f"VPN rotation on cooldown ({int(elapsed)}s since last). Waiting for tunnel to stabilize...")
            await asyncio.sleep(5)
            return

        self._last_rotation = now
        logger.info("Rate limit hit. Rotating VPN IP...")
        client = self._client()
        try:
            stop_resp = await client.put("/v1/vpn/status", json={"status": "stopped"})
            stop_resp.raise_for_status()

            await asyncio.sleep(3.0)

            start_resp = await client.put("/v1/vpn/status", json={"status": "running"})
            start_resp.raise_for_status()

            # Wait until the tunnel is actually connected instead of blind sleep
            await self.wait_for_connection(timeout=60.0, interval=2.0)
            logger.info("VPN IP rotation completed.")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                logger.error(f"Gluetun auth failed ({e.response.status_code}). Check GLUETUN_API_KEY.")
            else:
                logger.error(f"Gluetun control API error: {e.response.status_code}")
            raise
        except httpx.ConnectError:
            logger.error("Cannot connect to Gluetun control server. Is Gluetun running?")
            raise
        except httpx.HTTPError as e:
            logger.error(f"Failed to communicate with Gluetun control server: {e}")
            raise
        finally:
            await client.aclose()
