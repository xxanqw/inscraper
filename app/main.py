import asyncio
import os
import re
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from .models import ScrapeRequest, ScrapeResponse, MediaItem
from .scraper import InstagramGraphScraper, RateLimitError
from .playwright_scraper import InstagramPlaywrightScraper
from .storage import MediaStorage
from .vpn_controller import GluetunController, VpnRotationError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="InstaCaper API", version="1.0.0")
scraper = InstagramGraphScraper()
pw_scraper = InstagramPlaywrightScraper()
gluetun = GluetunController()
storage = MediaStorage(
    base_path=os.getenv("CACHE_PATH", "./cache"),
    max_size_gb=float(os.getenv("CACHE_MAX_SIZE_GB", "10.0")),
)


def extract_shortcode(url: str) -> str:
    """Extract the shortcode from Instagram URL formats."""
    match = re.search(r"(?:p|reels|reel|tv|share/v)/([^/?#&]+)", str(url))
    if not match:
        raise HTTPException(status_code=400, detail="Invalid target URL format.")
    return match.group(1)


async def _download_and_build_response(shortcode: str, raw: dict) -> ScrapeResponse:
    """Download media files and build the API response with local URLs."""
    storage.prepare_post_dir(shortcode)

    primary = raw["primary_media"]
    carousel = raw.get("carousel_children")

    # Download primary media
    tasks = []
    if primary.get("display_url"):
        tasks.append(storage.download(primary["display_url"], storage.thumbnail_path(shortcode)))
    if primary.get("video_url"):
        tasks.append(storage.download(primary["video_url"], storage.video_path(shortcode)))

    # Download carousel items
    if carousel:
        for idx, child in enumerate(carousel):
            if child.get("display_url"):
                tasks.append(storage.download(
                    child["display_url"], storage.carousel_thumbnail_path(shortcode, idx)
                ))
            if child.get("video_url"):
                tasks.append(storage.download(
                    child["video_url"], storage.carousel_video_path(shortcode, idx)
                ))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"Download error: {r}")

    thumbnail_exists = storage.thumbnail_path(shortcode).exists()
    video_exists = storage.video_path(shortcode).exists()

    carousel_items = []
    if carousel:
        for idx, child in enumerate(carousel):
            carousel_items.append(MediaItem(
                index=idx,
                media_type=child["media_type"],
                thumbnail_url=f"/media/{shortcode}/carousel/{idx}_thumbnail.jpg",
                video_url=f"/media/{shortcode}/carousel/{idx}_video.mp4"
                if storage.carousel_video_path(shortcode, idx).exists() else None,
            ))

    response = ScrapeResponse(
        shortcode=shortcode,
        caption=raw["caption"],
        author=raw["author"],
        media_type=primary["media_type"],
        thumbnail_url=f"/media/{shortcode}/thumbnail.jpg" if thumbnail_exists else "",
        video_url=f"/media/{shortcode}/video.mp4" if video_exists else None,
        carousel=carousel_items if carousel else None,
    )

    storage.save_metadata(shortcode, response.model_dump())
    return response


@app.post("/scrape", response_model=ScrapeResponse)
async def process_scrape_request(request: ScrapeRequest):
    shortcode = extract_shortcode(str(request.url))

    if storage.is_cached(shortcode):
        logger.info(f"Cache hit for shortcode: {shortcode}")
        metadata = storage.load_metadata(shortcode)
        return ScrapeResponse(**metadata)

    logger.info(f"Scraping shortcode: {shortcode}")
    last_error = None

    # Endpoint-level retry: if VPN is restarting, wait and try again
    # so the bot/user never has to resend the link.
    for attempt in range(1, 4):
        try:
            raw_graph_data = await scraper.extract_media(shortcode)
            raw = scraper.parse_response(raw_graph_data)
            return await _download_and_build_response(shortcode, raw)
        except RateLimitError as e:
            last_error = e
            logger.warning(f"Attempt {attempt} for {shortcode} rate-limited/timed-out: {e}")
            if attempt < 3:
                await asyncio.sleep(15)
        except VpnRotationError as e:
            last_error = e
            logger.warning(f"Attempt {attempt} for {shortcode} VPN rotation failed: {e}")
            if attempt < 3:
                await asyncio.sleep(30)
        except Exception as e:
            last_error = e
            logger.error(f"Attempt {attempt} for {shortcode} failed: {e}")
            if attempt < 3:
                await asyncio.sleep(15)

    # All attempts exhausted
    if isinstance(last_error, RateLimitError):
        logger.error(f"Rate limit exhausted for {shortcode}: {last_error}")
        raise HTTPException(status_code=503, detail="Service Unavailable: Proxies exhausted or rate limited.")
    logger.error(f"Internal Processing Error for {shortcode}: {last_error}")
    raise HTTPException(status_code=500, detail=f"Internal Processing Error: {last_error}")


@app.post("/scrape/playwright", response_model=ScrapeResponse)
async def process_playwright_scrape_request(request: ScrapeRequest):
    shortcode = extract_shortcode(str(request.url))

    if storage.is_cached(shortcode):
        logger.info(f"Cache hit for shortcode: {shortcode}")
        metadata = storage.load_metadata(shortcode)
        return ScrapeResponse(**metadata)

    logger.info(f"Processing Playwright scrape request for: {request.url}")
    raw = await pw_scraper.scrape(str(request.url))
    if not raw:
        raise HTTPException(status_code=500, detail="Playwright scraper failed to extract data.")
    return await _download_and_build_response(shortcode, raw)


@app.get("/media/{shortcode}/{filename:path}")
async def serve_media(shortcode: str, filename: str):
    """Serve locally stored media files."""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=404, detail="Invalid path")

    file_path = storage.base_path / shortcode / filename
    resolved = file_path.resolve()
    base_resolved = storage.base_path.resolve()
    if not str(resolved).startswith(str(base_resolved)):
        raise HTTPException(status_code=404, detail="Invalid path")

    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(resolved)


@app.get("/health")
async def health_check():
    try:
        status = await gluetun.get_vpn_status()
        vpn_status = status.get("status", "").lower()
        if vpn_status != "connected":
            raise HTTPException(
                status_code=503,
                detail=f"VPN tunnel not connected (status: {vpn_status})."
            )
        return {"status": "healthy", "vpn": status}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Health check failed to query VPN status: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Unable to verify VPN status: {e}"
        )
