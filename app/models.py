from pydantic import BaseModel, HttpUrl, Field
from typing import Optional, List, Dict, Any

class ScrapeRequest(BaseModel):
    """Client payload specifying the target URL."""
    url: HttpUrl = Field(..., description="The complete Instagram Post, Reel, or Carousel URL.")
    proxy: bool = Field(False, description="If true, rewrite media URLs to route through the local /proxy endpoint.")

class MediaDimension(BaseModel):
    """Structural dimensions of the extracted asset."""
    height: int
    width: int

class ExtractedMedia(BaseModel):
    """Normalized media object representing a single asset."""
    media_type: str = Field(..., description="GraphImage, GraphVideo, or GraphSidecar")
    display_url: str = Field(..., description="High-resolution image thumbnail URL")
    video_url: Optional[str] = Field(None, description="Direct MP4 URL for Reels/Videos")
    dimensions: MediaDimension

class ScrapeResponse(BaseModel):
    """The complete response schema returned to the API client."""
    shortcode: str
    caption: str
    primary_media: ExtractedMedia
    carousel_children: Optional[List[ExtractedMedia]] = Field(None, description="Nested assets for Carousels")
    engagement: Dict[str, int] = Field(..., description="Aggregated likes, comments, and views")
