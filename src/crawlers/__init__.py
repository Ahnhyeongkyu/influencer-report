"""
멀티플랫폼 인플루언서 캠페인 크롤러 모듈

지원 플랫폼:
- 샤오홍슈 (小红书/RED)
- 페이스북
- 인스타그램
- 유튜브
- 디카드 (대만)
"""

from .xhs_crawler import (
    XHSCrawler,
    XHSCrawlerError,
    XHSLoginError,
    XHSPostLoadError,
    crawl_xhs_post,
    crawl_xhs_posts,
)

from .youtube_crawler import (
    YouTubeCrawler,
    YouTubeCrawlerError,
    YouTubeVideoNotFoundError,
    YouTubeRateLimitError,
    crawl_youtube_video,
    crawl_youtube_videos,
)

from .dcard_crawler import (
    DcardCrawler,
    DcardCrawlerError,
    DcardAPIError,
    DcardPostNotFoundError,
    DcardCloudflareError,
    crawl_dcard_post,
    crawl_dcard_posts,
)

from .facebook_crawler import (
    FacebookCrawler,
    FacebookCrawlerError,
    FacebookLoginError,
    FacebookPostLoadError,
    FacebookRateLimitError,
    crawl_facebook_post,
    crawl_facebook_posts,
)

from .instagram_crawler import (
    InstagramCrawler,
    InstagramCrawlerError,
    InstagramLoginError,
    InstagramPostLoadError,
    InstagramRateLimitError,
    crawl_instagram_post,
    crawl_instagram_posts,
)

__all__ = [
    # 샤오홍슈
    "XHSCrawler",
    "XHSCrawlerError",
    "XHSLoginError",
    "XHSPostLoadError",
    "crawl_xhs_post",
    "crawl_xhs_posts",
    # 유튜브
    "YouTubeCrawler",
    "YouTubeCrawlerError",
    "YouTubeVideoNotFoundError",
    "YouTubeRateLimitError",
    "crawl_youtube_video",
    "crawl_youtube_videos",
    # Dcard
    "DcardCrawler",
    "DcardCrawlerError",
    "DcardAPIError",
    "DcardPostNotFoundError",
    "DcardCloudflareError",
    "crawl_dcard_post",
    "crawl_dcard_posts",
    # Facebook
    "FacebookCrawler",
    "FacebookCrawlerError",
    "FacebookLoginError",
    "FacebookPostLoadError",
    "FacebookRateLimitError",
    "crawl_facebook_post",
    "crawl_facebook_posts",
    # Instagram
    "InstagramCrawler",
    "InstagramCrawlerError",
    "InstagramLoginError",
    "InstagramPostLoadError",
    "InstagramRateLimitError",
    "crawl_instagram_post",
    "crawl_instagram_posts",
]

__version__ = "1.3.0"
