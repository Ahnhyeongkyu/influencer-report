"""
YouTube 크롤러 모듈

yt-dlp를 활용한 YouTube 동영상 데이터 크롤링
- 조회수, 좋아요, 댓글 수, 채널명, 제목, 구독자 수 수집
- API 키 불필요 (yt-dlp 기반)
- Rate limiting 및 에러 핸들링 내장
"""

import logging
import re
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

# 로거 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 콘솔 핸들러 추가 (기본)
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class YouTubeCrawlerError(Exception):
    """YouTube 크롤러 기본 예외"""
    pass


class YouTubeVideoNotFoundError(YouTubeCrawlerError):
    """동영상을 찾을 수 없음"""
    pass


class YouTubeRateLimitError(YouTubeCrawlerError):
    """Rate limit 초과"""
    pass


class YouTubeCrawler:
    """
    YouTube 크롤러 클래스

    yt-dlp를 사용하여 동영상 메타데이터 수집
    API 키 없이 작동하며, 필요시 API 키 사용 가능
    """

    # 기본 설정
    DEFAULT_TIMEOUT = 30
    REQUEST_DELAY = 1.0  # 요청 간 딜레이 (초)
    MAX_RETRIES = 3
    RETRY_DELAY = 5.0

    # YouTube URL 패턴
    YOUTUBE_URL_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        request_delay: float = REQUEST_DELAY,
        collect_comments: bool = True,  # 댓글 내용 수집 여부
        max_comments: int = 10,  # 수집할 최대 댓글 수
    ):
        """
        크롤러 초기화

        Args:
            api_key: YouTube Data API v3 키 (선택적, 현재 미사용)
            timeout: 요청 타임아웃 (초)
            request_delay: 요청 간 딜레이 (초)
            collect_comments: 댓글 내용 수집 여부
            max_comments: 수집할 최대 댓글 수
        """
        self.api_key = api_key
        self.timeout = timeout
        self.request_delay = request_delay
        self.collect_comments = collect_comments
        self.max_comments = max_comments

        # yt-dlp 옵션 설정
        self._ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': timeout,
            'retries': self.MAX_RETRIES,
            # 봇 탐지 우회를 위한 User-Agent
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            },
        }

        # 댓글 수집용 yt-dlp 옵션
        self._ydl_opts_with_comments = {
            **self._ydl_opts,
            'getcomments': True,
            'extractor_args': {
                'youtube': {
                    'max_comments': [str(max_comments)],
                    'comment_sort': ['top'],  # 인기순 댓글
                }
            }
        }

        self._ydl = None
        self._ydl_comments = None
        logger.info(f"YouTubeCrawler 초기화 완료 (collect_comments={collect_comments})")

    def _get_ydl(self, with_comments: bool = False):
        """yt-dlp 인스턴스 가져오기 (지연 로딩)

        Args:
            with_comments: 댓글 수집 옵션 포함 여부
        """
        try:
            import yt_dlp

            if with_comments:
                if self._ydl_comments is None:
                    self._ydl_comments = yt_dlp.YoutubeDL(self._ydl_opts_with_comments)
                return self._ydl_comments
            else:
                if self._ydl is None:
                    self._ydl = yt_dlp.YoutubeDL(self._ydl_opts)
                return self._ydl

        except ImportError:
            raise YouTubeCrawlerError(
                "yt-dlp가 설치되지 않았습니다. "
                "'pip install yt-dlp' 명령으로 설치해주세요."
            )

    def _extract_video_id(self, url: str) -> Optional[str]:
        """
        YouTube URL에서 동영상 ID 추출

        Args:
            url: YouTube URL

        Returns:
            동영상 ID 또는 None
        """
        for pattern in self.YOUTUBE_URL_PATTERNS:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _validate_url(self, url: str) -> bool:
        """
        YouTube URL 유효성 검사

        Args:
            url: 검사할 URL

        Returns:
            유효 여부
        """
        video_id = self._extract_video_id(url)
        return video_id is not None

    def _normalize_url(self, url: str) -> str:
        """
        YouTube URL 정규화

        Args:
            url: YouTube URL

        Returns:
            정규화된 URL
        """
        video_id = self._extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        return url

    def _parse_count(self, value: Any) -> int:
        """
        숫자 값 파싱

        Args:
            value: 숫자 또는 숫자 문자열

        Returns:
            정수 값
        """
        if value is None:
            return 0

        if isinstance(value, int):
            return value

        if isinstance(value, float):
            return int(value)

        if isinstance(value, str):
            # 숫자만 추출
            num = re.sub(r'[^\d]', '', value)
            return int(num) if num else 0

        return 0

    def _extract_with_ytdlp(self, url: str) -> Dict[str, Any]:
        """
        yt-dlp를 사용하여 동영상 정보 추출

        Args:
            url: YouTube URL

        Returns:
            동영상 메타데이터
        """
        # 먼저 기본 정보 추출 (댓글 없이) - comment_count를 정확히 얻기 위함
        # getcomments + max_comments 옵션 사용 시 comment_count가 수집된 댓글 수로 제한됨
        ydl_basic = self._get_ydl(with_comments=False)

        try:
            info = ydl_basic.extract_info(url, download=False)

            if info is None:
                raise YouTubeVideoNotFoundError(f"동영상 정보를 가져올 수 없습니다: {url}")

            # 실제 총 댓글 수 저장 (getcomments 옵션 없이 가져온 값)
            total_comment_count = self._parse_count(info.get("comment_count"))

            # 댓글 내용 수집이 필요한 경우 별도로 가져오기
            comments_list = []
            if self.collect_comments:
                try:
                    ydl_comments = self._get_ydl(with_comments=True)
                    info_with_comments = ydl_comments.extract_info(url, download=False)
                    if info_with_comments and info_with_comments.get("comments"):
                        raw_comments = info_with_comments.get("comments", [])
                        for comment in raw_comments[:self.max_comments]:
                            comment_data = {
                                "author": comment.get("author", "익명"),
                                "text": comment.get("text", ""),
                                "likes": self._parse_count(comment.get("like_count", 0)),
                                "timestamp": comment.get("timestamp"),
                            }
                            if comment_data["text"]:  # 빈 댓글 제외
                                comments_list.append(comment_data)
                        logger.info(f"댓글 {len(comments_list)}개 수집됨")
                except Exception as e:
                    logger.warning(f"댓글 수집 중 오류 (무시): {e}")

            return {
                "platform": "youtube",
                "url": self._normalize_url(url),
                "video_id": info.get("id"),
                "author": info.get("uploader") or info.get("channel"),
                "author_id": info.get("uploader_id") or info.get("channel_id"),
                "channel_url": info.get("channel_url") or info.get("uploader_url"),
                "title": info.get("title"),
                "description": info.get("description", "")[:500] if info.get("description") else None,
                "likes": self._parse_count(info.get("like_count")),
                "comments": total_comment_count,  # 실제 총 댓글 수 사용
                "views": self._parse_count(info.get("view_count")),
                "subscribers": self._parse_count(info.get("channel_follower_count")),
                "duration": info.get("duration"),  # 초 단위
                "upload_date": info.get("upload_date"),  # YYYYMMDD 형식
                "thumbnail": info.get("thumbnail"),
                "categories": info.get("categories", []),
                "tags": info.get("tags", []),
                "is_live": info.get("is_live", False),
                "comments_list": comments_list,  # 댓글 내용 리스트
                "crawled_at": datetime.now().isoformat(),
            }

        except Exception as e:
            error_msg = str(e).lower()

            # 에러 타입 분류
            if "video unavailable" in error_msg or "private video" in error_msg:
                raise YouTubeVideoNotFoundError(f"동영상을 찾을 수 없거나 비공개입니다: {url}")
            elif "rate" in error_msg or "too many" in error_msg:
                raise YouTubeRateLimitError(f"Rate limit 초과: {url}")
            else:
                raise YouTubeCrawlerError(f"동영상 정보 추출 실패: {e}")

    def crawl_video(self, url: str, retry: bool = True) -> Dict[str, Any]:
        """
        단일 YouTube 동영상 데이터 크롤링

        Args:
            url: YouTube 동영상 URL
            retry: 실패 시 재시도 여부

        Returns:
            {
                "platform": "youtube",
                "url": str,
                "author": str,
                "title": str,
                "likes": int,
                "comments": int,
                "views": int,
                "subscribers": int or None,
                "crawled_at": str
            }
        """
        # URL 유효성 검사 - ValueError 대신 에러 dict 반환 (verify-bot 프로토콜)
        if not url:
            logger.error("URL이 비어있습니다")
            return {
                "platform": "youtube",
                "url": url or "",
                "video_id": None,
                "author": None,
                "title": None,
                "likes": 0,
                "comments": 0,
                "views": 0,
                "subscribers": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "URL이 비어있습니다.",
                "error_type": "validation_error"
            }

        if not self._validate_url(url):
            logger.error(f"유효하지 않은 YouTube URL: {url}")
            return {
                "platform": "youtube",
                "url": url,
                "video_id": None,
                "author": None,
                "title": None,
                "likes": 0,
                "comments": 0,
                "views": 0,
                "subscribers": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "유효하지 않은 YouTube URL입니다. youtube.com 또는 youtu.be URL을 입력해주세요.",
                "error_type": "validation_error"
            }

        logger.info(f"YouTube 동영상 크롤링 시작: {url}")

        last_error = None
        attempts = self.MAX_RETRIES if retry else 1

        for attempt in range(attempts):
            try:
                result = self._extract_with_ytdlp(url)
                logger.info(
                    f"크롤링 성공: views={result['views']}, "
                    f"likes={result['likes']}, comments={result['comments']}"
                )
                return result

            except YouTubeRateLimitError as e:
                logger.warning(f"Rate limit 감지. {self.RETRY_DELAY}초 후 재시도...")
                last_error = e
                time.sleep(self.RETRY_DELAY)

            except YouTubeVideoNotFoundError:
                raise

            except Exception as e:
                last_error = e
                if attempt < attempts - 1:
                    logger.warning(
                        f"크롤링 실패 (시도 {attempt + 1}/{attempts}): {e}. "
                        f"{self.RETRY_DELAY}초 후 재시도..."
                    )
                    time.sleep(self.RETRY_DELAY)

        # 모든 재시도 실패
        raise YouTubeCrawlerError(f"크롤링 실패 (최대 재시도 초과): {last_error}")

    def crawl_videos(
        self,
        urls: List[str],
        delay: Optional[float] = None,
        continue_on_error: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        여러 YouTube 동영상 데이터 크롤링

        Args:
            urls: 동영상 URL 리스트
            delay: 요청 간 딜레이 (초). None이면 기본값 사용
            continue_on_error: 에러 발생 시 계속 진행 여부

        Returns:
            동영상 데이터 리스트
        """
        if delay is None:
            delay = self.request_delay

        results = []

        for i, url in enumerate(urls):
            try:
                logger.info(f"크롤링 중 ({i + 1}/{len(urls)}): {url}")
                result = self.crawl_video(url)
                results.append(result)

            except Exception as e:
                logger.error(f"크롤링 실패 ({url}): {e}")

                if continue_on_error:
                    results.append({
                        "platform": "youtube",
                        "url": url,
                        "error": str(e),
                        "crawled_at": datetime.now().isoformat(),
                    })
                else:
                    raise

            # Rate limiting
            if i < len(urls) - 1:
                time.sleep(delay)

        return results

    def close(self) -> None:
        """리소스 정리"""
        if self._ydl is not None:
            try:
                self._ydl.close()
            except Exception:
                pass
            self._ydl = None
        if self._ydl_comments is not None:
            try:
                self._ydl_comments.close()
            except Exception:
                pass
            self._ydl_comments = None
        logger.info("YouTubeCrawler 종료")

    def __enter__(self):
        """Context manager 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager 종료"""
        self.close()


# === 편의 함수 ===

def crawl_youtube_video(url: str) -> Dict[str, Any]:
    """
    YouTube 동영상 데이터 크롤링 (단일 함수)

    Args:
        url: YouTube 동영상 URL

    Returns:
        {
            "platform": "youtube",
            "url": str,
            "author": str,
            "title": str,
            "likes": int,
            "comments": int,
            "views": int,
            "subscribers": int or None,
            "crawled_at": str
        }
    """
    with YouTubeCrawler() as crawler:
        return crawler.crawl_video(url)


def crawl_youtube_videos(urls: List[str], delay: float = 1.0) -> List[Dict[str, Any]]:
    """
    여러 YouTube 동영상 데이터 크롤링 (단일 함수)

    Args:
        urls: 동영상 URL 리스트
        delay: 요청 간 딜레이 (초)

    Returns:
        동영상 데이터 리스트
    """
    with YouTubeCrawler() as crawler:
        return crawler.crawl_videos(urls, delay=delay)


# === 테스트 코드 ===

if __name__ == "__main__":
    import json

    # 로깅 레벨 설정
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("YouTube 크롤러 테스트")
    print("=" * 60)

    # 테스트 URL
    test_url = input("테스트할 YouTube URL을 입력하세요 (Enter로 기본 테스트): ").strip()

    if not test_url:
        # 기본 테스트 URL (공개 동영상)
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        print(f"기본 테스트 URL 사용: {test_url}")

    try:
        result = crawl_youtube_video(test_url)
        print("\n크롤링 결과:")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except Exception as e:
        print(f"\n오류 발생: {e}")
