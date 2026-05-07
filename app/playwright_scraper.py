import asyncio
import json
import logging
import re
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)


class InstagramPlaywrightScraper:
    def __init__(self):
        self.SELECTORS = {
            "next_button": "button[aria-label='Next']",
            "login_overlay": "div[role='dialog'] span:has-text('Sign up')",
            "media_article": "article[role='presentation']",
            "caption": "article h1, article div[dir='auto']",
            "likes": "section span:has-text('likes')"
        }

    async def scrape(self, url: str) -> Optional[Dict]:
        """Scrape using Playwright with stealth and network interception."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900}
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            intercepted_data = {"media_node": None}

            async def on_response(response):
                if "/graphql/query" in response.url:
                    try:
                        data = await response.json()
                        node = self._find_media_node(data)
                        if node:
                            intercepted_data["media_node"] = node
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                logger.info(f"Navigating to {url} via Playwright...")
                await page.goto(url, wait_until="networkidle", timeout=60000)

                # Remove login popups
                await page.evaluate("""() => {
                    const removeOverlays = () => {
                        const targets = ['div[role="dialog"]', 'div.x1n2onr6'];
                        targets.forEach(sel => {
                            document.querySelectorAll(sel).forEach(el => {
                                if (el.innerText.includes('Sign up') || el.innerText.includes('Log in')) {
                                    el.remove();
                                }
                            });
                        });
                        document.body.style.overflow = 'auto';
                        document.documentElement.style.overflow = 'auto';
                    };
                    removeOverlays();
                    setInterval(removeOverlays, 1000);
                }""")

                # Trigger lazy loading in carousels
                for _ in range(12):
                    next_btn = page.locator(self.SELECTORS["next_button"])
                    if await next_btn.is_visible():
                        await next_btn.click(force=True)
                        await asyncio.sleep(0.6)
                    else:
                        break

                if intercepted_data["media_node"]:
                    logger.info("Using intercepted GraphQL data.")
                    return self._parse_graphql_node(intercepted_data["media_node"])

                embedded_node = await self._extract_embedded_json(page)
                if embedded_node:
                    logger.info("Using embedded JSON data.")
                    return self._parse_graphql_node(embedded_node)

                logger.warning("All extraction methods failed.")
                return None

            except Exception as e:
                logger.error(f"Playwright scrape failed for {url}: {e}")
                return None
            finally:
                await browser.close()

    async def _extract_embedded_json(self, page) -> Optional[Dict]:
        """Search script tags for the media object."""
        return await page.evaluate("""
            () => {
                const findMedia = (obj) => {
                    if (!obj || typeof obj !== 'object') return null;
                    if (obj.xdt_shortcode_media) return obj.xdt_shortcode_media;
                    if (obj.shortcode_media) return obj.shortcode_media;
                    for (const k in obj) {
                        const res = findMedia(obj[k]);
                        if (res) return res;
                    }
                    return null;
                };
                const scripts = Array.from(document.querySelectorAll('script[type="application/json"]'));
                for (const s of scripts) {
                    try {
                        const data = JSON.parse(s.innerText);
                        const media = findMedia(data);
                        if (media) return media;
                    } catch (e) {}
                }
                return null;
            }
        """)

    def _find_media_node(self, obj: Any) -> Optional[Dict]:
        if isinstance(obj, dict):
            if "xdt_shortcode_media" in obj:
                return obj["xdt_shortcode_media"]
            if "shortcode_media" in obj:
                return obj["shortcode_media"]
            for v in obj.values():
                res = self._find_media_node(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = self._find_media_node(item)
                if res:
                    return res
        return None

    def _parse_graphql_node(self, node: Dict) -> Dict:
        """Map a GraphQL node to a plain dict."""
        typename = node.get("__typename", "GraphImage")

        caption_edges = (node.get("edge_media_to_caption") or {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""
        author = (node.get("owner") or {}).get("username", "")

        primary_media = {
            "media_type": typename,
            "display_url": node.get("display_url", ""),
            "video_url": node.get("video_url") if "video" in typename.lower() else None,
        }

        carousel_children = None
        if typename in ("GraphSidecar", "XDTGraphSidecar"):
            carousel_children = []
            edges = (node.get("edge_sidecar_to_children") or {}).get("edges", [])
            for edge in edges:
                child = edge["node"]
                carousel_children.append({
                    "media_type": child.get("__typename", "GraphImage"),
                    "display_url": child.get("display_url", ""),
                    "video_url": child.get("video_url") if child.get("is_video") else None,
                })

        return {
            "shortcode": node.get("shortcode", ""),
            "caption": caption,
            "author": author,
            "primary_media": primary_media,
            "carousel_children": carousel_children,
        }
