import pytest
import asyncio
from app.scraper import InstagramGraphScraper
from app.playwright_scraper import InstagramPlaywrightScraper
from app.models import ScrapeResponse
import re

# Test URLs provided by the user
TEST_URLS = [
    "https://www.instagram.com/p/DUlFguzjAwl/", # Carousel
    "https://www.instagram.com/p/CuWXKUiMS-o/", # Single picture
    "https://www.instagram.com/reels/DVqyGcuj7yA/", # Reels
    "https://www.instagram.com/p/DX_VVYRgOVz/"  # Carousel with video
]

def get_shortcode(url):
    match = re.search(r"(?:p|reels|reel|tv|share/v)/([^/?#&]+)", url)
    return match.group(1) if match else None

@pytest.fixture
def graph_scraper():
    return InstagramGraphScraper()

@pytest.fixture
def pw_scraper():
    return InstagramPlaywrightScraper()

@pytest.mark.asyncio
@pytest.mark.parametrize("url", TEST_URLS)
async def test_playwright_scraper_live(pw_scraper, url):
    """Verifies that the Playwright scraper can extract valid data from live URLs."""
    print(f"\nTesting Playwright Scraper with: {url}")
    result = await pw_scraper.scrape(url)
    
    assert result is not None, f"Playwright scraper failed to return a result for {url}"
    assert isinstance(result, ScrapeResponse), f"Result for {url} is not a valid ScrapeResponse object"
    assert result.shortcode == get_shortcode(url), f"Shortcode mismatch for {url}"
    assert result.primary_media.display_url.startswith("https://"), f"Invalid display_url for {url}"
    
    # Check for carousel items if it's a known carousel
    if "/p/" in url and (url == TEST_URLS[0] or url == TEST_URLS[3]):
        assert result.primary_media.media_type == "GraphSidecar", f"Expected GraphSidecar for {url}"
        assert result.carousel_children is not None and len(result.carousel_children) > 0, f"Expected carousel children for {url}"

@pytest.mark.asyncio
@pytest.mark.parametrize("url", TEST_URLS)
async def test_graph_scraper_live(graph_scraper, url):
    """
    Verifies the direct Graph scraper. 
    Note: This may fail locally if the IP is rate-limited and no Gluetun sidecar is present.
    """
    shortcode = get_shortcode(url)
    assert shortcode is not None
    
    print(f"\nTesting Graph Scraper with: {url} (shortcode: {shortcode})")
    try:
        raw_data = await graph_scraper.extract_media(shortcode)
        result = graph_scraper.parse_response(raw_data)
        
        assert result is not None
        assert isinstance(result, ScrapeResponse)
        assert result.shortcode == shortcode
    except Exception as e:
        # If we hit a rate limit locally, we log it but don't necessarily fail the test suite 
        # as this scraper is designed to run behind a VPN.
        if "429" in str(e) or "403" in str(e):
            pytest.skip(f"Local IP rate limited for Graph Scraper on {url}")
        else:
            raise e
