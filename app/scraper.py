from curl_cffi.requests import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import urllib.parse
import json
import logging
import asyncio
from .models import ScrapeResponse, ExtractedMedia, MediaDimension
from .vpn_controller import GluetunController

logger = logging.getLogger(__name__)


class ScraperError(Exception):
    """Base exception for extraction logic failures."""
    pass


class RateLimitError(ScraperError):
    """Exception indicating network-level blocking (429/403)."""
    pass


gluetun = GluetunController()


def trigger_rotation(retry_state):
    """Trigger VPN IP rotation before each retry."""
    logger.warning(f"Retry attempt {retry_state.attempt_number}. Rotating VPN IP...")
    asyncio.create_task(gluetun.rotate_ip())


class InstagramGraphScraper:
    # Volatile parameter; may need updating if Instagram changes their API.
    DOC_ID = "8845758582119845"

    def __init__(self):
        self.base_headers = {
            "x-ig-app-id": "936619743392459",
            "x-asbd-id": "198387",
            "x-ig-www-claim": "0",
            "x-requested-with": "XMLHttpRequest",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
        }

    async def _bootstrap_session(self, session: AsyncSession):
        """Harvest CSRF token and tracking cookies from Instagram's homepage."""
        response = await session.get("https://www.instagram.com/")
        response.raise_for_status()
        csrf_token = session.cookies.get("csrftoken")
        if csrf_token:
            self.base_headers["x-csrftoken"] = csrf_token
        else:
            logger.warning("Failed to extract CSRF token during bootstrap.")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1.5, min=4, max=30),
        retry=retry_if_exception_type(RateLimitError),
        before_sleep=trigger_rotation
    )
    async def extract_media(self, shortcode: str) -> dict:
        """Query Instagram's GraphQL API with a spoofed JA3 TLS fingerprint."""
        async with AsyncSession(impersonate="chrome124") as session:
            await self._bootstrap_session(session)

            variables = json.dumps({
                "shortcode": shortcode,
                "child_comment_count": 0,
                "fetch_comment_count": 0
            })

            payload = {
                "doc_id": self.DOC_ID,
                "variables": variables
            }

            # Instagram requires form-encoded payloads, not JSON.
            encoded_payload = urllib.parse.urlencode(payload)
            headers = self.base_headers.copy()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

            response = await session.post(
                "https://www.instagram.com/graphql/query/",
                headers=headers,
                data=encoded_payload
            )

            if response.status_code in [403, 429]:
                raise RateLimitError(f"HTTP {response.status_code}: IP restriction detected.")

            response.raise_for_status()
            data = response.json()

            try:
                return data['data']['xdt_shortcode_media']
            except (KeyError, TypeError):
                raise ScraperError("Failed to parse GraphQL response. The active doc_id may be deprecated.")

    def parse_response(self, raw_data: dict) -> ScrapeResponse:
        """Transform raw GraphQL response into the Pydantic schema."""
        typename = raw_data.get("__typename")

        caption_edges = raw_data.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""

        engagement = {
            "likes": raw_data.get("edge_media_preview_like", {}).get("count", 0),
            "comments": raw_data.get("edge_media_to_parent_comment", {}).get("count", 0),
            "video_views": raw_data.get("video_view_count", 0)
        }

        primary_media = ExtractedMedia(
            media_type=typename,
            display_url=raw_data.get("display_url"),
            video_url=raw_data.get("video_url") if raw_data.get("is_video") else None,
            dimensions=MediaDimension(
                height=raw_data.get("dimensions", {}).get("height", 0),
                width=raw_data.get("dimensions", {}).get("width", 0)
            )
        )

        carousel_children = None
        if typename in ("GraphSidecar", "XDTGraphSidecar"):
            carousel_children = []
            edges = raw_data.get("edge_sidecar_to_children", {}).get("edges", [])
            for edge in edges:
                node = edge["node"]
                child_media = ExtractedMedia(
                    media_type=node.get("__typename"),
                    display_url=node.get("display_url"),
                    video_url=node.get("video_url") if node.get("is_video") else None,
                    dimensions=MediaDimension(
                        height=node.get("dimensions", {}).get("height", 0),
                        width=node.get("dimensions", {}).get("width", 0)
                    )
                )
                carousel_children.append(child_media)

        return ScrapeResponse(
            shortcode=raw_data.get("shortcode"),
            caption=caption,
            primary_media=primary_media,
            carousel_children=carousel_children,
            engagement=engagement
        )
