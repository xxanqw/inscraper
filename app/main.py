from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
import os
import re
import logging
import urllib.parse
import httpx
from .models import ScrapeRequest, ScrapeResponse, ExtractedMedia
from .scraper import InstagramGraphScraper, RateLimitError
from .playwright_scraper import InstagramPlaywrightScraper
from .cache import DiskCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="InstaCaper API", version="1.0.0")
scraper = InstagramGraphScraper()
pw_scraper = InstagramPlaywrightScraper()
disk_cache = DiskCache(
    db_path=os.getenv("CACHE_PATH", "./cache/scraper.db"),
    max_size_gb=float(os.getenv("CACHE_MAX_SIZE_GB", "10.0")),
    ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
)


def extract_shortcode(url: str) -> str:
    """Extract the shortcode from Instagram URL formats (p, reels, reel, tv, share/v)."""
    match = re.search(r"(?:p|reels|reel|tv|share/v)/([^/?#&]+)", str(url))
    if not match:
        raise HTTPException(status_code=400, detail="Invalid target URL format.")
    return match.group(1)


def _proxify_url(base_url: str, target_url: str | None) -> str | None:
    if not target_url:
        return None
    return f"{base_url}proxy?url={urllib.parse.quote(target_url, safe='')}"


def _proxify_media(media: ExtractedMedia, base_url: str) -> ExtractedMedia:
    return ExtractedMedia(
        media_type=media.media_type,
        display_url=_proxify_url(base_url, media.display_url),
        video_url=_proxify_url(base_url, media.video_url),
        dimensions=media.dimensions,
    )


def _proxify_response(response: ScrapeResponse, base_url: str) -> ScrapeResponse:
    return ScrapeResponse(
        shortcode=response.shortcode,
        caption=response.caption,
        primary_media=_proxify_media(response.primary_media, base_url),
        carousel_children=[
            _proxify_media(child, base_url) for child in response.carousel_children
        ] if response.carousel_children else None,
        engagement=response.engagement,
    )


@app.post("/scrape", response_model=ScrapeResponse)
async def process_scrape_request(request: ScrapeRequest, fastapi_request: Request):
    shortcode = extract_shortcode(str(request.url))

    cached = await disk_cache.get(shortcode)
    if cached:
        logger.info(f"Cache hit for shortcode: {shortcode}")
        result = ScrapeResponse(**cached)
        if request.proxy:
            result = _proxify_response(result, str(fastapi_request.base_url))
        return result

    logger.info(f"Processing scrape request for shortcode: {shortcode}")
    try:
        raw_graph_data = await scraper.extract_media(shortcode)
        parsed_data = scraper.parse_response(raw_graph_data)
        await disk_cache.set(shortcode, parsed_data.model_dump())
        if request.proxy:
            parsed_data = _proxify_response(parsed_data, str(fastapi_request.base_url))
        return parsed_data
    except RateLimitError as e:
        logger.error(f"Rate limit exhausted: {str(e)}")
        raise HTTPException(status_code=503, detail="Service Unavailable: Proxies exhausted or rate limited.")
    except Exception as e:
        logger.error(f"Internal Processing Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal Processing Error: {str(e)}")


@app.post("/scrape/playwright", response_model=ScrapeResponse)
async def process_playwright_scrape_request(request: ScrapeRequest, fastapi_request: Request):
    """Fallback scraper using Playwright for DOM-based extraction."""
    shortcode = extract_shortcode(str(request.url))

    cached = await disk_cache.get(shortcode)
    if cached:
        logger.info(f"Cache hit for shortcode: {shortcode}")
        result = ScrapeResponse(**cached)
        if request.proxy:
            result = _proxify_response(result, str(fastapi_request.base_url))
        return result

    logger.info(f"Processing Playwright scrape request for: {request.url}")
    result = await pw_scraper.scrape(str(request.url))
    if not result:
        raise HTTPException(status_code=500, detail="Playwright scraper failed to extract data.")
    await disk_cache.set(shortcode, result.model_dump())
    if request.proxy:
        result = _proxify_response(result, str(fastapi_request.base_url))
    return result


@app.get("/proxy")
async def proxy_media(url: str = Query(..., description="Target media URL to fetch through the VPN tunnel")):
    """Stream media from Instagram's CDN through the VPN tunnel."""
    client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)
    try:
        response = await client.send(
            client.build_request(
                "GET", url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Referer": "https://www.instagram.com/",
                    "Accept": "*/*",
                }
            ),
            stream=True
        )

        async def stream_generator():
            try:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        headers = {}
        for header in ["content-type", "content-disposition", "etag", "last-modified", "cache-control"]:
            if header in response.headers:
                headers[header] = response.headers[header]

        return StreamingResponse(
            stream_generator(),
            status_code=response.status_code,
            headers=headers
        )
    except Exception:
        await client.aclose()
        raise


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
