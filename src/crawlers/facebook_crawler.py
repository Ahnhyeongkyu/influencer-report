"""
Facebook 크롤러 모듈

facebook-scraper 라이브러리를 통한 데이터 크롤링
- Selenium 없이 서버리스 환경에서 동작
- API 기반으로 봇 탐지 우회
- 좋아요, 댓글, 공유 수 등 수집

Streamlit Cloud 호환:
- facebook-scraper 우선 사용
- Selenium fallback 지원 (로컬 환경용)
"""

import html as html_module
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

import platform
import requests


from src.utils.text_utils import decode_unicode_escapes

# facebook-scraper for API-based crawling
try:
    from facebook_scraper import get_posts, get_page_info
    HAS_FB_SCRAPER = True
except ImportError:
    HAS_FB_SCRAPER = False

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    StaleElementReferenceException,
)

# undetected_chromedriver 비활성화 (ChromeDriver 버전 충돌 문제)
# webdriver-manager 사용으로 대체
HAS_UNDETECTED = False
try:
    import undetected_chromedriver as uc
except ImportError:
    uc = None

# Streamlit Cloud 환경 감지
IS_CLOUD = platform.system() == "Linux" and os.path.exists("/etc/debian_version")

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


class FacebookCrawlerError(Exception):
    """Facebook 크롤러 기본 예외"""
    pass


class FacebookLoginError(FacebookCrawlerError):
    """로그인 관련 예외"""
    pass


class FacebookPostLoadError(FacebookCrawlerError):
    """게시물 로드 관련 예외"""
    pass


class FacebookRateLimitError(FacebookCrawlerError):
    """Rate Limit 관련 예외"""
    pass


class FacebookCrawler:
    """
    Facebook 크롤러 클래스

    facebook-scraper 라이브러리를 우선 사용 (Selenium 없이 동작)
    Selenium은 로컬 환경 fallback으로 사용
    """

    # Facebook URL
    BASE_URL = "https://www.facebook.com"
    MOBILE_URL = "https://m.facebook.com"
    MBASIC_URL = "https://mbasic.facebook.com"  # 가장 가벼운 버전

    # 기본 설정
    DEFAULT_TIMEOUT = 30
    PAGE_LOAD_WAIT = 3
    LOGIN_TIMEOUT = 120

    # 쿠키 파일 경로
    COOKIE_DIR = Path(__file__).parent.parent.parent / "data" / "cookies"
    COOKIE_FILE = COOKIE_DIR / "facebook_cookies.json"

    # Rate Limiting
    MIN_REQUEST_DELAY = 2.0
    MAX_REQUEST_DELAY = 5.0

    # API 헤더
    API_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.facebook.com/',
    }

    def __init__(
        self,
        headless: bool = False,
        chrome_driver_path: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        cookie_file: Optional[str] = None,
        use_mobile: bool = True,
        use_scraper: bool = True,  # facebook-scraper 우선 사용
        use_api: bool = True,  # requests 기반 API 방식 사용
        collect_comments: bool = True,  # 댓글 내용 수집 여부
        max_comments: int = 10,  # 수집할 최대 댓글 수
    ):
        """
        크롤러 초기화

        Args:
            headless: 헤드리스 모드 (로그인 시에는 False 권장)
            chrome_driver_path: ChromeDriver 경로 (None이면 자동 탐색)
            timeout: 기본 타임아웃 (초)
            cookie_file: 쿠키 저장 파일 경로 (None이면 기본 경로 사용)
            use_mobile: 모바일 버전 사용 여부 (True 권장)
            use_scraper: facebook-scraper 라이브러리 사용 여부
            use_api: requests 기반 API 방식 사용 여부
            collect_comments: 댓글 내용 수집 여부
            max_comments: 수집할 최대 댓글 수
        """
        self.headless = headless
        self.chrome_driver_path = chrome_driver_path
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.use_mobile = use_mobile
        self.use_scraper = use_scraper and HAS_FB_SCRAPER
        self.use_api = use_api
        self.collect_comments = collect_comments
        self.max_comments = max_comments

        self.driver: Optional[webdriver.Chrome] = None
        self.is_logged_in = False
        self._last_request_time = 0
        self.session: Optional[requests.Session] = None

        # 쿠키 디렉토리 생성
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

        # requests 세션 초기화
        if use_api:
            self._init_session()

        logger.info(f"FacebookCrawler 초기화 완료 (use_api={use_api}, use_scraper={self.use_scraper}, collect_comments={collect_comments})")

    def _init_session(self) -> None:
        """requests 세션 초기화"""
        self.session = requests.Session()
        self.session.headers.update(self.API_HEADERS)

    def _crawl_via_api(self, url: str) -> Optional[Dict[str, Any]]:
        """
        requests를 통한 API 기반 크롤링

        Facebook 페이지의 HTML에서 메타데이터 추출

        Args:
            url: Facebook 게시물 URL

        Returns:
            게시물 데이터 또는 None
        """
        if not self.session:
            return None

        try:
            logger.info(f"requests API로 크롤링: {url}")

            # mbasic 버전으로 요청 (가장 가벼운 HTML)
            mbasic_url = url.replace('www.facebook.com', 'mbasic.facebook.com')
            mbasic_url = mbasic_url.replace('m.facebook.com', 'mbasic.facebook.com')

            response = self.session.get(mbasic_url, timeout=self.timeout)

            if response.status_code != 200:
                logger.warning(f"페이지 요청 실패: {response.status_code}")
                return None

            html = response.text

            # HTML에서 데이터 추출
            result = self._extract_data_from_html(html, url)
            return result

        except Exception as e:
            logger.warning(f"API 크롤링 실패: {e}")
            return None

    def _extract_data_from_html(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        HTML에서 게시물 데이터 추출

        Args:
            html: 페이지 HTML
            url: 원본 URL

        Returns:
            게시물 데이터 또는 None
        """
        result = {
            "platform": "facebook",
            "url": url,
            "author": None,
            "content": None,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
            "comments_list": [],  # 댓글 내용 리스트
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # HTML 엔티티 디코딩 (좋아요/댓글 수 regex 매칭 정확도 향상)
            html = html_module.unescape(html)

            # 좋아요 수 추출
            like_patterns = [
                r'(\d+(?:,\d+)*)\s*(?:likes?|좋아요)',
                r'aria-label="(\d+(?:,\d+)*)\s*(?:reactions?|반응)"',
                r'>(\d+(?:,\d+)*)</span>\s*(?:likes?|좋아요)',
            ]
            for pattern in like_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    result["likes"] = int(match.group(1).replace(',', ''))
                    break

            # 댓글 수 추출
            comment_patterns = [
                r'(\d+(?:,\d+)*)\s*(?:comments?|댓글)',
                r'>(\d+(?:,\d+)*)</span>\s*(?:comments?|댓글)',
            ]
            for pattern in comment_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    result["comments"] = int(match.group(1).replace(',', ''))
                    break

            # 공유 수 추출
            share_patterns = [
                r'(\d+(?:,\d+)*)\s*(?:shares?|공유)',
                r'>(\d+(?:,\d+)*)</span>\s*(?:shares?|공유)',
            ]
            for pattern in share_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    result["shares"] = int(match.group(1).replace(',', ''))
                    break

            # 작성자 추출
            author_patterns = [
                r'<strong[^>]*>([^<]+)</strong>',
                r'class="[^"]*author[^"]*"[^>]*>([^<]+)<',
            ]
            for pattern in author_patterns:
                match = re.search(pattern, html)
                if match:
                    author_candidate = match.group(1).strip()
                    # 유효한 작성자 이름인지 검증
                    if self._is_valid_author_name(author_candidate):
                        result["author"] = author_candidate
                        break

            # 페이스북 썸네일 비활성화 (정확한 게시물 이미지 추출 불가 - v1.5.5에서 개선 예정)

            # mbasic HTML에서 댓글 내용 추출
            try:
                comments_list = []
                # mbasic 댓글 패턴: <h3> 작성자 </h3> 다음에 댓글 텍스트
                # 또는 <a> 작성자 </a> 텍스트 구조
                comment_blocks = re.findall(
                    r'<div[^>]*id="[^"]*comment[^"]*"[^>]*>(.*?)</div>\s*</div>',
                    html, re.DOTALL | re.IGNORECASE
                )
                if not comment_blocks:
                    # 대안 패턴: mbasic 댓글 영역
                    comment_blocks = re.findall(
                        r'<div[^>]*>\s*<h3[^>]*><a[^>]*>([^<]+)</a></h3>\s*(.*?)\s*<div',
                        html, re.DOTALL | re.IGNORECASE
                    )
                    for author_match, text_match in comment_blocks[:10]:
                        text = re.sub(r'<[^>]+>', '', text_match).strip()
                        if text and len(text) > 1 and author_match:
                            if not any(c.get('text') == text for c in comments_list):
                                comments_list.append({
                                    'author': author_match.strip(),
                                    'text': text[:1000]
                                })
                else:
                    for block in comment_blocks[:10]:
                        author_m = re.search(r'<a[^>]*>([^<]+)</a>', block)
                        text_clean = re.sub(r'<[^>]+>', ' ', block).strip()
                        # 작성자 이름 제거
                        if author_m:
                            author = author_m.group(1).strip()
                            text_clean = text_clean.replace(author, '', 1).strip()
                        else:
                            author = None
                        # 불필요한 텍스트 제거
                        text_clean = re.sub(r'(좋아요|답글|Like|Reply|댓글|·|\d+시간|\d+분|\d+일)\s*', '', text_clean).strip()
                        if text_clean and len(text_clean) > 1:
                            if not any(c.get('text') == text_clean[:1000] for c in comments_list):
                                comments_list.append({
                                    'author': author,
                                    'text': text_clean[:1000]
                                })
                if comments_list:
                    result["comments_list"] = comments_list
                    logger.info(f"mbasic HTML에서 댓글 {len(comments_list)}개 추출")
            except Exception as e:
                logger.debug(f"mbasic 댓글 추출 실패: {e}")

            return result if result.get('likes', 0) > 0 or result.get('author') else None

        except Exception as e:
            logger.debug(f"HTML 데이터 추출 실패: {e}")
            return None

    def _extract_post_id_from_url(self, url: str) -> Optional[str]:
        """
        URL에서 게시물 ID 추출

        Args:
            url: Facebook 게시물 URL

        Returns:
            게시물 ID 또는 None
        """
        # 다양한 URL 패턴 처리
        patterns = [
            r'/posts/(\d+)',  # /posts/123456
            r'/posts/(pfbid\w+)',  # /posts/pfbid0abc123...
            r'/(\d+)/?$',  # 끝의 숫자
            r'fbid=(\d+)',  # fbid=123456
            r'story_fbid=(\d+)',  # story_fbid=123456
            r'/permalink/(\d+)',  # /permalink/123456
            r'/videos/(\d+)',  # /videos/123456
            r'/photos/[^/]+/(\d+)',  # /photos/xxx/123456
            r'[?&]v=(\d+)',  # /watch/?v=123456
            r'/reel/(\d+)',  # /reel/123456
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def _extract_page_name_from_url(self, url: str) -> Optional[str]:
        """
        URL에서 페이지명 추출

        Args:
            url: Facebook URL

        Returns:
            페이지명 또는 None
        """
        patterns = [
            r'facebook\.com/(\d+)/posts',  # 숫자 페이지 ID
            r'facebook\.com/([^/]+)/posts',  # 페이지명
            r'facebook\.com/([^/?]+)',  # 기본 페이지명
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                name = match.group(1)
                if name not in ['photo', 'video', 'watch', 'reel', 'share']:
                    return name

        return None

    def _crawl_via_scraper(self, url: str) -> Optional[Dict[str, Any]]:
        """
        facebook-scraper를 통한 게시물 크롤링

        Args:
            url: Facebook 게시물 URL

        Returns:
            게시물 데이터 또는 None
        """
        if not HAS_FB_SCRAPER:
            return None

        try:
            post_id = self._extract_post_id_from_url(url)
            page_name = self._extract_page_name_from_url(url)

            if not page_name:
                logger.warning("페이지명을 추출할 수 없습니다.")
                return None

            logger.info(f"facebook-scraper로 크롤링: page={page_name}, post_id={post_id}")

            # 쿠키 파일 경로 (저장된 쿠키 사용)
            cookie_file = str(self.cookie_file) if self.cookie_file.exists() else None
            if cookie_file:
                logger.info(f"facebook-scraper에 쿠키 적용: {cookie_file}")

            # get_posts로 특정 게시물 가져오기
            posts = get_posts(
                page_name,
                pages=1,
                cookies=cookie_file,  # 쿠키 파일 전달
                options={
                    "posts_per_page": 10,
                    "allow_extra_requests": True,
                    "comments": 20,  # 댓글 최대 20개 수집
                    "reactors": False,
                }
            )

            for post in posts:
                # post_id가 일치하거나 URL이 일치하는 게시물 찾기
                if post_id and str(post.get('post_id')) == str(post_id):
                    return self._format_scraper_result(post, url)

                # URL 비교
                post_url = post.get('post_url', '')
                if post_id and post_id in post_url:
                    return self._format_scraper_result(post, url)

            # 첫 번째 게시물 반환 (특정 게시물 못 찾은 경우)
            logger.warning("특정 게시물을 찾지 못해 최근 게시물 데이터 반환")
            return None

        except Exception as e:
            logger.warning(f"facebook-scraper 크롤링 실패: {e}")
            return None

    def _format_scraper_result(self, post: dict, url: str) -> Dict[str, Any]:
        """
        facebook-scraper 결과를 표준 포맷으로 변환

        Args:
            post: facebook-scraper의 게시물 데이터
            url: 원본 URL

        Returns:
            표준화된 게시물 데이터
        """
        # 댓글 목록 추출
        comments_list = []
        raw_comments = post.get('comments_full') or []
        for c in raw_comments[:20]:
            text = c.get('comment_text', '') if isinstance(c, dict) else str(c)
            if text:
                comments_list.append(text[:1000])

        # 댓글 수: comments 필드(HTML 추출 값) 우선, 없으면 comments_full 길이 fallback
        html_comment_count = post.get('comments', 0) or 0
        comment_count = html_comment_count if html_comment_count > 0 else len(raw_comments)

        return {
            "platform": "facebook",
            "url": url,
            "author": post.get('username') or post.get('user_id') or "Unknown",
            "content": (post.get('text') or "")[:5000],
            "likes": self._parse_count(post.get('likes') or post.get('reactions') or 0),
            "comments": comment_count,
            "shares": post.get('shares', 0) or 0,
            "views": post.get('video_views'),
            "comments_list": comments_list,
            "crawled_at": datetime.now().isoformat(),
        }

    def _create_driver(self) -> webdriver.Chrome:
        """
        Stealth 모드가 적용된 Chrome WebDriver 생성

        Returns:
            Chrome WebDriver 인스턴스
        """
        # 로컬 환경에서 undetected_chromedriver 사용 (Facebook 봇 탐지 우회에 효과적)
        if HAS_UNDETECTED and not IS_CLOUD:
            try:
                logger.info("undetected_chromedriver 사용")
                uc_options = uc.ChromeOptions()
                uc_options.add_argument("--no-sandbox")
                uc_options.add_argument("--disable-dev-shm-usage")
                if self.headless:
                    uc_options.add_argument("--headless=new")
                # Chrome 버전 자동 감지
                chrome_ver = None
                try:
                    import subprocess as _sp
                    _r = _sp.run(['reg', 'query', r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon', '/v', 'version'],
                               capture_output=True, text=True, timeout=5)
                    if _r.returncode == 0:
                        for _line in _r.stdout.split('\n'):
                            if 'version' in _line.lower():
                                chrome_ver = int(_line.strip().split()[-1].split('.')[0])
                except Exception:
                    pass
                driver = uc.Chrome(options=uc_options, use_subprocess=True, version_main=chrome_ver)
                logger.info(f"Stealth Chrome WebDriver 생성 완료 (undetected, ver={chrome_ver})")
                return driver
            except Exception as e:
                logger.warning(f"undetected_chromedriver 실패: {e}, webdriver-manager로 fallback")
                # webdriver-manager fallback
                try:
                    # ChromeDriverManager는 파일 상단에서 이미 import됨
                    options = Options()
                    options.add_argument("--no-sandbox")
                    options.add_argument("--disable-dev-shm-usage")
                    if self.headless:
                        options.add_argument("--headless=new")
                    service = Service(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=service, options=options)
                    logger.info("webdriver-manager로 Chrome 생성 완료")
                    return driver
                except Exception as e2:
                    logger.error(f"webdriver-manager도 실패: {e2}")

        # Cloud 환경 또는 undetected 없는 경우 기존 로직
        options = Options()

        # Cloud 환경이면 headless 강제
        if self.headless or IS_CLOUD:
            options.add_argument("--headless=new")

        # 기본 옵션
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=414,896")  # 모바일 사이즈

        # === Stealth 모드 설정 ===
        # 1. 자동화 탐지 비활성화
        options.add_argument("--disable-blink-features=AutomationControlled")

        # experimental_option은 로컬에서만 사용 (Cloud에서 호환성 문제)
        if not IS_CLOUD:
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

        # 2. WebRTC IP 노출 방지
        options.add_argument("--disable-webrtc")

        # 3. 플러그인/확장 관련
        options.add_argument("--disable-plugins-discovery")
        options.add_argument("--disable-extensions")

        # 4. 모바일 User-Agent 설정 (봇 탐지 우회에 효과적)
        if self.use_mobile:
            mobile_ua = (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            )
            options.add_argument(f"user-agent={mobile_ua}")
        else:
            desktop_ua = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            options.add_argument(f"user-agent={desktop_ua}")

        # 5. 언어 설정
        options.add_argument("--lang=ko-KR")
        options.add_argument("--accept-language=ko-KR,ko;q=0.9,en;q=0.8")

        # 6. 추가 stealth 옵션
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-popup-blocking")

        try:
            if self.chrome_driver_path:
                service = Service(executable_path=self.chrome_driver_path)
            elif IS_CLOUD:
                # Streamlit Cloud: Chromium 사용
                service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
            else:
                # 로컬: 일반 Chrome 사용
                service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)

            # Stealth JavaScript 삽입
            self._inject_stealth_scripts(driver)

            logger.info("Stealth Chrome WebDriver 생성 완료")
            return driver

        except WebDriverException as e:
            logger.error(f"WebDriver 생성 실패: {e}")
            raise FacebookCrawlerError(f"Chrome WebDriver를 생성할 수 없습니다: {e}")

    def _inject_stealth_scripts(self, driver: webdriver.Chrome) -> None:
        """
        봇 탐지 우회를 위한 JavaScript 삽입

        Args:
            driver: Chrome WebDriver 인스턴스
        """
        stealth_js = """
            // navigator.webdriver 속성 숨기기
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Chrome 속성 위장
            window.chrome = {
                runtime: {}
            };

            // permissions.query 위장
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // plugins 배열 위장
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // languages 위장
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ko-KR', 'ko', 'en-US', 'en']
            });

            // platform 위장
            Object.defineProperty(navigator, 'platform', {
                get: () => 'iPhone'
            });

            // hardwareConcurrency 위장
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 4
            });
        """

        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": stealth_js}
            )
        except Exception as e:
            logger.warning(f"Stealth 스크립트 삽입 실패: {e}")

    def _rate_limit(self) -> None:
        """Rate Limiting 적용"""
        import random

        elapsed = time.time() - self._last_request_time
        delay = random.uniform(self.MIN_REQUEST_DELAY, self.MAX_REQUEST_DELAY)

        if elapsed < delay:
            sleep_time = delay - elapsed
            logger.debug(f"Rate limiting: {sleep_time:.2f}초 대기")
            time.sleep(sleep_time)

        self._last_request_time = time.time()

    def _save_cookies(self) -> None:
        """현재 세션 쿠키를 파일에 저장"""
        if not self.driver:
            return

        try:
            cookies = self.driver.get_cookies()
            with open(self.cookie_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            logger.info(f"쿠키 저장 완료: {self.cookie_file}")
        except Exception as e:
            logger.warning(f"쿠키 저장 실패: {e}")

    def _load_cookies(self) -> bool:
        """
        저장된 쿠키 로드

        Returns:
            쿠키 로드 성공 여부
        """
        if not self.cookie_file.exists():
            logger.info("저장된 쿠키 파일 없음")
            return False

        try:
            base_url = self.MOBILE_URL if self.use_mobile else self.BASE_URL
            self.driver.get(base_url)
            time.sleep(2)

            with open(self.cookie_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)

            for cookie in cookies:
                if "expiry" in cookie:
                    cookie["expiry"] = int(cookie["expiry"])
                if "sameSite" in cookie and cookie["sameSite"] not in ["Strict", "Lax", "None"]:
                    del cookie["sameSite"]

                try:
                    self.driver.add_cookie(cookie)
                except Exception as e:
                    logger.debug(f"쿠키 추가 실패 (무시): {e}")

            logger.info("쿠키 로드 완료")
            return True

        except Exception as e:
            logger.warning(f"쿠키 로드 실패: {e}")
            return False

    def _check_login_status(self) -> bool:
        """
        로그인 상태 확인

        Returns:
            로그인 여부
        """
        try:
            base_url = self.MOBILE_URL if self.use_mobile else self.BASE_URL
            self.driver.get(base_url)
            time.sleep(self.PAGE_LOAD_WAIT)

            # 로그인 폼이 있는지 확인
            try:
                login_form = self.driver.find_element(
                    By.XPATH,
                    "//input[@name='email'] | //input[@id='m_login_email']"
                )
                # 로그인 폼이 있으면 미로그인 상태
                return False
            except NoSuchElementException:
                pass

            # 로그인 관련 쿠키 확인
            cookies = self.driver.get_cookies()
            login_cookies = ["c_user", "xs", "datr"]
            found_cookies = 0
            for cookie in cookies:
                if cookie.get("name") in login_cookies and cookie.get("value"):
                    found_cookies += 1

            if found_cookies >= 2:
                logger.info("로그인 상태 확인됨")
                return True

            return False

        except Exception as e:
            logger.warning(f"로그인 상태 확인 중 오류: {e}")
            return False

    def login(self, email: str = None, password: str = None, force_login: bool = False) -> bool:
        """
        Facebook 로그인

        저장된 쿠키가 있으면 재사용, 없으면 수동 로그인 필요

        Args:
            email: 로그인 이메일 (선택)
            password: 로그인 비밀번호 (선택)
            force_login: True면 쿠키 무시하고 새로 로그인

        Returns:
            로그인 성공 여부
        """
        if self.driver is None:
            self.driver = self._create_driver()

        # 저장된 쿠키로 로그인 시도
        if not force_login and self.cookie_file.exists():
            logger.info("저장된 쿠키로 로그인 시도...")
            if self._load_cookies():
                if self._check_login_status():
                    self.is_logged_in = True
                    logger.info("쿠키를 사용한 로그인 성공!")
                    return True
                else:
                    logger.info("저장된 쿠키가 만료됨.")

        # 자동 로그인 시도 (이메일/비밀번호 제공된 경우)
        if email and password:
            return self._auto_login(email, password)

        # 수동 로그인 대기
        return self._wait_for_manual_login()

    def _auto_login(self, email: str, password: str) -> bool:
        """
        자동 로그인 시도

        Args:
            email: 이메일
            password: 비밀번호

        Returns:
            로그인 성공 여부
        """
        try:
            login_url = f"{self.MOBILE_URL}/login" if self.use_mobile else f"{self.BASE_URL}/login"
            self.driver.get(login_url)
            time.sleep(self.PAGE_LOAD_WAIT)

            # 이메일 입력
            email_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//input[@name='email' or @id='m_login_email' or @id='email']"
                ))
            )
            email_input.clear()
            email_input.send_keys(email)

            # 비밀번호 입력
            password_input = self.driver.find_element(
                By.XPATH,
                "//input[@name='pass' or @id='m_login_password' or @id='pass']"
            )
            password_input.clear()
            password_input.send_keys(password)

            # 로그인 버튼 클릭
            login_btn = self.driver.find_element(
                By.XPATH,
                "//button[@name='login'] | //input[@name='login'] | //button[contains(text(), '로그인')] | //input[@value='로그인']"
            )
            login_btn.click()

            time.sleep(5)

            # 로그인 확인
            if self._check_login_status():
                self._save_cookies()
                self.is_logged_in = True
                logger.info("자동 로그인 성공!")
                return True
            else:
                logger.warning("자동 로그인 실패 - 2단계 인증이나 보안 확인 필요할 수 있음")
                return False

        except Exception as e:
            logger.error(f"자동 로그인 중 오류: {e}")
            return False

    def _wait_for_manual_login(self) -> bool:
        """
        수동 로그인 대기

        Returns:
            로그인 성공 여부
        """
        logger.info("=" * 50)
        logger.info("수동 로그인이 필요합니다!")
        logger.info("브라우저 창에서 Facebook에 로그인해주세요.")
        logger.info(f"대기 시간: {self.LOGIN_TIMEOUT}초")
        logger.info("=" * 50)

        login_url = f"{self.MOBILE_URL}/login" if self.use_mobile else f"{self.BASE_URL}/login"
        self.driver.get(login_url)

        start_time = time.time()

        while time.time() - start_time < self.LOGIN_TIMEOUT:
            if self._check_login_status():
                self._save_cookies()
                self.is_logged_in = True
                logger.info("수동 로그인 성공!")
                return True

            remaining = int(self.LOGIN_TIMEOUT - (time.time() - start_time))
            if remaining % 15 == 0:
                logger.info(f"로그인 대기 중... 남은 시간: {remaining}초")

            time.sleep(2)

        logger.error("로그인 시간 초과")
        return False

    def _convert_to_mobile_url(self, url: str) -> str:
        """
        URL을 모바일 버전으로 변환

        Args:
            url: 원본 URL

        Returns:
            모바일 URL
        """
        if not self.use_mobile:
            return url

        # m.facebook.com 모바일 버전 사용
        url = url.replace("www.facebook.com", "m.facebook.com")
        url = url.replace("web.facebook.com", "m.facebook.com")
        url = url.replace("mbasic.facebook.com", "m.facebook.com")

        return url

    def _parse_count(self, text: str) -> int:
        """
        숫자 텍스트 파싱 (예: "1.2K" -> 1200, "5천" -> 5000)

        Args:
            text: 숫자 텍스트

        Returns:
            정수 값
        """
        if not text:
            return 0

        # 이미 숫자 타입이면 바로 반환
        if isinstance(text, (int, float)):
            return int(text)

        text = str(text).strip()
        text_lower = text.lower()

        # 숫자 추출 헬퍼 함수 (점만 있는 경우 제외)
        def extract_number(pattern, text):
            match = re.search(pattern, text)
            if match:
                val = match.group()
                # 숫자가 포함되어 있는지 확인 (점만 있는 경우 제외)
                if re.search(r'\d', val):
                    try:
                        return float(val)
                    except ValueError:
                        pass
            return None

        # K/천 단위 (먼저 체크)
        if "k" in text_lower or "천" in text:
            num = extract_number(r"[\d.]+", text)
            if num is not None:
                return int(num * 1000)

        # M 단위 (million) - 영어권
        if "m" in text_lower and "만" not in text:
            num = extract_number(r"[\d.]+", text)
            if num is not None:
                return int(num * 1000000)

        # 만 단위 (한국어)
        if "만" in text:
            num = extract_number(r"[\d.]+", text)
            if num is not None:
                return int(num * 10000)

        # B/억 단위
        if "b" in text_lower or "억" in text:
            num = extract_number(r"[\d.]+", text)
            if num is not None:
                return int(num * 100000000)

        # 숫자만 있는 경우 (접미사 처리 후)
        clean_text = re.sub(r"[^\d.]", "", text.replace(",", ""))
        if clean_text and re.search(r'\d', clean_text):
            try:
                # 소수점이 있으면 float으로 변환 후 int
                return int(float(clean_text))
            except ValueError:
                pass

        # 그 외 숫자 추출
        num = re.search(r"[\d,]+", text.replace(",", ""))
        if num:
            return int(num.group().replace(",", ""))

        return 0

    def _is_time_text(self, text: str) -> bool:
        """시간/날짜 관련 텍스트인지 판별"""
        if not text:
            return False
        return bool(re.search(
            r'\d+\s*(시간|분|일|초|주|개월|년)(\s*전)?|'
            r'\d+\s*(hour|min|day|sec|week|month|year)s?(\s*ago)?|'
            r'(어제|오늘|그저께|방금|yesterday|today|just\s*now)',
            text, re.IGNORECASE
        ))

    def _extract_reactions(self, driver: webdriver.Chrome) -> int:
        """
        반응(좋아요 등) 수 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            반응 수
        """
        # 1단계: aria-label에서 반응 수 추출 (가장 정확)
        # Facebook은 aria-label="좋아요 839명", "839 people reacted" 등으로 표시
        try:
            aria_selectors = [
                "//*[contains(@aria-label, '좋아요') and @role='button']",
                "//*[contains(@aria-label, '명') and (contains(@aria-label, '좋아요') or contains(@aria-label, '반응'))]",
                "//*[contains(@aria-label, 'like') and contains(@aria-label, 'people')]",
                "//*[contains(@aria-label, 'reaction') and @role]",
            ]
            for selector in aria_selectors:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    for elem in elements:
                        aria = elem.get_attribute('aria-label') or ''
                        if self._is_time_text(aria):
                            continue
                        count = self._parse_count(aria)
                        if count > 0:
                            logger.info(f"aria-label에서 반응 수 추출: {count} (label: {aria[:60]})")
                            return count
                except (NoSuchElementException, StaleElementReferenceException):
                    continue
        except Exception:
            pass

        # 2단계: XPath 기반 텍스트 추출 (시간 텍스트 필터링 적용)
        reaction_selectors = [
            # 모바일 버전
            "//div[contains(@data-sigil, 'reactions-sentence')]",
            "//span[contains(@data-sigil, 'reaction')]",
            "//a[contains(@href, 'reaction')]//span",
            # 데스크톱 버전
            "//span[contains(@class, 'reactions')]//span",
            "//div[contains(@aria-label, '좋아요')]",
            "//div[contains(@aria-label, 'like')]",
            # 일반적인 패턴
            "//span[contains(text(), '좋아요') or contains(text(), 'Like')]",
        ]

        for selector in reaction_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    text = elem.text.strip()
                    if text and not self._is_time_text(text):
                        count = self._parse_count(text)
                        if count > 0:
                            return count
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        return 0

    def _extract_comments_count(self, driver: webdriver.Chrome, scoped_source: str = None) -> int:
        """
        댓글 수 추출

        Args:
            driver: WebDriver 인스턴스
            scoped_source: 스코핑된 페이지 소스 (None이면 driver.page_source 사용)

        Returns:
            댓글 수
        """
        # 먼저 페이지 소스에서 정규식으로 추출 시도 (텍스트 패턴만 - JSON 패턴은 _try_javascript_extraction에서 스코핑 처리)
        try:
            page_source = scoped_source or driver.page_source
            # 텍스트 기반 댓글 수 패턴 (DOM에 렌더링된 텍스트 - 타겟 포스트에 해당할 가능성 높음)
            text_comment_patterns = [
                r'(\d+)\s*(?:개의\s*)?댓글',  # "23개의 댓글", "23 댓글"
                r'댓글\s*(\d+)',  # "댓글 23"
                r'(\d+)\s*[Cc]omments?',  # "23 Comments", "23 comments"
                r'[Cc]omments?\s*(\d+)',  # "Comments 23"
                r'댓글\s*\((\d+)\)',  # "댓글(23)"
                r'\((\d+)\)\s*댓글',  # "(23) 댓글"
            ]
            for pattern in text_comment_patterns:
                match = re.search(pattern, page_source)
                if match:
                    count = int(match.group(1))
                    if count > 0 and count < 1000000:
                        logger.info(f"정규식으로 댓글 수 추출: {count}")
                        return count
        except Exception as e:
            logger.debug(f"정규식 댓글 추출 실패: {e}")

        # XPath 셀렉터 fallback
        comment_selectors = [
            # 모바일 버전 (m.facebook.com)
            "//a[contains(@href, 'comment')]",
            "//div[contains(@data-sigil, 'comment')]",
            "//span[contains(@class, 'comment')]",
            # 데스크톱 버전
            "//span[contains(text(), '댓글') or contains(text(), 'comment')]",
            "//a[contains(text(), '댓글')]",
            "//div[contains(@aria-label, '댓글')]",
            # 추가 셀렉터
            "//*[contains(text(), '개의 댓글')]",
            "//*[contains(text(), 'Comments')]",
        ]

        for selector in comment_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    text = elem.text.strip()
                    if text:
                        # 시간/날짜 텍스트 필터링 (예: "3주", "5시간 전" 등)
                        if self._is_time_text(text):
                            logger.debug(f"XPath 댓글 추출: 시간 텍스트 건너뜀 ({text[:30]})")
                            continue
                        count = self._parse_count(text)
                        if count > 0:
                            logger.info(f"XPath로 댓글 수 추출: {count} (from: {text[:30]})")
                            return count
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        # aria-label fallback: 개별 댓글 aria-label 카운팅 ("N전에 X님이 남긴 댓글")
        try:
            comment_labels = driver.find_elements(
                By.XPATH, "//*[contains(@aria-label, '님이 남긴 댓글') or contains(@aria-label, 'comment by')]"
            )
            if comment_labels:
                count = len(comment_labels)
                logger.info(f"aria-label에서 댓글 수 추정: {count}개 (보이는 댓글 기준)")
                return count
        except Exception:
            pass

        return 0

    def _extract_comment_list(self, driver: webdriver.Chrome, max_comments: int = 10) -> list:
        """
        댓글 내용 추출

        Args:
            driver: WebDriver 인스턴스
            max_comments: 최대 수집할 댓글 수

        Returns:
            댓글 리스트 [{"author": "작성자", "text": "내용"}, ...]
        """
        comments = []

        try:
            # 1. 페이지 소스에서 JSON으로 댓글 추출 시도
            page_source = driver.page_source

            # Facebook JSON 패턴: "body":{"text":"댓글내용"}
            comment_pattern = r'"body"\s*:\s*\{\s*"text"\s*:\s*"([^"]+)"'
            author_pattern = r'"name"\s*:\s*"([^"]+)"'

            # 댓글 본문 추출
            comment_texts = re.findall(comment_pattern, page_source)

            if comment_texts:
                for i, text in enumerate(comment_texts[:max_comments]):
                    decoded_text = decode_unicode_escapes(text)
                    comments.append({
                        "author": f"사용자{i+1}",  # JSON에서 정확한 매칭 어려움
                        "text": decoded_text
                    })
                logger.info(f"JSON에서 댓글 {len(comments)}개 추출")
                return comments

            # 2. DOM에서 댓글 요소 직접 추출
            comment_selectors = [
                "//div[contains(@class, 'comment')]//div[contains(@dir, 'auto')]",
                "//div[@data-sigil='comment-body']",
                "//div[contains(@class, '_2b05')]",  # 모바일 댓글
                "//span[contains(@class, '_3l3x')]",  # 데스크톱 댓글
            ]

            for selector in comment_selectors:
                try:
                    elements = driver.find_elements(By.XPATH, selector)
                    for elem in elements[:max_comments]:
                        text = elem.text.strip()
                        if text and len(text) > 1:
                            comments.append({
                                "author": "Facebook 사용자",
                                "text": text
                            })
                    if comments:
                        logger.info(f"DOM에서 댓글 {len(comments)}개 추출")
                        return comments
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"댓글 내용 추출 실패: {e}")

        return comments

    def _extract_shares_count(self, driver: webdriver.Chrome) -> int:
        """
        공유 수 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            공유 수
        """
        share_selectors = [
            # 모바일 버전
            "//a[contains(@href, 'share')]//span",
            "//div[contains(@data-sigil, 'share')]",
            # 데스크톱 버전
            "//span[contains(text(), '공유') or contains(text(), 'share')]",
            "//a[contains(text(), '공유')]",
            "//div[contains(@aria-label, '공유')]",
        ]

        for selector in share_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    text = elem.text.strip()
                    if text and ("공유" in text or "share" in text.lower()):
                        count = self._parse_count(text)
                        if count > 0:
                            return count
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        return 0

    def _extract_views_count(self, driver: webdriver.Chrome) -> Optional[int]:
        """
        조회수 추출 (동영상인 경우)

        Args:
            driver: WebDriver 인스턴스

        Returns:
            조회수 또는 None
        """
        view_selectors = [
            # 동영상 조회수
            "//span[contains(text(), '조회') or contains(text(), 'view')]",
            "//div[contains(@aria-label, '조회')]",
            "//span[contains(@class, 'view')]",
            # Reels
            "//span[contains(text(), '재생')]",
        ]

        for selector in view_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    text = elem.text.strip()
                    if text and ("조회" in text or "view" in text.lower() or "재생" in text):
                        count = self._parse_count(text)
                        if count > 0:
                            return count
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        return None

    def _extract_author(self, driver: webdriver.Chrome) -> Optional[str]:
        """
        작성자/페이지명 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            작성자 이름 또는 None
        """
        # 0. URL에서 페이지명 추출 시도 (가장 신뢰할 수 있음)
        url_author = None  # fallback용 변수 초기화
        try:
            current_url = driver.current_url
            excluded_slugs = ['profile.php', 'watch', 'groups', 'events', 'marketplace',
                            'gaming', 'photo.php', 'video.php', 'story.php', 'permalink.php',
                            'share', 'reel', 'login', 'checkpoint']
            # 패턴 1: facebook.com/페이지명/posts|videos|photos|watch|reels/...
            url_page_match = re.search(r'facebook\.com/([^/?]+)/(posts|videos|photos|watch|reels?)', current_url)
            if url_page_match:
                page_slug = url_page_match.group(1)
                if page_slug not in excluded_slugs and not page_slug.isdigit():
                    logger.info(f"URL에서 페이지명 추출 성공: {page_slug}")
                    url_author = page_slug
                    return page_slug
            # 패턴 2: facebook.com/페이지명 (단순 페이지 URL)
            if not url_author:
                simple_match = re.search(r'facebook\.com/([^/?]+)/?$', current_url)
                if simple_match:
                    page_slug = simple_match.group(1)
                    if page_slug not in excluded_slugs and not page_slug.isdigit():
                        url_author = page_slug  # fallback으로 저장 (바로 반환하지 않음)
        except Exception as e:
            url_author = None
            logger.debug(f"URL 페이지명 추출 실패: {e}")

        # 쿠키 없는 requests 세션으로 작성자 추출 시도 (경량 방식 - v1.5.8)
        try:
            import requests as req_lib
            current_url = driver.current_url
            # 데스크톱 URL로 변환
            desktop_url = current_url.replace("m.facebook.com", "www.facebook.com")
            resp = req_lib.get(desktop_url, timeout=10, allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
            if resp.status_code == 200:
                temp_src = resp.text
                # owner 패턴으로 작성자 추출
                owner_match = re.search(r'"owner"[^}]*"name"\s*:\s*"([^"]{2,50})"', temp_src)
                if owner_match:
                    author = decode_unicode_escapes(owner_match.group(1))
                    if self._is_valid_author_name(author):
                        logger.info(f"requests 세션에서 작성자 추출: {author}")
                        return author
        except Exception as e:
            logger.debug(f"requests 세션 작성자 추출 실패: {e}")

        page_source = driver.page_source

        # 0. Meta 태그에서 추출 시도 (가장 안정적)
        try:
            meta_patterns = [
                # og:title에서 페이지명 추출 "영상제목 | 페이지명"
                r'<meta\s+property="og:title"\s+content="[^|]+\|\s*([^"]+)"',
                r'<meta\s+content="[^|]+\|\s*([^"]+)"\s+property="og:title"',
                # og:site_name
                r'<meta\s+property="og:site_name"\s+content="([^"]+)"',
                r'<meta\s+content="([^"]+)"\s+property="og:site_name"',
            ]
            for pattern in meta_patterns:
                match = re.search(pattern, page_source)
                if match:
                    author = match.group(1).strip()
                    # Facebook 자체는 제외
                    if author and author.lower() != 'facebook' and self._is_valid_author_name(author):
                        logger.info(f"Meta 태그에서 작성자 추출: {author}")
                        return author
        except Exception as e:
            logger.debug(f"Meta 작성자 추출 실패: {e}")

        # 0.5. Title 태그에서 추출 (Watch 영상: "제목 | 페이지명 | Facebook")
        try:
            title_match = re.search(r'<title>([^<]+)</title>', page_source)
            if title_match:
                title = title_match.group(1)
                # 알림 숫자 제거: "(20+) ", "(99+) " 등
                title = re.sub(r'^\(\d+\+?\)\s*', '', title)

                # 패턴 1: "영상제목 | 페이지명 | Facebook" 또는 "영상제목 | 페이지명"
                if '|' in title:
                    parts = [p.strip() for p in title.split('|')]
                    if len(parts) >= 2:
                        # 마지막이 Facebook이면 그 전 것이 페이지명
                        if parts[-1].lower() == 'facebook' and len(parts) >= 3:
                            author = parts[-2]
                        elif parts[-1].lower() != 'facebook':
                            author = parts[-1]
                        else:
                            author = None
                        if author and author.lower() != 'facebook' and self._is_valid_author_name(author):
                            logger.info(f"Title 태그에서 작성자 추출 (|): {author}")
                            return author

                # 패턴 2: "(20+) 게시물 내용 - 작성자명" (일반 포스트)
                if ' - ' in title:
                    parts = title.rsplit(' - ', 1)  # 마지막 " - "로 분리
                    if len(parts) == 2:
                        author = parts[1].strip()
                        # "| Facebook" 제거
                        if '|' in author:
                            author = author.split('|')[0].strip()
                        if author and author.lower() != 'facebook' and self._is_valid_author_name(author):
                            logger.info(f"Title 태그에서 작성자 추출 (-): {author}")
                            return author
        except Exception as e:
            logger.debug(f"Title 작성자 추출 실패: {e}")

        # 1. JSON 데이터에서 작성자 추출 시도
        try:
            # 로그인한 사용자 이름 가져오기 (필터링용)
            logged_in_user = None
            user_match = re.search(r'"viewer"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', page_source)
            if user_match:
                logged_in_user = user_match.group(1)
                logger.debug(f"로그인 사용자 감지: {logged_in_user}")

            author_patterns = [
                # Watch 동영상 소유자 패턴 (가장 정확) - 2026 구조
                r'"owner"[^}]*"name"\s*:\s*"([가-힣A-Za-z][가-힣A-Za-z ]{1,29})"',
                r'"video_owner_profile"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                r'"video_owner_profile":\{"__typename":"(?:User|Page)"[^}]*"name":"([^"]+)"',
                r'"video_owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                r'"video"\s*:\s*\{[^}]*"owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                # 페이지 이름
                r'"page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                r'"owning_page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                # 채널/퍼블리셔
                r'"channelName"\s*:\s*"([^"]+)"',
                r'"channel"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                r'"publisher"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                # owner 패턴
                r'"owner"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
                r'"owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                # __typename이 Page/User인 경우 (2026 최신 구조 - 중첩 객체 허용)
                r'"__typename"\s*:\s*"Page".{0,500}"name"\s*:\s*"([^"]+)"',
                r'"__typename"\s*:\s*"User".{0,500}"name"\s*:\s*"([^"]+)"',
                # actor/poster
                r'"actor"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                r'"poster"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
            ]
            for pattern in author_patterns:
                match = re.search(pattern, page_source)
                if match:
                    author = match.group(1)
                    # 유효한 이름인지 확인 + 로그인 사용자 제외
                    if author and self._is_valid_author_name(author):
                        # 로그인한 사용자 이름과 같으면 건너뛰기
                        if logged_in_user and author == logged_in_user:
                            logger.debug(f"로그인 사용자 이름 제외: {author}")
                            continue
                        logger.info(f"JSON에서 작성자 추출: {author}")
                        return author
        except Exception as e:
            logger.debug(f"JSON 작성자 추출 실패: {e}")

        # 2. DOM에서 작성자 추출 (fallback)
        author_selectors = [
            # Facebook Watch 페이지 전용
            "//a[contains(@href, '/watch/') or contains(@href, '/page/')]/span[string-length(text()) > 1]",
            "//div[contains(@class, 'video')]//a[contains(@href, '/')]/span",
            "//div[contains(@data-pagelet, 'WatchPermalinkVideo')]//a/span",
            # 2025-2026 최신 페이스북 구조
            "//div[@role='article']//span[contains(@class, 'x1lliihq')]//a/strong",
            "//div[@role='article']//a[contains(@href, '/profile.php') or contains(@href, '.com/')]/span",
            "//div[@role='article']//h2//a/span",
            "//div[@role='article']//h3//a",
            # 모바일 버전
            "//header//strong//a",
            "//h3//a[@role='link']",
            "//div[contains(@data-sigil, 'actor')]//a",
            "//div[@class='story_body_container']//header//a",
            # 데스크톱 버전
            "//strong//a[contains(@href, '/')]",
            "//span[contains(@class, 'author')]",
            "//a[contains(@class, 'profileLink')]",
            # 일반 패턴
            "//h2//a",
            "//div[@role='article']//strong//a",
            "//a[@data-hovercard]",
        ]

        for selector in author_selectors:
            try:
                elem = driver.find_element(By.XPATH, selector)
                text = elem.text.strip()
                if text and self._is_valid_author_name(text):
                    return text
            except NoSuchElementException:
                continue

        # JavaScript DOM 기반 추출 (프로필 링크에서 작성자명 추출)
        try:
            js_code = """
            var results = [];
            // 1. 비디오 영역 근처의 h2, strong 내 링크 (작성자명 가능성 높음)
            document.querySelectorAll('h2 a, strong a, [data-pagelet] a[role="link"]').forEach(function(a) {
                var text = a.innerText.trim();
                var href = a.href || '';
                // 네비게이션 URL 제외
                if (href.includes('/watch?') || href.includes('/watch/') ||
                    href.includes('/groups') || href.includes('/events') ||
                    href.includes('/marketplace') || href.includes('/gaming')) return;
                if (text && text.length > 1 && text.length < 50 && !text.startsWith('#')) {
                    results.push(text);
                }
            });
            // 2. 명시적 프로필 링크
            document.querySelectorAll('a[href*="profile.php"], a[href*="user.php"]').forEach(function(a) {
                var text = a.innerText.trim();
                if (text && text.length > 1 && text.length < 50) results.push(text);
            });
            return results.slice(0, 20);
            """
            link_texts = driver.execute_script(js_code)

            if link_texts:
                # 필터링: 해시태그, UI 텍스트, 날짜/시간 제외
                ui_texts = ['좋아요', 'Like', 'Comment', 'Share', '공유', '댓글', '팔로우', 'Follow',
                           '더 보기', 'See more', '설정', 'Settings', '메뉴', 'Menu', '검색', 'Search',
                           '홈', 'Home', 'mome', '동영상', 'Video', 'Watch', 'Reels', '스토리', 'Stories',
                           '알림', 'Notifications', '친구', 'Friends', '그룹', 'Groups', '페이지', 'Pages',
                           'Sponsored', '광고', 'Facebook', 'Log In', '로그인', 'Sign Up', '가입',
                           '둘러보기', 'Browse', 'Explore', '마켓플레이스', 'Marketplace', '이벤트', 'Events',
                           '게임', 'Gaming', '저장됨', 'Saved', '더보기', 'See More', '접기', 'See Less',
                           '저장된 동영상', 'Saved videos', '라이브', 'Live', '추천', 'Suggested',
                           '피드', 'Feed', '프로필', 'Profile', '내 프로필', 'My Profile',
                           '인기 동영상', '인기 동영상 찾아보기', 'Popular videos', 'Trending',
                           'Messenger', '메신저', 'Create', '만들기', '새 게시물', 'New post']

                for text in link_texts:
                    # 해시태그 제외
                    if text.startswith('#'):
                        continue
                    # UI 텍스트 제외
                    if text.lower() in [u.lower() for u in ui_texts]:
                        continue
                    # 숫자만 있는 텍스트 제외
                    if text.replace(',', '').replace('.', '').replace(' ', '').isdigit():
                        continue
                    # 날짜/시간 패턴 제외
                    if self._is_date_time_text(text):
                        continue
                    # 유효한 작성자명인지 확인
                    if self._is_valid_author_name(text):
                        logger.info(f"JavaScript DOM에서 작성자 추출: {text}")
                        return text
        except Exception as e:
            logger.debug(f"JavaScript DOM 작성자 추출 실패: {e}")

        # 모든 방법 실패 시 URL에서 추출한 페이지 slug 반환
        if url_author:
            logger.info(f"Fallback: URL 페이지명 사용: {url_author}")
            return url_author

        return None

    def _is_date_time_text(self, text: str) -> bool:
        """
        텍스트가 날짜/시간 형식인지 확인

        Args:
            text: 확인할 텍스트

        Returns:
            날짜/시간 형식이면 True
        """
        if not text:
            return False

        # 날짜/시간 패턴들
        date_time_patterns = [
            # 한국어 날짜 (2025년 12월 26일, 2025년를 12개월 등 깨진 형태 포함)
            r'^\d{4}년',
            r'^\d+월\s*\d+일',
            r'^\d+개월',
            r'년\d+월',
            # 시간 형식
            r'^오전\s*\d',
            r'^오후\s*\d',
            r'^\d{1,2}:\d{2}',
            r'^\d{1,2}\s*(am|pm|AM|PM)',
            # 상대 시간
            r'^\d+분\s*전',
            r'^\d+시간\s*전',
            r'^\d+일\s*전',
            r'^어제',
            r'^오늘',
            r'^방금',
            # 영어 날짜/시간
            r'^yesterday',
            r'^today',
            r'^\d+\s*(min|hour|day|week|month|year)s?\s*(ago)?',
            # 깨진 인코딩 패턴 (UTF-8 -> Latin-1)
            r'^[\d\s년월일¼½¾\/:.\-]+$',  # 숫자와 날짜 문자만
            r'¼|½|¾',  # 분수 문자가 있으면 깨진 것
        ]

        for pattern in date_time_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True

        # 숫자, 콜론, 슬래시, 날짜 관련 문자만으로 이루어진 경우
        if re.match(r'^[\d\s년월일시분초:\/.¼½¾\-]+$', text):
            return True

        return False

    def _is_valid_author_name(self, name: str) -> bool:
        """
        유효한 작성자 이름인지 확인
        시간 정보, 날짜, 특수문자만 있는 경우 등을 필터링

        Args:
            name: 추출된 이름 문자열

        Returns:
            유효한 작성자 이름이면 True
        """
        if not name or len(name) < 2:
            return False

        # URL이면 제외
        if name.startswith('http'):
            return False

        # JSON 형식이면 제외
        if name.startswith('{') or name.startswith('['):
            return False

        # 시간 패턴 필터링 (한국어)
        time_patterns_ko = [
            r'^어제',          # 어제 오전 5:02
            r'^오늘',          # 오늘 오후 3:00
            r'^그저께',
            r'^방금',
            r'^\d+분\s*전',    # 5분 전
            r'^\d+시간\s*전',  # 2시간 전
            r'^\d+일\s*전',    # 3일 전
            r'^오전\s*\d',     # 오전 5:02
            r'^오후\s*\d',     # 오후 3:00
            r'^\d+월\s*\d+일', # 1월 20일, 12월 5일 등
            r'^\d{4}년',       # 2025년 12월 29일 등 (년도로 시작)
            r'년\s*\d+월',     # ~년 12월 형태
        ]

        # 시간 패턴 필터링 (영어/중국어/일반)
        time_patterns_other = [
            r'^yesterday',
            r'^today',
            r'^\d+\s*(min|hour|day|week|month|year)s?\s*(ago)?',
            r'^just\s*now',
            r'^\d{1,2}:\d{2}',  # 5:02, 10:30
            r'^\d{1,2}\s*(am|pm|AM|PM)',  # 5 AM, 10 PM
            r'^昨天',          # 중국어 어제
            r'^今天',          # 중국어 오늘
            r'^\d+\s*小时前',  # N시간 전 (중국어)
        ]

        # 인코딩 깨진 시간 패턴 (UTF-8 -> Latin-1 변환 시 발생)
        broken_encoding_patterns = [
            r'^ì´ì ',         # "어제" 깨진 형태
            r'^ì˜¤ë',         # "오늘" 깨진 형태
            r'^ì˜¤ì ',        # "오전" 깨진 형태
            r'^ì˜¤í›„',        # "오후" 깨진 형태
            r'ì¤ì ',          # "오전" 일부
            r'ë¶„\s*ì ',      # "분 전" 깨진 형태
            r'ì‹œê°„\s*ì ',   # "시간 전" 깨진 형태
        ]
        time_patterns_other.extend(broken_encoding_patterns)

        all_patterns = time_patterns_ko + time_patterns_other

        for pattern in all_patterns:
            if re.search(pattern, name, re.IGNORECASE):
                logger.debug(f"시간 패턴으로 필터링됨: {name}")
                return False

        # Facebook 일반 페이지 제목 패턴 필터링
        # "(20+) 동영상", "(99+) Video", "Watch" 등
        fb_generic_patterns = [
            r'^\(\d+\+?\)',       # (20+), (99+) 등 알림 카운트
            r'^동영상$',          # 한국어 "동영상"
            r'^video$',          # 영어 "Video"
            r'^watch$',          # Watch
            r'^facebook$',       # Facebook
            r'^reels?$',         # Reel, Reels
            r'^live$',           # Live
            r'^라이브$',         # 한국어 "라이브"
            r'^릴스$',           # 한국어 "릴스"
            r'^워치$',           # 한국어 "워치"
            r'^mome$',           # Facebook 모바일 네비게이션 요소
            r'^home$',           # Home
            r'^홈$',             # 한국어 "홈"
            r'^messenger$',     # Messenger
            r'^메신저$',         # 한국어 "메신저"
            r'^explore$',       # Explore
            r'^둘러보기$',       # 한국어 "둘러보기"
            r'^create$',        # Create
            r'^만들기$',         # 한국어 "만들기"
            r'^notifications?$', # Notification(s)
            r'^알림$',           # 한국어 "알림"
            r'^search$',        # Search
            r'^검색$',           # 한국어 "검색"
            r'^프로필\d*$',      # 한국어 "프로필", "프로필0" 등
            r'^profile\d*$',    # 영어 "Profile", "Profile0" 등
            r'^내\s*프로필$',    # "내 프로필"
        ]
        for pattern in fb_generic_patterns:
            if re.search(pattern, name.strip(), re.IGNORECASE):
                logger.debug(f"Facebook 일반 페이지 제목으로 필터링됨: {name}")
                return False

        # "(20+) 동영상" 형태의 복합 패턴 필터링
        if re.match(r'^\(\d+\+?\)\s*(동영상|video|watch|facebook)', name, re.IGNORECASE):
            logger.debug(f"Facebook 알림 카운트 패턴으로 필터링됨: {name}")
            return False

        # UI 텍스트 필터링 (버튼, 라벨 등)
        ui_texts = [
            # 한국어 UI
            '좋아요', '댓글', '공유', '저장', '더보기', '접기',
            '팔로우', '팔로잉', '구독', '알림', '설정', '메뉴',
            '답글', '신고', '숨기기', '차단', '삭제', '동영상',
            # 영어 UI
            'like', 'comment', 'share', 'save', 'follow', 'following',
            'subscribe', 'more', 'menu', 'reply', 'report', 'hide',
            'block', 'delete', 'see more', 'view more', 'video', 'watch',
            # 중국어 UI
            '赞', '评论', '分享', '收藏', '关注', '更多',
        ]
        name_lower = name.lower().strip()
        for ui_text in ui_texts:
            if name_lower == ui_text.lower():
                logger.debug(f"UI 텍스트로 필터링됨: {name}")
                return False

        # 숫자와 콜론만으로 이루어진 경우 (시간 형식)
        if re.match(r'^[\d:\s]+$', name):
            return False

        # 너무 긴 이름 (보통 작성자/페이지 이름은 50자 이내)
        if len(name) > 50:
            return False

        # 단어 수 체크: 작성자 이름은 보통 4단어 이하 (공백 4개 초과 = 문장)
        if name.count(' ') > 4:
            logger.debug(f"단어 수 초과로 필터링됨 (공백 {name.count(' ')}개): {name}")
            return False

        # 한국어 문장/내용 패턴 감지 (조사/어미로 끝나는 경우 - 작성자 이름이 아님)
        sentence_endings = [
            r'할\s*때$', r'합니다$', r'입니다$', r'습니다$', r'됩니다$',
            r'한다$', r'된다$', r'는다$', r'인다$',
            r'해요$', r'예요$', r'이에요$', r'세요$',
            r'였다$', r'했다$', r'됐다$',
            r'등\s*\d+명의$', r'\d+명의$', r'\d+개의$',
            r'때문에$', r'에서$', r'에게$', r'로부터$',
            r'것이다$', r'이다$', r'였다$',
        ]
        name_stripped = name.strip()
        for pattern in sentence_endings:
            if re.search(pattern, name_stripped):
                logger.debug(f"문장 패턴으로 필터링됨: {name}")
                return False

        # "(20+) 텍스트" 패턴으로 시작하면 제목/알림이지 작성자가 아님
        if re.match(r'^\(\d+\+?\)\s+', name):
            logger.debug(f"알림 패턴으로 필터링됨: {name}")
            return False

        return True

    def _sanitize_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        결과 데이터를 정제 - 잘못된 작성자 이름 제거

        Args:
            result: 크롤링 결과

        Returns:
            정제된 결과
        """
        if result and result.get('author'):
            if not self._is_valid_author_name(result['author']):
                logger.debug(f"최종 결과에서 잘못된 작성자 제거: {result['author']}")
                result['author'] = None
        return result

    def _extract_content(self, driver: webdriver.Chrome) -> Optional[str]:
        """
        게시물 내용 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            게시물 내용 또는 None
        """
        # 쿠키 없는 별도 세션으로 내용 추출 시도
        try:
            import time
            import undetected_chromedriver as uc
            current_url = driver.current_url

            uc_options = uc.ChromeOptions()
            uc_options.add_argument('--headless=new')
            uc_options.add_argument('--no-sandbox')

            temp_driver = uc.Chrome(options=uc_options)
            temp_driver.get(current_url)
            time.sleep(10)
            temp_driver.execute_script('window.scrollTo(0, 500)')
            time.sleep(3)

            temp_src = temp_driver.page_source
            content_patterns_temp = [
                r'"savable_description"[^}]*"text"\s*:\s*"([^"]{10,3000})"',
                r'"message"[^}]*"text"\s*:\s*"([^"]{10,3000})"',
                r'"description"\s*:\s*"([^"]{10,3000})"'
            ]
            for p in content_patterns_temp:
                m = re.search(p, temp_src)
                if m:
                    content = m.group(1)
                    content = self._decode_unicode_escapes(content)
                    if len(content) > 5:
                        logger.info(f"쿠키 없는 세션에서 내용 추출: {content[:30]}...")
                        temp_driver.quit()
                        return content
            temp_driver.quit()
        except Exception as e:
            logger.debug(f"쿠키 없는 세션 내용 추출 실패: {e}")

        # 1. JSON 데이터에서 내용 추출 시도 (가장 정확)
        try:
            page_source = driver.page_source
            content_patterns = [
                # === Reel/Video 메시지 패턴 (최우선) ===
                # Facebook Reel caption - "message":{"text":"..."} (2026 구조: 중간에 다른 필드 있음)
                r'"message":\{[^}]*"text":"([^"]{10,5000})"',
                r'"message":\{"text":"([^"]{10,5000})"',
                # === Watch 동영상 전용 패턴 (2026 구조: ranges 등 필드 먼저 옴) ===
                r'"savable_description":\{[^}]*"text":"([^"]{5,5000})"',
                r'"savable_description":\{"text":"([^"]{5,5000})"',
                r'"video_title":"([^"]{5,500})"',
                r'"title":\{"text":"([^"]{5,500})"',
                # 2026년 페이스북 Reel/Video 패턴
                r'"attachments"[^}]*"title":"([^"]{5,500})"',
                r'"creation_story"[^}]*"message"[^}]*"text":"([^"]{5,5000})"',
                # 포스트 텍스트
                r'"text":"([^"]{10,5000})"[^}]*"__typename":"TextWithEntities"',
                r'"post_text":"([^"]{5,5000})"',
                # Watch 페이지 설명
                r'"video"[^}]*"description":\{"text":"([^"]{5,5000})"',
                r'"description_with_entities"[^}]*"text":"([^"]{5,5000})"',
                # 캡션/설명
                r'"caption":"([^"]{5,5000})"',
                r'"description":\{"text":"([^"]{5,5000})"',
                # 추가 패턴
                r'"story_attachment"[^}]*"description"[^}]*"text":"([^"]{5,5000})"',
                # og:description 메타 태그
                r'<meta\s+property="og:description"\s+content="([^"]+)"',
                r'<meta\s+content="([^"]+)"\s+property="og:description"',
                # 이름 필드 (비디오 제목으로 사용될 수 있음 - 최후 수단)
                r'"name":"([^"]{15,500})"',
            ]
            for pattern in content_patterns:
                match = re.search(pattern, page_source)
                if match:
                    content = match.group(1)
                    # JSON escape 문자 처리
                    content = content.replace('\\n', '\n').replace('\\u0040', '@')
                    content = decode_unicode_escapes(content)
                    # 필터링: UI 텍스트나 짧은 내용 제외
                    if content and len(content) > 5:
                        # Watch UI 텍스트 필터링
                        skip_texts = ['Watch', 'Log In', 'Sign Up', 'Facebook', 'See more']
                        if not any(content.strip().startswith(skip) for skip in skip_texts):
                            # JavaScript/코드 관련 텍스트 필터링
                            code_patterns = [
                                r'^[A-Z][a-z]+[A-Z]',  # CamelCase (e.g., WAWebOpus...)
                                r'^__',                # Double underscore
                                r'Bundle$',            # JavaScript bundle
                                r'Worker$',            # Web Worker
                                r'^function\s*\(',     # function declaration
                                r'^\{.*\}$',           # JSON object
                                r'^\[.*\]$',           # JSON array
                            ]
                            is_code = any(re.search(p, content.strip()) for p in code_patterns)
                            if not is_code:
                                logger.info(f"JSON에서 내용 추출: {content[:50]}...")
                                return content[:5000] if len(content) > 5000 else content
                            else:
                                logger.debug(f"코드 패턴 필터링: {content[:30]}...")
        except Exception as e:
            logger.debug(f"JSON 내용 추출 실패: {e}")

        # 2. DOM에서 내용 추출 (fallback)
        content_selectors = [
            # 2025-2026 최신 페이스북 구조
            "//div[@role='article']//div[@data-ad-comet-preview='message']",
            "//div[@role='article']//div[@dir='auto']//span[string-length(text()) > 10]",
            "//div[@role='article']//div[contains(@class, 'xdj266r')]",
            # 모바일 버전
            "//div[contains(@data-sigil, 'message')]",
            "//div[contains(@data-sigil, 'expose')]//span",
            "//div[@class='story_body_container']//p",
            # 데스크톱 버전
            "//div[@data-ad-preview='message']",
            "//div[contains(@class, 'userContent')]",
            # 일반 패턴
            "//div[@role='article']//div[contains(@style, 'text-align')]",
            "//p[contains(@class, 'text')]",
            # 추가 패턴
            "//div[@role='article']//div[@dir='auto'][not(ancestor::div[contains(@aria-label, 'Comment')])]",
        ]

        for selector in content_selectors:
            try:
                elem = driver.find_element(By.XPATH, selector)
                text = elem.text.strip()
                # 유효한 내용인지 확인 (링크, 날짜 등 제외)
                if text and len(text) > 5 and not text.startswith('http') and not re.match(r'^\d+\s*(h|d|w|m|y|분|시간|일)', text):
                    return text[:5000] if len(text) > 5000 else text
            except NoSuchElementException:
                continue

        return None

    def _extract_post_data_from_page(self, url: str) -> dict:
        """
        페이지에서 게시물 데이터 추출

        Args:
            url: 게시물 URL

        Returns:
            게시물 데이터 딕셔너리
        """
        result = {
            "platform": "facebook",
            "url": url,
            "author": None,
            "title": None,  # 게시물 제목
            "content": None,  # 게시물 본문
            "thumbnail": None,  # 썸네일 이미지
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
            "comments_list": [],  # 댓글 내용
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # 스코핑 관련 인스턴스 변수 초기화
            self._last_scoped_source = None
            self._scoping_succeeded = False

            # 브라우저 캐시 초기화 (이전 포스트 데이터 오염 방지)
            try:
                self.driver.get("about:blank")
                self.driver.execute_cdp_cmd("Network.clearBrowserCache", {})
                time.sleep(1)
            except Exception:
                pass

            # Rate limiting 적용
            self._rate_limit()

            # URL 변환 및 페이지 로드
            mobile_url = self._convert_to_mobile_url(url)
            self.driver.get(mobile_url)
            time.sleep(self.PAGE_LOAD_WAIT)

            # 페이지 로드 대기
            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # 추가 대기 (동적 콘텐츠 로드) - Watch 동영상용 충분한 대기
            time.sleep(5)

            # share URL 리다이렉트 확인 (로그인된 브라우저에서 JS 리다이렉트 발생)
            current_url = self.driver.current_url
            if "/share/" in url and current_url != url and "/share/" not in current_url:
                logger.info(f"share URL 리다이렉트 감지: {url[:60]} → {current_url[:80]}")
                url = current_url
                result["url"] = current_url

            # 스크롤해서 콘텐츠 로드 유도 (여러 번 스크롤)
            for scroll_y in [300, 600, 900]:
                self.driver.execute_script(f"window.scrollTo(0, {scroll_y})")
                time.sleep(1)
            time.sleep(3)

            # 로그인 필요 여부 확인
            current_url = self.driver.current_url
            if "login" in current_url.lower() or "checkpoint" in current_url.lower():
                logger.warning("로그인이 필요한 게시물입니다.")
                result["error"] = "login_required"
                result["error_type"] = "login_required"
                return result

            # 삭제/비공개 게시물 감지
            try:
                early_source = self.driver.page_source
                not_found_indicators = [
                    "This content isn't available",
                    "콘텐츠를 사용할 수 없습니다",
                    "이 콘텐츠를 이용할 수 없습니다",
                    "This page isn't available",
                    "이 페이지를 사용할 수 없습니다",
                    "The link you followed may be broken",
                    "Sorry, this content isn't available right now",
                    "일부 대상에게만 공유했거나",
                    "게시물이 삭제된",
                ]
                for indicator in not_found_indicators:
                    if indicator in early_source:
                        logger.warning(f"삭제/비공개 게시물 감지: {indicator}")
                        result["error"] = "게시물이 삭제되었거나 비공개 상태입니다"
                        result["error_type"] = "not_found"
                        return result
            except Exception:
                pass

            # === 작성자 및 제목 추출 ===
            page_source = self.driver.page_source

            # 방법 최우선: URL에서 페이지명/사용자명 추출
            # facebook.com/USERNAME/posts|videos|photos/... 패턴이 있으면 가장 정확
            try:
                # 원본 URL과 현재 URL 모두 확인
                for check_url in [url, self.driver.current_url]:
                    url_author_match = re.search(
                        r'facebook\.com/([^/?]+)/(posts|videos|photos)',
                        check_url
                    )
                    if url_author_match:
                        url_slug = url_author_match.group(1)
                        excluded_slugs = [
                            'profile.php', 'watch', 'groups', 'events', 'marketplace',
                            'gaming', 'photo.php', 'video.php', 'story.php',
                            'permalink.php', 'share', 'reel', 'login', 'checkpoint',
                            'home.php', 'mome', 'www.facebook.com', 'facebook.com',
                        ]
                        if url_slug not in excluded_slugs and not url_slug.isdigit() and len(url_slug) > 1:
                            result["author"] = url_slug
                            logger.info(f"URL에서 작성자 우선 설정: {url_slug}")
                            break
            except Exception:
                pass

            # 방법 0: og:title 메타 태그에서 직접 추출 (가장 신뢰성 높음)
            try:
                og_title_patterns = [
                    r'<meta\s+property="og:title"\s+content="([^"]+)"',
                    r'<meta\s+content="([^"]+)"\s+property="og:title"',
                    r'"og_title":"([^"]+)"',
                ]
                for pattern in og_title_patterns:
                    match = re.search(pattern, page_source)
                    if match:
                        og_title = match.group(1)
                        og_title = decode_unicode_escapes(og_title)
                        if og_title and '|' in og_title:
                            parts = [p.strip() for p in og_title.split('|')]
                            # 제목: 첫 번째 파트 (날짜가 아닌 경우)
                            if parts and parts[0] and len(parts[0]) > 3:
                                if not self._is_date_time_text(parts[0]):
                                    result["title"] = parts[0][:200]
                                    result["content"] = parts[0]
                                    logger.info(f"og:title에서 제목 추출: {result['title'][:50]}...")
                            # 작성자: Facebook 직전 파트 (URL 기반 작성자가 없을 때만)
                            if not result["author"]:
                                for i in range(len(parts) - 1, -1, -1):
                                    part = parts[i].strip()
                                    if part.lower() not in ['facebook', 'watch', ''] and len(part) > 1:
                                        if self._is_valid_author_name(part):
                                            result["author"] = part
                                            logger.info(f"og:title에서 작성자 추출: {part}")
                                            break
                            break
            except Exception as e:
                logger.debug(f"og:title 추출 실패: {e}")

            # 방법 0.5: JSON에서 video_owner 추출 (매우 정확)
            if not result["author"]:
                try:
                    # 로그인 사용자 필터링용
                    logged_in_user = None
                    viewer_match = re.search(r'"viewer"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', page_source)
                    if viewer_match:
                        logged_in_user = decode_unicode_escapes(viewer_match.group(1))
                        logger.debug(f"Watch 로그인 사용자 감지: {logged_in_user}")

                    video_owner_patterns = [
                        r'"video"[^}]*"owner"[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"video_owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"owner"\s*:\s*\{[^}]*"__typename"\s*:\s*"(?:Page|User)"[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"owning_page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"channelName"\s*:\s*"([^"]+)"',
                        # 단순 owner.name 패턴 (Watch 영상용)
                        r'"owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                    ]
                    for pattern in video_owner_patterns:
                        matches = re.findall(pattern, page_source)
                        if matches:
                            logger.debug(f"Watch 패턴 매칭: {pattern[:50]}... -> {len(matches)}개")
                        for match in matches[:10]:  # 최대 10개만 확인
                            owner = decode_unicode_escapes(match)
                            logger.debug(f"Watch owner 후보: '{owner}'")
                            # 로그인 사용자 제외
                            if logged_in_user and owner == logged_in_user:
                                logger.debug(f"Watch 로그인 사용자 제외: {owner}")
                                continue
                            if owner and self._is_valid_author_name(owner):
                                result["author"] = owner
                                logger.info(f"JSON video_owner에서 작성자 추출: {owner}")
                                break
                            else:
                                logger.debug(f"Watch owner 유효하지 않음: '{owner}'")
                        if result["author"]:
                            break
                except Exception as e:
                    logger.debug(f"JSON video_owner 추출 실패: {e}")

            # 방법 1: document.title (JavaScript) - fallback
            if not result["author"] or not result["title"]:
                try:
                    doc_title = self.driver.execute_script("return document.title;")
                    if doc_title:
                        # 패턴 1: "|" 구분자 (Watch 영상: "제목 | 페이지명 | Facebook")
                        if '|' in doc_title:
                            parts = [p.strip() for p in doc_title.split('|')]
                            # 제목: 첫 번째 파트
                            if not result["title"] and parts and parts[0] and len(parts[0]) > 3:
                                if not self._is_date_time_text(parts[0]):
                                    result["title"] = parts[0][:200]
                                    result["content"] = parts[0]
                                    logger.info(f"document.title에서 제목 추출: {result['title'][:50]}...")
                            # 작성자: Facebook 앞의 파트
                            if not result["author"] and len(parts) >= 2:
                                for i in range(len(parts) - 1, -1, -1):
                                    if parts[i].lower() != 'facebook' and parts[i].lower() != 'watch' and len(parts[i]) > 1:
                                        if self._is_valid_author_name(parts[i]):
                                            result["author"] = parts[i]
                                            logger.info(f"document.title에서 작성자 추출 (|): {parts[i]}")
                                            break

                        # 패턴 2: " - " 구분자 (일반 포스트: "(20+) 내용 - 작성자명")
                        if not result["author"] and ' - ' in doc_title:
                            parts = doc_title.rsplit(' - ', 1)  # 마지막 " - "로 분리
                            if len(parts) == 2:
                                author_part = parts[1].strip()
                                # "| Facebook" 제거
                                if '|' in author_part:
                                    author_part = author_part.split('|')[0].strip()
                                if author_part and author_part.lower() != 'facebook' and self._is_valid_author_name(author_part):
                                    result["author"] = author_part
                                    logger.info(f"document.title에서 작성자 추출 (-): {author_part}")
                                # 제목도 추출
                                if not result["title"] and parts[0]:
                                    # (20+) 제거
                                    title_part = re.sub(r'^\(\d+\+?\)\s*', '', parts[0]).strip()
                                    if title_part and len(title_part) > 3:
                                        result["title"] = title_part[:200]
                                        result["content"] = title_part
                except Exception as e:
                    logger.debug(f"document.title 추출 실패: {e}")

            # 방법 2: DOM에서 페이지/채널 이름 직접 추출 (작성자가 없을 때)
            if not result["author"]:
                try:
                    # Watch 페이지의 채널/페이지 이름 링크
                    author_js = self.driver.execute_script("""
                        var skipTexts = ['home','홈','mome','watch','워치','reels','릴스','marketplace',
                            '마켓플레이스','groups','그룹','gaming','게임','menu','메뉴','notifications',
                            '알림','messenger','메신저','video','동영상','facebook','search','검색',
                            'explore','둘러보기','saved','저장됨','events','이벤트','pages','페이지',
                            'friends','친구','live','라이브','more','더보기','create','만들기'];
                        function isValidAuthor(text) {
                            if (!text || text.length < 2 || text.length > 50) return false;
                            if (/^[0-9\\s\\.:]+$/.test(text)) return false;
                            if (skipTexts.indexOf(text.toLowerCase()) >= 0) return false;
                            if (/^[0-9년월일시분\\s]+$/.test(text)) return false;
                            return true;
                        }
                        // 방법 A: 게시물 article 내 프로필 링크에서 추출
                        var articles = document.querySelectorAll('div[role="article"], div[data-pagelet]');
                        for (var article of articles) {
                            var links = article.querySelectorAll('a[role="link"] strong, h2 a, h3 a');
                            for (var link of links) {
                                var text = link.innerText.trim();
                                if (isValidAuthor(text)) return text;
                            }
                        }
                        // 방법 B: 프로필 링크 (페이지명 포함 URL)
                        var profileLinks = document.querySelectorAll('a[href*="/posts/"], a[href*="/videos/"], a[href*="/photos/"]');
                        for (var plink of profileLinks) {
                            var span = plink.querySelector('span');
                            if (span) {
                                var text = span.innerText.trim();
                                if (isValidAuthor(text)) return text;
                            }
                        }
                        // 방법 C: h2/strong 내 링크 (넓은 범위, 단 네비게이션 제외)
                        var headings = document.querySelectorAll('h2 a span, strong a span');
                        for (var h of headings) {
                            var text = h.innerText.trim();
                            if (isValidAuthor(text)) return text;
                        }
                        return null;
                    """)
                    if author_js and self._is_valid_author_name(author_js):
                        result["author"] = author_js
                        logger.info(f"DOM JavaScript에서 작성자 추출: {author_js}")
                except Exception as e:
                    logger.debug(f"DOM 작성자 추출 실패: {e}")

            # 방법 3: Selenium으로 데스크톱 페이지 직접 방문하여 작성자 추출 (쿠키 유지)
            if not result["author"]:
                try:
                    mobile_url = self.driver.current_url
                    desktop_url = mobile_url.replace("m.facebook.com", "www.facebook.com")
                    if "m.facebook.com" in mobile_url:
                        logger.info(f"데스크톱 URL로 전환하여 작성자 추출: {desktop_url}")
                        self.driver.get(desktop_url)
                        time.sleep(5)

                        # 로그인 리다이렉트 확인
                        if "login" not in self.driver.current_url.lower():
                            desktop_source = self.driver.page_source
                            desktop_title = self.driver.execute_script("return document.title;") or ""
                            desktop_title = re.sub(r'^\(\d+\+?\)\s*', '', desktop_title)

                            # 3-A: document.title "콘텐츠 | 작성자명 | Facebook"
                            if '|' in desktop_title:
                                parts = [p.strip() for p in desktop_title.split('|')]
                                for i in range(len(parts) - 1, -1, -1):
                                    if parts[i].lower() not in ['facebook', 'watch', ''] and len(parts[i]) > 1:
                                        if self._is_valid_author_name(parts[i]):
                                            result["author"] = parts[i]
                                            logger.info(f"데스크톱 title에서 작성자 추출: {parts[i]}")
                                            break

                            # 3-B: document.title "콘텐츠 - 작성자명"
                            if not result["author"] and ' - ' in desktop_title:
                                parts = desktop_title.rsplit(' - ', 1)
                                if len(parts) == 2:
                                    author_part = parts[1].strip()
                                    if '|' in author_part:
                                        author_part = author_part.split('|')[0].strip()
                                    if author_part and author_part.lower() != 'facebook' and self._is_valid_author_name(author_part):
                                        result["author"] = author_part
                                        logger.info(f"데스크톱 title(-) 작성자: {author_part}")

                            # 3-C: JSON owner.name (데스크톱에서 풍부한 JSON 제공)
                            if not result["author"]:
                                # 로그인 사용자 필터링
                                logged_in = None
                                viewer_m = re.search(r'"viewer"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', desktop_source)
                                if viewer_m:
                                    logged_in = decode_unicode_escapes(viewer_m.group(1))

                                owner_patterns = [
                                    r'"owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                                    r'"video_owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                                    r'"owning_page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                                    r'"page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                                    r'"channelName"\s*:\s*"([^"]+)"',
                                    r'"publisher"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                                    r'"actor"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                                ]
                                for pattern in owner_patterns:
                                    match = re.search(pattern, desktop_source)
                                    if match:
                                        name = decode_unicode_escapes(match.group(1))
                                        if name and self._is_valid_author_name(name):
                                            if logged_in and name == logged_in:
                                                continue
                                            result["author"] = name
                                            logger.info(f"데스크톱 JSON에서 작성자 추출: {name}")
                                            break

                            # 3-D: DOM 기반 추출 (데스크톱)
                            if not result["author"]:
                                desktop_author = self.driver.execute_script("""
                                    var skipTexts = ['home','홈','mome','watch','워치','reels','릴스',
                                        'marketplace','마켓플레이스','groups','그룹','gaming','게임',
                                        'menu','메뉴','notifications','알림','messenger','메신저',
                                        'video','동영상','facebook','search','검색','explore',
                                        '둘러보기','saved','저장됨','events','이벤트','pages','페이지',
                                        'friends','친구','live','라이브','more','더보기','create','만들기'];
                                    function isValid(t) {
                                        if (!t || t.length < 2 || t.length > 50) return false;
                                        if (/^[0-9\\s\\.:]+$/.test(t)) return false;
                                        if (skipTexts.indexOf(t.toLowerCase()) >= 0) return false;
                                        return true;
                                    }
                                    // article 내 h2/strong/a 링크
                                    var articles = document.querySelectorAll('div[role="article"], div[data-pagelet]');
                                    for (var a of articles) {
                                        var els = a.querySelectorAll('h2 a span, strong a, a[role="link"] strong');
                                        for (var e of els) {
                                            var t = e.innerText.trim();
                                            if (isValid(t)) return t;
                                        }
                                    }
                                    // 최상위 h2 > a
                                    var h2s = document.querySelectorAll('h2 a');
                                    for (var h of h2s) {
                                        var t = h.innerText.trim();
                                        if (isValid(t)) return t;
                                    }
                                    return null;
                                """)
                                if desktop_author and self._is_valid_author_name(desktop_author):
                                    result["author"] = desktop_author
                                    logger.info(f"데스크톱 DOM에서 작성자 추출: {desktop_author}")

                            # 3-E: og:title 메타 태그
                            if not result["author"]:
                                og_m = re.search(r'property="og:title"\s+content="([^"]+)"', desktop_source)
                                if not og_m:
                                    og_m = re.search(r'content="([^"]+)"\s+property="og:title"', desktop_source)
                                if og_m:
                                    og_text = decode_unicode_escapes(og_m.group(1))
                                    if '|' in og_text:
                                        parts = [p.strip() for p in og_text.split('|')]
                                        for i in range(len(parts) - 1, -1, -1):
                                            if parts[i].lower() not in ['facebook', 'watch', ''] and len(parts[i]) > 1:
                                                if self._is_valid_author_name(parts[i]):
                                                    result["author"] = parts[i]
                                                    logger.info(f"데스크톱 og:title 작성자: {parts[i]}")
                                                    break

                        else:
                            logger.debug("데스크톱에서 로그인 리다이렉트 감지, 스킵")

                        # 원래 모바일 URL로 복귀하지 않음 (이후 추출에 영향 없음)
                except Exception as e:
                    logger.debug(f"데스크톱 Selenium 작성자 추출 실패: {e}")

            # 방법 4: URL에서 페이지명 추출 (최종 fallback)
            if not result["author"]:
                try:
                    cur_url = self.driver.current_url
                    # 패턴 1: facebook.com/pagename/posts|videos|photos|watch/...
                    url_match = re.search(r'facebook\.com/([^/?]+)/(posts|videos|photos|watch|reels?)', cur_url)
                    if not url_match:
                        # 패턴 2: facebook.com/pagename (단순 페이지 URL)
                        url_match = re.search(r'facebook\.com/([^/?]+)/?$', cur_url)
                    if url_match:
                        url_page = url_match.group(1)
                        excluded = ['profile.php', 'watch', 'groups', 'events', 'marketplace',
                                   'gaming', 'photo.php', 'video.php', 'story.php', 'permalink.php',
                                   'share', 'reel', 'login', 'checkpoint', 'home.php', 'mome']
                        if url_page not in excluded and not url_page.isdigit():
                            result["author"] = url_page
                            logger.info(f"URL에서 작성자 설정: {url_page}")
                except Exception:
                    pass

            if not result["content"]:
                # Watch 동영상 DOM에서 직접 설명 추출
                try:
                    content_js = self.driver.execute_script("""
                        // Watch 동영상 설명 추출
                        var selectors = [
                            'div[data-ad-comet-preview="message"]',
                            'div[data-ad-preview="message"]',
                            'span[dir="auto"][class*="x193iq5w"]',
                            'div[dir="auto"] span'
                        ];
                        for (var sel of selectors) {
                            var elems = document.querySelectorAll(sel);
                            for (var elem of elems) {
                                var text = elem.innerText.trim();
                                if (text && text.length > 20 && text.length < 1000) {
                                    if (!text.match(/^[0-9\\s\\.:년월일시분좋아요댓글공유]+$/)) {
                                        return text;
                                    }
                                }
                            }
                        }
                        return null;
                    """)
                    if content_js:
                        result["content"] = content_js[:5000]
                        logger.info(f"DOM JS에서 content 추출: {content_js[:50]}...")
                except Exception as e:
                    logger.debug(f"DOM content 추출 실패: {e}")

                if not result["content"]:
                    content_raw = self._extract_content(self.driver)
                    result["content"] = decode_unicode_escapes(content_raw) if content_raw else None
                # title은 content의 첫 줄에서 추출
                if result["content"] and not result["title"]:
                    first_line = result["content"].split('\n')[0].strip()
                    # 날짜/시간 패턴이면 제외
                    if not self._is_date_time_text(first_line) and len(first_line) > 3:
                        result["title"] = first_line[:100] if len(first_line) > 100 else first_line

            # Facebook 알림 배지 접두사 제거: "(20+) 실제내용" → "실제내용"
            for field in ["title", "content"]:
                if result.get(field) and re.match(r'^\(\d+\+?\)\s+', result[field]):
                    cleaned = re.sub(r'^\(\d+\+?\)\s+', '', result[field])
                    if cleaned:
                        logger.debug(f"알림 배지 접두사 제거 ({field}): {result[field][:30]} → {cleaned[:30]}")
                        result[field] = cleaned

            # HTML 엔티티 디코딩 (작성자/제목/콘텐츠)
            for field in ["author", "title", "content"]:
                if result.get(field):
                    result[field] = html_module.unescape(result[field])
                    # 미해석 HTML 엔티티 정리 (&Ai; -> &Ai)
                    # html.unescape 후 남은 &Word; 패턴은 유효하지 않은 엔티티
                    result[field] = re.sub(r'&([A-Za-z]{1,8});', r'&\1', result[field])

            # 최종 검증: title이 날짜/시간이면 제거
            if result.get("title") and self._is_date_time_text(result["title"]):
                logger.debug(f"날짜/시간 제목 필터링: {result['title']}")
                result["title"] = None

            # Facebook 일반 페이지 제목 패턴 필터링 (title/content)
            fb_generic_title_pattern = r'^\(\d+\+?\)\s*(동영상|video|watch|facebook|라이브|live|릴스|reels?)'
            if result.get("title") and re.match(fb_generic_title_pattern, result["title"], re.IGNORECASE):
                logger.debug(f"Facebook 일반 제목 필터링: {result['title']}")
                result["title"] = None
            if result.get("content") and re.match(fb_generic_title_pattern, result["content"], re.IGNORECASE):
                logger.debug(f"Facebook 일반 내용 필터링: {result['content']}")
                result["content"] = None
                # 필터링 후 실제 내용 다시 추출 시도
                content_raw = self._extract_content(self.driver)
                if content_raw:
                    result["content"] = decode_unicode_escapes(content_raw)
                    logger.info(f"필터링 후 재추출 성공: {result['content'][:50]}...")

            # 브라우저 알림/권한 팝업 텍스트 필터링 (Chrome 데스크톱 알림 등)
            browser_noise_patterns = [
                r'설정을\s*변경하려면\s*설정\s*슬라이더',
                r'Chrome에\s*데스크톱\s*알림',
                r'알림을\s*보낼\s*수\s*있는\s*권한',
                r'Turn on desktop notifications',
                r'Allow notifications',
            ]
            for field in ["content", "title"]:
                if result.get(field):
                    for noise_pattern in browser_noise_patterns:
                        if re.search(noise_pattern, result[field], re.IGNORECASE):
                            logger.debug(f"브라우저 알림 텍스트 필터링: {result[field][:50]}...")
                            result[field] = None
                            break

            # 필터링 후 content가 없으면 재추출
            if not result.get("content"):
                content_raw = self._extract_content(self.driver)
                if content_raw:
                    clean = decode_unicode_escapes(content_raw)
                    # 재추출된 내용도 브라우저 노이즈인지 체크
                    is_noise = False
                    for noise_pattern in browser_noise_patterns:
                        if re.search(noise_pattern, clean, re.IGNORECASE):
                            is_noise = True
                            break
                    if not is_noise:
                        result["content"] = clean
                        logger.info(f"필터링 후 재추출 성공: {clean[:50]}...")

            # 페이스북 썸네일 추출 (og:image 또는 JSON에서)
            try:
                thumb = None
                page_src = self.driver.page_source
                # og:image 메타 태그에서 추출 (가장 신뢰)
                og_match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\'](https?://[^"\']+)', page_src, re.IGNORECASE)
                if not og_match:
                    og_match = re.search(r'content=["\'](https?://[^"\']+)["\'].*?property=["\']og:image', page_src, re.IGNORECASE)
                if og_match:
                    thumb = og_match.group(1)
                    # Facebook CDN 이미지인지 확인 (프로필 아이콘 제외)
                    if thumb and ('fbcdn' in thumb or 'facebook' in thumb) and 'emoji' not in thumb:
                        result["thumbnail"] = thumb
                        logger.info(f"og:image 썸네일 추출: {thumb[:60]}...")
                if not result.get("thumbnail"):
                    # JSON에서 이미지 URL 추출 (fallback)
                    img_match = re.search(r'"preferred_thumbnail_image":\{"uri":"([^"]+)"', page_src)
                    if not img_match:
                        img_match = re.search(r'"image":\{"uri":"([^"]+)"', page_src)
                    if img_match:
                        thumb_url = img_match.group(1).replace('\\/', '/')
                        if 'fbcdn' in thumb_url:
                            result["thumbnail"] = thumb_url
                            logger.info(f"JSON 썸네일 추출: {thumb_url[:60]}...")
            except Exception as e:
                logger.debug(f"썸네일 추출 실패: {e}")

            # === JSON 패턴으로 engagement 데이터 추출 (스코핑 적용) ===
            self._try_javascript_extraction(result)

            # 좋아요: JSON 스코핑이 다른 게시물 데이터를 포함할 수 있으므로 DOM 교차 검증
            json_likes = result["likes"]
            dom_likes = self._extract_reactions(self.driver)
            if json_likes == 0:
                result["likes"] = dom_likes
            elif dom_likes > 0 and json_likes != dom_likes:
                # JSON과 DOM이 크게 다르면 (3배 이상 차이) DOM 우선
                ratio = max(json_likes, dom_likes) / max(min(json_likes, dom_likes), 1)
                if ratio >= 3:
                    logger.warning(
                        f"좋아요 교차검증: JSON({json_likes}) vs DOM({dom_likes}), "
                        f"비율 {ratio:.1f}x → DOM 값 사용"
                    )
                    result["likes"] = dom_likes

            # 작성자 기반 전체 소스 검증: 스코핑+DOM 모두 실패 시 안전망
            # 전체 page_source에서 작성자 근처의 reaction 데이터를 찾아 비교
            author_slug_for_verify = None
            full_source = None
            try:
                url_match = re.search(r'facebook\.com/([^/?]+)/(posts|videos|photos)', url)
                if url_match:
                    author_slug_for_verify = url_match.group(1)
                if author_slug_for_verify and result["likes"] < 500:
                    full_source = self.driver.page_source
                    # 작성자 슬러그의 모든 출현 위치에서 가장 가까운 reaction count 탐색
                    search_start_v = 0
                    best_author_likes = 0
                    while True:
                        slug_pos = full_source.find(author_slug_for_verify, search_start_v)
                        if slug_pos < 0:
                            break
                        # 작성자 위치 ±8000자 범위에서 reaction count 검색
                        v_start = max(0, slug_pos - 8000)
                        v_end = min(len(full_source), slug_pos + 8000)
                        v_window = full_source[v_start:v_end]
                        verify_patterns = [
                            r'"reaction_count":\{"count":(\d+)',
                            r'"i18n_reaction_count":"([\d,\.KMkm천만억]+)"',
                            r'"feedback_reaction_count":(\d+)',
                            r'"video_reaction_count":(\d+)',
                        ]
                        for vp in verify_patterns:
                            for vm in re.finditer(vp, v_window):
                                vc = self._parse_count(vm.group(1))
                                if vc > best_author_likes:
                                    best_author_likes = vc
                        search_start_v = slug_pos + 1
                        if search_start_v > len(full_source) - 10:
                            break
                    if best_author_likes > result["likes"]:
                        logger.warning(
                            f"작성자 기반 검증: 스코핑({result['likes']}) vs "
                            f"작성자 근처({best_author_likes}), "
                            f"비율 {best_author_likes/max(result['likes'],1):.1f}x → 작성자 근처 값 사용"
                        )
                        result["likes"] = best_author_likes
            except Exception as e:
                logger.debug(f"작성자 기반 검증 실패: {e}")

            # 댓글: JSON 스코핑이 다른 게시물 데이터를 포함할 수 있으므로 항상 DOM 교차 검증
            json_comments = result["comments"]
            # DOM 추출 시 스코핑 소스가 있으면 전달 (전체 페이지에서 "2" 등 UI 값 오캡처 방지)
            scoped_src = getattr(self, '_last_scoped_source', None) if getattr(self, '_scoping_succeeded', False) else None
            dom_comments = self._extract_comments_count(self.driver, scoped_source=scoped_src)
            if json_comments == 0:
                # JSON에서 못 찾았으면 DOM 값 사용 (노이즈 필터 완화 - 실제 댓글이 1-3개인 경우도 있음)
                result["comments"] = dom_comments
            elif dom_comments > 0 and json_comments > dom_comments * 5:
                # DOM이 매우 작은 값(1-3)이면 UI 노이즈 가능성 → JSON 우선
                if 1 <= dom_comments <= 3 and json_comments >= 5:
                    logger.warning(
                        f"댓글 교차검증: JSON({json_comments}) vs DOM({dom_comments}), "
                        f"DOM이 노이즈 의심(1-3) → JSON 값 유지"
                    )
                    # result["comments"]는 json_comments 그대로 유지
                else:
                    logger.warning(
                        f"댓글 교차검증: JSON({json_comments}) vs DOM({dom_comments}), "
                        f"비율 {json_comments/dom_comments:.0f}x → DOM 값 사용"
                    )
                    result["comments"] = dom_comments
            elif dom_comments == 0 and json_comments > 0:
                # DOM이 0인데 JSON에 값이 있는 경우: 좋아요 대비 비율로 오염 검증
                likes = result.get("likes", 0)
                if likes > 0 and json_comments > likes * 5:
                    logger.warning(
                        f"댓글 오염 의심 (DOM=0): JSON({json_comments}) vs likes({likes}), "
                        f"비율 {json_comments/likes:.0f}x → 0으로 대체"
                    )
                    result["comments"] = 0

            # 댓글도 작성자 기반 전체 소스 검증 (소규모 댓글 수 교정)
            try:
                if author_slug_for_verify and result["comments"] <= 10:
                    if not full_source:
                        full_source = self.driver.page_source
                    search_start_vc = 0
                    best_author_comments = 0
                    while True:
                        slug_pos = full_source.find(author_slug_for_verify, search_start_vc)
                        if slug_pos < 0:
                            break
                        v_start = max(0, slug_pos - 8000)
                        v_end = min(len(full_source), slug_pos + 8000)
                        v_window = full_source[v_start:v_end]
                        comment_verify_patterns = [
                            r'"comment_count":\{"total_count":(\d+)',
                            r'"comment_count":(\d+)',
                            r'"feedback_comment_count":(\d+)',
                        ]
                        for vp in comment_verify_patterns:
                            for vm in re.finditer(vp, v_window):
                                vc = int(vm.group(1))
                                if 0 < vc < 100000 and vc > best_author_comments:
                                    best_author_comments = vc
                        search_start_vc = slug_pos + 1
                        if search_start_vc > len(full_source) - 10:
                            break
                    if best_author_comments > max(result["comments"], 1):
                        logger.warning(
                            f"댓글 작성자 기반 검증: 기존({result['comments']}) vs "
                            f"작성자 근처({best_author_comments}) → 작성자 근처 값 사용"
                        )
                        result["comments"] = best_author_comments
            except Exception as e:
                logger.debug(f"댓글 작성자 기반 검증 실패: {e}")

            if result["shares"] == 0:
                result["shares"] = self._extract_shares_count(self.driver)
            if result["views"] is None:
                result["views"] = self._extract_views_count(self.driver)

            # === 댓글 내용 추출 시도 (강화 버전 v2) ===
            if self.collect_comments and (result["comments"] > 0 or True):  # collect_comments=False이면 스킵
                try:
                    comments_list = []

                    # === 1단계: 댓글 영역 클릭하여 댓글 로드 ===
                    logger.info("Facebook 댓글 로딩을 위해 버튼 클릭 시도...")

                    # 2026 Facebook UI: 댓글 수 표시 영역 클릭
                    comment_button_selectors = [
                        # 댓글 수가 표시된 영역 (가장 확실)
                        "//span[contains(text(), '댓글') and contains(text(), '개')]",
                        "//span[contains(text(), 'comment') and not(contains(text(), 'Write'))]",
                        # 댓글 아이콘/버튼
                        "//div[@aria-label='댓글 달기' or @aria-label='Leave a comment' or @aria-label='Write a comment']",
                        "//div[@aria-label='댓글' or @aria-label='Comment' or @aria-label='Comments']",
                        # 숫자 + 댓글 텍스트
                        "//span[matches(text(), '\\d+.*댓글')]",
                        "//span[contains(text(), '개의 댓글')]",
                        # 일반 댓글 버튼
                        "//span[text()='댓글' or text()='Comment' or text()='Comments']",
                        "//div[@role='button']//span[contains(text(), '댓글')]",
                        # 댓글 입력창 (클릭하면 댓글 섹션 열림)
                        "//div[contains(@aria-label, '댓글을 입력') or contains(@aria-label, 'Write a comment')]",
                        "//input[contains(@placeholder, '댓글') or contains(@placeholder, 'comment')]",
                    ]

                    clicked = 0
                    for selector in comment_button_selectors:
                        try:
                            buttons = self.driver.find_elements(By.XPATH, selector)
                            for btn in buttons[:3]:
                                try:
                                    if btn.is_displayed() and btn.is_enabled():
                                        self.driver.execute_script("arguments[0].click();", btn)
                                        clicked += 1
                                        logger.info(f"댓글 버튼 클릭: {selector[:50]}")
                                        time.sleep(2)
                                except Exception:
                                    pass
                            if clicked >= 1:
                                break
                        except Exception:
                            pass

                    if clicked > 0:
                        logger.info(f"댓글 버튼 {clicked}회 클릭")
                        time.sleep(5)  # 댓글 로드 대기 시간 증가 (5초)

                    # === 2단계: 모달 내에서 댓글 영역으로 스크롤 및 추가 로딩 ===
                    try:
                        # 모달이 있으면 모달 내에서 스크롤
                        modals = self.driver.find_elements(By.XPATH, "//div[@role='dialog']")
                        if modals:
                            modal = modals[0]
                            logger.info("모달 내에서 댓글 로드 중...")

                            # 모달 맨 아래로 스크롤 (댓글 섹션 로드)
                            for i in range(10):
                                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", modal)
                                time.sleep(0.5)

                            time.sleep(2)

                            # "관련성 높은 댓글" → "모든 댓글" 전환 클릭
                            relevance_btns = modal.find_elements(By.XPATH,
                                ".//span[contains(text(), '관련성') or contains(text(), 'Most relevant')]")
                            for btn in relevance_btns[:2]:
                                try:
                                    self.driver.execute_script("arguments[0].click();", btn)
                                    logger.info("관련성 메뉴 클릭")
                                    time.sleep(2)
                                    # "모든 댓글" 선택
                                    all_comments = self.driver.find_elements(By.XPATH,
                                        "//span[contains(text(), '모든 댓글') or contains(text(), 'All comments')]")
                                    for ac in all_comments[:1]:
                                        self.driver.execute_script("arguments[0].click();", ac)
                                        logger.info("모든 댓글 선택")
                                        time.sleep(2)
                                except Exception:
                                    pass

                            # 추가 스크롤
                            for i in range(5):
                                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", modal)
                                time.sleep(0.5)
                        else:
                            # 모달 없으면 일반 스크롤
                            for i in range(5):
                                self.driver.execute_script("window.scrollBy(0, 400);")
                                time.sleep(0.8)

                        # "더 보기", "이전 댓글 보기", "모든 댓글 보기" 클릭
                        more_selectors = [
                            "//span[contains(text(), '이전 댓글')]",
                            "//span[contains(text(), '더 보기') or contains(text(), '모두 보기')]",
                            "//span[contains(text(), 'View more') or contains(text(), 'previous')]",
                            "//span[contains(text(), 'View all') or contains(text(), 'See all')]",
                        ]
                        for sel in more_selectors:
                            try:
                                more_btns = self.driver.find_elements(By.XPATH, sel)
                                for btn in more_btns[:3]:
                                    try:
                                        if btn.is_displayed():
                                            self.driver.execute_script("arguments[0].click();", btn)
                                            logger.info(f"더보기 클릭: {sel[:40]}")
                                            time.sleep(2)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                        # 댓글 로드 대기
                        time.sleep(3)
                    except Exception:
                        pass

                    # 페이지 소스 새로 가져오기 (댓글 로드 후) - 모달 우선
                    # 모달이 있으면 모달의 HTML만 사용 (피드 게시물 제외)
                    modals = self.driver.find_elements(By.XPATH, "//div[@role='dialog']")
                    if modals:
                        try:
                            page_source = modals[0].get_attribute('outerHTML')
                            logger.info("3단계: 모달 HTML만 사용 (피드 필터링)")
                        except Exception:
                            page_source = self.driver.page_source
                    else:
                        page_source = self.driver.page_source

                    # === 3단계: 페이지 소스에서 댓글 JSON 추출 (작성자 포함) ===
                    # 작성자와 텍스트를 함께 추출하는 패턴 (2024-2026 Facebook 구조)
                    comment_with_author_patterns = [
                        # author.name + body.text 구조
                        r'"author":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        r'"commenter":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        # 역순 패턴
                        r'"body":\{"text":"((?:[^"\\]|\\.)*)"\}[^}]*"author":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}',
                        # 2025-2026 새 패턴: message.text 구조
                        r'"author":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}[^}]*"message":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        # display_name 패턴
                        r'"display_name":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        # short_name 패턴
                        r'"short_name":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        # username 패턴
                        r'"username":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        # feedback 구조
                        r'"feedback_commenter_name":"((?:[^"\\]|\\.)*)"\}[^}]*"text":"((?:[^"\\]|\\.)*)"\}',
                    ]

                    # 먼저 작성자+텍스트 함께 추출 시도
                    for pattern in comment_with_author_patterns:
                        matches = re.findall(pattern, page_source)
                        for match in matches[:15]:
                            if len(match) >= 2:
                                # 패턴에 따라 순서가 다를 수 있음
                                if 'body' in pattern[:20]:  # 역순 패턴
                                    text, author = match[0], match[1]
                                else:
                                    author, text = match[0], match[1]

                                author = decode_unicode_escapes(author)
                                text = decode_unicode_escapes(text)

                                if text and len(text) > 5 and not any(c.get('text') == text[:1000] for c in comments_list):
                                    # UI 요소 필터링 (강화)
                                    skip_json = ['좋아요', 'like', 'reply', '답글', '공유', 'share',
                                                '동영상에서', '둘러보기', '새로운 소식', '오리지널 오디오',
                                                'reels', 'watch', 'videos from', 'original audio',
                                                '팔로우', 'follow', '구독', 'subscribe', '홈', 'home']
                                    if not any(skip in text.lower() for skip in skip_json):
                                        comments_list.append({
                                            "author": author if author else "user",
                                            "text": text[:1000],
                                            "likes": 0
                                        })
                        if len(comments_list) >= 10:
                            break

                    # 작성자+텍스트 패턴 실패시, 텍스트만 추출하고 주변에서 작성자 찾기
                    if len(comments_list) < 5:
                        comment_patterns = [
                            r'"body":\{"text":"((?:[^"\\]|\\.)*)"}',
                            r'"comment_body":\{"text":"((?:[^"\\]|\\.)*)"}',
                            r'"message":\{"text":"((?:[^"\\]|\\.)*)"}',
                        ]

                        # 작성자명 추출 패턴 (우선순위 순)
                        author_name_patterns = [
                            r'"name":"((?:[^"\\]|\\.){2,50})"',
                            r'"display_name":"((?:[^"\\]|\\.){2,50})"',
                            r'"short_name":"((?:[^"\\]|\\.){2,50})"',
                            r'"username":"((?:[^"\\]|\\.){2,50})"',
                        ]

                        # 유효한 작성자명 검증 함수
                        def is_valid_author_name(name):
                            if not name or len(name) < 2 or len(name) > 50:
                                return False
                            # 제외할 키워드
                            skip_keywords = [
                                'facebook', 'video', 'comment', 'like', 'share',
                                'reply', 'bundle', 'worker', 'module', 'script',
                                'handler', 'listener', 'callback', 'undefined',
                                'null', 'true', 'false', 'function', 'object',
                            ]
                            if any(skip in name.lower() for skip in skip_keywords):
                                return False
                            # 순수 숫자 제외
                            if name.isdigit():
                                return False
                            # 특수문자만 있는 경우 제외
                            if not any(c.isalnum() for c in name):
                                return False
                            return True

                        for pattern in comment_patterns:
                            matches = re.findall(pattern, page_source)
                            for match in matches[:15]:
                                if match and len(match) > 5:
                                    text = decode_unicode_escapes(match)
                                    if text and len(text) > 5 and not any(c.get('text') == text[:1000] for c in comments_list):
                                        # UI 텍스트 필터링 (강화)
                                        skip_ui = ['좋아요', 'like', 'reply', '답글', '공유', 'share',
                                                  '동영상에서', '둘러보기', '새로운 소식', '오리지널 오디오',
                                                  'reels', 'watch', 'videos from', 'original audio']
                                        if not any(skip in text.lower() for skip in skip_ui):
                                            # 피드 게시물 필터링 (해시태그 3개 이상, URL 포함, 줄바꿈 2개 이상)
                                            hashtag_count = text.count('#')
                                            newline_count = text.count('\n')
                                            has_url = 'http' in text.lower() or 'www.' in text.lower()
                                            # 피드 게시물 특징: 해시태그 많음, URL 있음, 줄바꿈 많음
                                            if hashtag_count >= 3 or has_url or newline_count >= 2:
                                                continue  # 피드 게시물로 판단, 스킵

                                            # 텍스트 위치에서 작성자 찾기 시도
                                            text_pos = page_source.find(f'"{match}"')
                                            author_name = None

                                            if text_pos > 0:
                                                # 앞쪽 1500자에서 author name 찾기 (범위 확대)
                                                nearby = page_source[max(0, text_pos-1500):text_pos]

                                                # 여러 패턴으로 작성자 찾기 시도
                                                for author_pattern in author_name_patterns:
                                                    # 가장 마지막(텍스트에 가까운) 매치를 사용
                                                    author_matches = list(re.finditer(author_pattern, nearby))
                                                    if author_matches:
                                                        # 역순으로 검사 (텍스트에 가까운 것부터)
                                                        for am in reversed(author_matches):
                                                            potential_author = decode_unicode_escapes(am.group(1))
                                                            if is_valid_author_name(potential_author):
                                                                author_name = potential_author
                                                                break
                                                    if author_name:
                                                        break

                                            comments_list.append({
                                                "author": author_name if author_name else "user",
                                                "text": text[:1000],
                                                "likes": 0
                                            })
                            if len(comments_list) >= 10:
                                break

                    # === 4단계: DOM에서 댓글 요소 추출 (2026 강화 버전 v3) ===
                    if len(comments_list) < 5:
                        logger.info("DOM에서 직접 댓글 추출 시도...")

                        # 2026 Facebook 댓글 구조:
                        # [프로필이미지] [작성자이름(링크)] [댓글텍스트] [시간 좋아요 답글달기 번역보기]
                        # 핵심: "답글 달기" 또는 "Reply" 버튼이 있는 요소가 댓글

                        # 방법 0 (최우선): Facebook 2026 댓글 구조 기반 추출
                        # 댓글 구조: [프로필이미지][작성자링크][댓글텍스트][시간/좋아요/답글달기]

                        # 모달 또는 페이지에서 검색 컨텍스트 설정
                        search_context = self.driver
                        try:
                            modals = self.driver.find_elements(By.XPATH, "//div[@role='dialog']")
                            if modals:
                                search_context = modals[0]
                                logger.info(f"모달 내에서 댓글 검색 시작")
                        except Exception:
                            pass

                        # 숫자/통계 패턴 필터 함수
                        def is_stat_text(text):
                            """좋아요 수, 공유 수 등 통계 텍스트인지 확인"""
                            if not text:
                                return True
                            # 숫자+단위 패턴 (예: "2.6천", "54", "1.2만", "100K", "1.5M")
                            if re.match(r'^[\d,.]+\s*(천|만|억|K|M|B)?$', text, re.IGNORECASE):
                                return True
                            # 순수 숫자
                            if text.replace(',', '').replace('.', '').isdigit():
                                return True
                            return False

                        # Facebook UI 메뉴 항목 필터 (확장)
                        fb_ui_menu_items = [
                            # 좌측 사이드바 메뉴
                            '친구', '그룹', '마켓플레이스', '동영상', 'Watch', 'Marketplace',
                            '이벤트', '추억', '저장됨', '과거의 오늘', '메시지', '알림',
                            '홈', '피드', 'Reels', '게임', '페이지', '설정', '로그아웃',
                            'Friends', 'Groups', 'Events', 'Memories', 'Saved', 'On This Day',
                            '뉴스피드', '스토리', '더 보기', 'See More', '모두 보기',
                            # 광고/제품 관련
                            '광고 관리자', 'Meta Quest', 'Ad Manager', 'Business Suite',
                            '비즈니스 도구', 'Meta Business', '최근 광고 활동', '광고 활동',
                            # 메신저/앱 관련
                            'Messenger', 'Instagram', 'WhatsApp', 'Threads',
                            # 기타 UI 요소
                            '생일', '날씨', '기금 모금', 'Fundraiser', '쇼핑', 'Shopping',
                            '첫 번째 동영상', '모든 동영상', '앱센', 'Crisis Response',
                            '메타 AI', 'Meta AI', '게시물', '사진', '활동 로그',
                        ]

                        def is_fb_ui_menu(text):
                            """Facebook UI 메뉴 항목인지 확인"""
                            if not text:
                                return True
                            # 정확한 매치
                            if text in fb_ui_menu_items:
                                return True
                            # 부분 매치 (Meta Quest 3S 같은 경우)
                            for menu_item in fb_ui_menu_items:
                                if menu_item in text:
                                    return True
                            return False

                        # 방법 0-1: 답글 달기 버튼이 있는 댓글 아이템 찾기 (가장 정확)
                        # Facebook 댓글은 반드시 "답글 달기" 또는 "Reply" 버튼을 포함
                        comment_item_selectors = [
                            # 답글 달기 버튼이 있는 li 아이템 (가장 정확)
                            ".//ul//li[.//span[text()='답글 달기']]",
                            ".//ul//li[.//span[text()='Reply']]",
                            # div 기반 (답글 버튼 포함)
                            ".//div[@role='article'][.//span[text()='답글 달기' or text()='Reply']]",
                        ]

                        for item_sel in comment_item_selectors:
                            if len(comments_list) >= 10:
                                break
                            try:
                                comment_items = search_context.find_elements(By.XPATH, item_sel)
                                if comment_items:
                                    logger.info(f"댓글 아이템 발견: {len(comment_items)}개 ({item_sel[:40]}...)")

                                    for item in comment_items[:20]:
                                        try:
                                            # 작성자 찾기: 첫 번째 유효한 프로필 링크 텍스트
                                            author = None
                                            author_links = item.find_elements(By.XPATH, ".//a[contains(@href, '/') and @role='link']")
                                            for link in author_links[:5]:
                                                link_text = link.text.strip()
                                                href = link.get_attribute('href') or ''
                                                # 프로필 링크인지 확인
                                                if link_text and len(link_text) > 1 and len(link_text) < 50:
                                                    skip_ui = ['좋아요', '답글', '공유', '더 보기', '번역', 'Like', 'Reply', 'Share',
                                                               '시간', '분', '일', '주', 'hour', 'min', 'day', 'week']
                                                    if not any(skip in link_text for skip in skip_ui):
                                                        # 숫자/통계 텍스트 및 UI 메뉴 제외
                                                        if not is_stat_text(link_text) and not is_fb_ui_menu(link_text):
                                                            if 'facebook.com' in href or '/user/' in href or not href.startswith('http'):
                                                                author = link_text
                                                                break

                                            # 댓글 텍스트 찾기
                                            comment_text = None
                                            # dir='auto' span에서 텍스트 찾기
                                            spans = item.find_elements(By.XPATH, ".//span[@dir='auto']")
                                            for span in spans:
                                                text = span.text.strip()
                                                if text and len(text) > 5 and text != author:
                                                    skip_words = ['좋아요', '답글 달기', 'Reply', '공유', '더 보기', '번역',
                                                                  '동영상에서', '둘러보기', '새로운 소식', '오리지널 오디오']
                                                    if not any(skip in text for skip in skip_words):
                                                        # 시간 패턴 및 숫자/통계 제외
                                                        if not re.match(r'^(\d+\s*(시간|분|일|주|초|hour|min|day|week|sec))', text):
                                                            if not is_stat_text(text):
                                                                comment_text = text
                                                                break

                                            if comment_text and len(comment_text) > 5:
                                                if not any(c.get('text') == comment_text[:1000] for c in comments_list):
                                                    comments_list.append({
                                                        "author": author if author else "user",
                                                        "text": decode_unicode_escapes(comment_text[:1000]),
                                                        "likes": 0
                                                    })
                                                    logger.info(f"댓글 추출: author={author}, text={comment_text[:30]}...")
                                        except Exception as e:
                                            continue
                            except Exception as e:
                                logger.debug(f"댓글 아이템 셀렉터 실패: {e}")
                                continue

                        # 방법 0-2: 답글 버튼 기준 역추적 (fallback)
                        if len(comments_list) < 5:
                            reply_btn_selectors = [
                                ".//span[text()='답글 달기']",
                                ".//span[text()='Reply']",
                                ".//div[@role='button']//span[contains(text(), '답글')]",
                            ]

                            for reply_sel in reply_btn_selectors:
                                if len(comments_list) >= 10:
                                    break
                                try:
                                    reply_btns = search_context.find_elements(By.XPATH, reply_sel)
                                    if reply_btns:
                                        logger.info(f"답글 버튼 발견: {len(reply_btns)}개")

                                    for btn in reply_btns[:20]:
                                        try:
                                            # 답글 버튼에서 부모로 이동하며 댓글 컨테이너 찾기
                                            # Facebook 2026: 답글 버튼 → 버튼그룹 → 댓글내용div → 댓글컨테이너
                                            container = btn
                                            # 4-6단계 위로 올라가서 댓글 전체 컨테이너 찾기
                                            for level in range(6):
                                                try:
                                                    parent = container.find_element(By.XPATH, '..')
                                                    # 작성자 링크가 있는 레벨인지 확인
                                                    author_check = parent.find_elements(By.XPATH, ".//a[@role='link'][contains(@href, 'facebook.com') or contains(@href, '/user/')]")
                                                    if author_check:
                                                        container = parent
                                                        break
                                                    container = parent
                                                except Exception:
                                                    break

                                            # 작성자 찾기
                                            author = None
                                            author_links = container.find_elements(By.XPATH, ".//a[@role='link']")
                                            for link in author_links[:5]:
                                                link_text = link.text.strip()
                                                if link_text and 1 < len(link_text) < 40:
                                                    skip_ui = ['좋아요', '답글', '공유', '더 보기', '번역', 'Like', 'Reply', 'Share']
                                                    if not any(skip in link_text for skip in skip_ui):
                                                        if not re.match(r'^\d+\s*(시간|분|일|주|hour|min|day|week)', link_text):
                                                            # 숫자/통계 텍스트 및 UI 메뉴 제외
                                                            if not is_stat_text(link_text) and not is_fb_ui_menu(link_text):
                                                                author = link_text
                                                                break

                                            # 댓글 텍스트 찾기
                                            comment_text = None
                                            spans = container.find_elements(By.XPATH, ".//span[@dir='auto']")
                                            for span in spans:
                                                text = span.text.strip()
                                                if text and len(text) > 5 and text != author:
                                                    skip_words = ['좋아요', '답글', 'reply', '공유', '더 보기', '번역',
                                                                  '동영상에서', '둘러보기', '새로운 소식']
                                                    if not any(skip in text.lower() for skip in skip_words):
                                                        if not re.match(r'^\d+\s*(시간|분|일|주|초)', text):
                                                            # 숫자/통계 텍스트 제외
                                                            if not is_stat_text(text):
                                                                comment_text = text
                                                                break

                                            if comment_text and len(comment_text) > 5:
                                                if not any(c.get('text') == comment_text[:1000] for c in comments_list):
                                                    comments_list.append({
                                                        "author": author if author else "user",
                                                        "text": decode_unicode_escapes(comment_text[:1000]),
                                                        "likes": 0
                                                    })
                                                    logger.info(f"댓글 추출 (답글버튼): author={author}, text={comment_text[:30]}...")
                                        except Exception as ce:
                                            continue
                                except Exception:
                                    continue

                        if comments_list:
                            logger.info(f"Facebook 댓글 {len(comments_list)}개 추출 완료")

                        # 방법 1: 기존 컨테이너 방식 (fallback) - 모달 내에서만 검색
                        if len(comments_list) < 5 and search_context != self.driver:
                            comment_container_selectors = [
                                ".//ul//li[.//span[contains(text(),'답글') or contains(text(),'Reply')]]",  # 댓글 리스트 (답글 버튼 있는 것만)
                            ]

                            for container_sel in comment_container_selectors:
                                if len(comments_list) >= 10:
                                    break
                                try:
                                    containers = search_context.find_elements(By.XPATH, container_sel)
                                    for container in containers[:20]:
                                        try:
                                            # 컨테이너 내에서 작성자 찾기
                                            author = None
                                            try:
                                                author_elem = container.find_element(By.XPATH, ".//a[@role='link']//span[@dir='auto']")
                                                author = author_elem.text.strip()
                                            except Exception:
                                                pass

                                            # 컨테이너 내에서 댓글 텍스트 찾기
                                            text = None
                                            try:
                                                text_elems = container.find_elements(By.XPATH, ".//span[@dir='auto']")
                                                for te in text_elems:
                                                    t = te.text.strip()
                                                    if t and len(t) > 15 and len(t) < 500 and t != author:
                                                        skip_words = ['좋아요', 'like', 'reply', '답글', '공유', 'share',
                                                                    '시간', 'hour', 'day', '분', 'min', '주', 'week',
                                                                    '더 보기', 'see more', '번역', 'translate',
                                                                    '동영상에서', '둘러보기', '새로운 소식', '오리지널 오디오',
                                                                    'reels', 'watch', 'videos from', 'original audio',
                                                                    '팔로우', 'follow', '구독', 'subscribe',
                                                                    '홈', 'home', '마켓플레이스', 'marketplace',
                                                                    '그룹', 'groups', '게임', 'gaming', '메뉴', 'menu']
                                                        if not any(skip in t.lower() for skip in skip_words):
                                                            text = t
                                                            break
                                            except Exception:
                                                pass

                                            if text and len(text) > 10:
                                                if not any(c.get('text') == text[:1000] for c in comments_list):
                                                    valid_author = author if author and len(author) > 1 and len(author) < 50 else None
                                                    if valid_author:
                                                        skip_authors = ['facebook', 'video', 'comment', '좋아요', '답글',
                                                                       '친구', '그룹', '마켓플레이스', '동영상', 'Watch',
                                                                       '이벤트', '추억', '저장됨', '과거의 오늘', '메시지',
                                                                       '알림', '홈', '피드', 'Reels', '게임', '페이지',
                                                                       # 추가 UI 요소
                                                                       '광고 관리자', 'Meta Quest', 'Meta Business', 'Ad Manager',
                                                                       '생일', '날씨', '기금 모금', '쇼핑', 'Shopping',
                                                                       '첫 번째 동영상', '모든 동영상', '앱센', '메타 AI', 'Meta AI',
                                                                       '최근 광고 활동', '광고 활동', 'Messenger', 'Instagram',
                                                                       'WhatsApp', 'Threads', '활동 로그']
                                                        if any(skip in valid_author for skip in skip_authors):
                                                            valid_author = None

                                                    comments_list.append({
                                                        "author": valid_author if valid_author else "user",
                                                        "text": decode_unicode_escapes(text[:1000]),
                                                        "likes": 0
                                                    })

                                                    if len(comments_list) >= 10:
                                                        break
                                        except Exception as ce:
                                            continue
                                except Exception:
                                    continue

                        # 방법 2: 기존 방식 (작성자/텍스트 분리 추출) - fallback, 모달 내에서만 검색
                        if len(comments_list) < 5 and search_context != self.driver:
                            logger.debug("방법 2: 모달 내 작성자/텍스트 분리 추출 시도")
                            dom_author_names = []
                            try:
                                author_selectors = [
                                    ".//a[@role='link']//span[@dir='auto']",
                                    ".//div[@role='article']//a//span",
                                ]
                                for sel in author_selectors:
                                    try:
                                        author_elems = search_context.find_elements(By.XPATH, sel)
                                        for ae in author_elems[:30]:
                                            try:
                                                name = ae.text.strip()
                                                if name and len(name) > 1 and len(name) < 50:
                                                    skip_names = ['facebook', 'video', 'comment', 'like', '좋아요',
                                                                '답글', 'share', '공유', 'reply', '더 보기',
                                                                '시간', 'hour', 'day', '분', 'min', '주', 'week',
                                                                '친구', '그룹', '마켓플레이스', '동영상', 'Watch',
                                                                '이벤트', '추억', '저장됨', '과거의 오늘', '메시지',
                                                                '알림', '홈', '피드', 'Reels', '게임', '페이지',
                                                                '뉴스피드', '스토리', '모두 보기',
                                                                # 추가 UI 요소
                                                                '광고 관리자', 'Meta Quest', 'Meta Business', 'Ad Manager',
                                                                '생일', '날씨', '기금 모금', '쇼핑', 'Shopping',
                                                                '첫 번째 동영상', '모든 동영상', '앱센', '메타 AI', 'Meta AI',
                                                                '최근 광고 활동', '광고 활동', 'Messenger', 'Instagram',
                                                                'WhatsApp', 'Threads', '활동 로그']
                                                    if not any(skip in name for skip in skip_names):
                                                        if not name.replace(',', '').replace('.', '').isdigit():
                                                            if name not in dom_author_names:
                                                                dom_author_names.append(name)
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                            dom_selectors = [
                                ".//div[@role='article']//span[@dir='auto'][string-length(text()) > 15]",
                                ".//ul//li//span[@dir='auto'][string-length(text()) > 15]",
                                ".//div[contains(@class, 'x1lliihq')]//span[string-length(text()) > 15]",
                            ]

                            author_idx = 0
                            for selector in dom_selectors:
                                if len(comments_list) >= 10:
                                    break
                                try:
                                    elements = search_context.find_elements(By.XPATH, selector)
                                    for elem in elements[:15]:
                                        try:
                                            text = elem.text.strip()
                                            if text and len(text) > 15 and len(text) < 300:
                                                skip_words = ['좋아요', 'like', 'reply', '답글', '공유', 'share',
                                                            '시간', 'hour', 'day', '분', 'min', '더 보기',
                                                            # Facebook UI/네비게이션 요소 필터
                                                            '동영상에서', '둘러보기', '새로운 소식', '오리지널 오디오',
                                                            'reels', 'watch', 'videos from', 'original audio',
                                                            '팔로우', 'follow', '구독', 'subscribe',
                                                            '홈', 'home', '마켓플레이스', 'marketplace',
                                                            '그룹', 'groups', '게임', 'gaming', '메뉴', 'menu']
                                                if not any(skip in text.lower() for skip in skip_words):
                                                    if not any(c.get('text') == text[:1000] for c in comments_list):
                                                        author = "user"
                                                        if author_idx < len(dom_author_names):
                                                            author = dom_author_names[author_idx]
                                                            author_idx += 1

                                                        comments_list.append({
                                                            "author": author,
                                                            "text": decode_unicode_escapes(text[:1000]),
                                                            "likes": 0
                                                        })
                                                        if len(comments_list) >= 10:
                                                            break
                                        except Exception:
                                            continue
                                except Exception:
                                    continue

                    if comments_list:
                        result["comments_list"] = comments_list
                        # 댓글 수가 0이면 추출된 댓글 리스트 길이 사용
                        if result["comments"] == 0:
                            result["comments"] = len(comments_list)
                        logger.info(f"Facebook 댓글 {len(comments_list)}개 수집")
                except Exception as ce:
                    logger.debug(f"Facebook 댓글 추출 실패: {ce}")

            # 댓글 내용 수집
            if self.collect_comments:
                result["comment_list"] = self._extract_comment_list(self.driver, self.max_comments)

            logger.info(
                f"데이터 추출 완료: likes={result['likes']}, "
                f"comments={result['comments']}, shares={result['shares']}"
            )

            # 최종 안전장치: 모든 추출 시도 후에도 유효 데이터 없으면 에러 설정
            has_any_data = (
                (result.get('likes') or 0) > 0 or
                (result.get('comments') or 0) > 0 or
                (result.get('shares') or 0) > 0 or
                (result.get('views') or 0) > 0
            )
            if not has_any_data and not result.get('error'):
                logger.warning("모든 추출 시도 실패 - 삭제/비공개 게시물 가능성")
                result["error"] = "게시물이 삭제되었거나 비공개 상태입니다"
                result["error_type"] = "not_found"

            return result

        except TimeoutException:
            logger.error(f"페이지 로드 타임아웃: {url}")
            result["error"] = "timeout"
            return result
        except Exception as e:
            logger.error(f"데이터 추출 중 오류: {e}")
            result["error"] = str(e)
            return result

    def _scope_source_by_post_id(self, page_source: str, url: str) -> str:
        """
        타겟 포스트 ID를 기반으로 page_source를 스코핑하여
        추천/관련 게시물의 데이터가 섞이지 않도록 함

        Args:
            page_source: 전체 페이지 소스
            url: 원본 게시물 URL

        Returns:
            스코핑된 소스 (실패 시 전체 소스 반환)
        """
        SCOPE_RADIUS = 15000  # ±15000자 컨텍스트 윈도우

        # engagement 데이터 존재 확인용 키워드
        engagement_indicators = [
            '"reaction_count"', '"like_count"', '"comment_count"',
            '"share_count"', '"i18n_reaction_count"', '"feedback_reaction_count"',
            '"video_reaction_count"', '"ufi_reaction_count"',
        ]

        def _try_scope_at(pos: int) -> Optional[str]:
            """특정 위치에서 스코핑 시도, engagement 데이터 있으면 반환"""
            start = max(0, pos - SCOPE_RADIUS)
            end = min(len(page_source), pos + SCOPE_RADIUS)
            window = page_source[start:end]
            for indicator in engagement_indicators:
                if indicator in window:
                    return window
            return None

        # 1. URL에서 포스트 ID 추출
        post_id = self._extract_post_id_from_url(url)
        if not post_id:
            try:
                post_id = self._extract_post_id_from_url(self.driver.current_url)
            except Exception:
                pass

        if post_id:
            logger.debug(f"스코핑용 포스트 ID: {post_id}")

            # 2a. pfbid URL인 경우: page_source에 pfbid 문자열이 없을 수 있음
            # → 작성자 슬러그 + engagement 동시 매칭으로 정확한 데이터 블록 탐색
            if post_id.startswith('pfbid'):
                # pfbid 자체를 먼저 검색
                pfbid_pos = page_source.find(post_id)
                if pfbid_pos >= 0:
                    scoped = _try_scope_at(pfbid_pos)
                    if scoped:
                        logger.info(f"pfbid 스코핑 성공 (pos={pfbid_pos})")
                        return scoped

                # URL에서 작성자 슬러그 추출 (facebook.com/USERNAME/posts/pfbid...)
                author_slug = None
                url_author_match = re.search(r'facebook\.com/([^/?]+)/posts/', url)
                if url_author_match:
                    author_slug = url_author_match.group(1)
                    logger.debug(f"pfbid 스코핑: 작성자 슬러그 '{author_slug}'")

                # 모든 story_fbid 위치를 탐색하여 작성자+engagement 동시 매칭 우선
                author_engagement_pos = None  # 작성자 + engagement 동시 매칭 (최우선)
                engagement_only_pos = None     # engagement만 매칭 (차선)
                best_engagement_count = 0      # engagement 지표 개수 (더 많을수록 타겟일 확률 높음)
                search_start = 0
                while True:
                    story_match = re.search(r'"story_fbid":"(\d{10,})"', page_source[search_start:])
                    if not story_match:
                        break
                    actual_pos = search_start + story_match.start()

                    start = max(0, actual_pos - SCOPE_RADIUS)
                    end = min(len(page_source), actual_pos + SCOPE_RADIUS)
                    window = page_source[start:end]

                    has_engagement = any(ind in window for ind in engagement_indicators)
                    has_author = author_slug and (
                        f'"{author_slug}"' in window or
                        f'/{author_slug}/' in window or
                        f'\\/{author_slug}\\/' in window or
                        f'/{author_slug}' in window or  # URL 경로 끝
                        f'\\/{author_slug}' in window or  # escaped URL 경로 끝
                        (len(author_slug) >= 8 and author_slug in window)  # 8자 이상이면 plain 매칭
                    )

                    engagement_count = sum(1 for ind in engagement_indicators if ind in window)

                    if has_engagement and has_author and author_engagement_pos is None:
                        author_engagement_pos = actual_pos
                        logger.info(
                            f"pfbid → 작성자({author_slug})+engagement 매칭: "
                            f"story_fbid={story_match.group(1)} (pos={actual_pos}, indicators={engagement_count})"
                        )
                        break  # 최우선 매칭 발견
                    elif has_engagement:
                        # engagement 지표가 더 많은 위치 = 타겟 포스트일 확률 높음
                        if engagement_only_pos is None or engagement_count > best_engagement_count:
                            engagement_only_pos = actual_pos
                            best_engagement_count = engagement_count

                    search_start = actual_pos + 1

                # 최우선: 작성자+engagement, 차선: engagement만
                best_pos = author_engagement_pos or engagement_only_pos
                if best_pos is not None:
                    start = max(0, best_pos - SCOPE_RADIUS)
                    end = min(len(page_source), best_pos + SCOPE_RADIUS)
                    scoped = page_source[start:end]
                    if author_engagement_pos:
                        logger.info(f"pfbid 작성자 기반 스코핑 성공 (pos={best_pos})")
                    else:
                        logger.info(f"pfbid engagement 스코핑 (작성자 미매칭, pos={best_pos})")
                    return scoped

            else:
                # 2b. 숫자 ID인 경우: 모든 출현 위치를 탐색하여 engagement 데이터가 있는 곳 선택
                specific_patterns = [
                    f'"post_id":"{post_id}"',
                    f'"story_fbid":"{post_id}"',
                    f'"videoID":"{post_id}"',
                    f'"video_id":"{post_id}"',
                    f'"id":"{post_id}"',
                ]

                # 1차: 모든 패턴의 모든 출현 위치에서 engagement 데이터가 있는 곳 탐색
                fallback_pos = None  # engagement 없어도 ID가 있는 첫 위치
                for pattern in specific_patterns:
                    search_start = 0
                    while True:
                        pos = page_source.find(pattern, search_start)
                        if pos < 0:
                            break
                        if fallback_pos is None:
                            fallback_pos = pos
                        scoped = _try_scope_at(pos)
                        if scoped:
                            logger.info(f"포스트 스코핑 성공: '{pattern[:40]}' (pos={pos})")
                            return scoped
                        search_start = pos + 1

                # 2차: 순수 ID 문자열의 모든 출현 위치 탐색 (10자리 이상)
                if len(post_id) >= 10:
                    search_str = f'"{post_id}"'
                    search_start = 0
                    while True:
                        pos = page_source.find(search_str, search_start)
                        if pos < 0:
                            break
                        if fallback_pos is None:
                            fallback_pos = pos
                        scoped = _try_scope_at(pos)
                        if scoped:
                            logger.info(f"포스트 스코핑 (순수 ID): '{post_id}' (pos={pos})")
                            return scoped
                        search_start = pos + 1

                # 3차: engagement 없어도 ID가 있는 위치 기반 스코핑
                if fallback_pos is not None:
                    start = max(0, fallback_pos - SCOPE_RADIUS)
                    end = min(len(page_source), fallback_pos + SCOPE_RADIUS)
                    scoped = page_source[start:end]
                    logger.info(f"포스트 스코핑 (engagement 미확인, fallback): pos={fallback_pos}")
                    return scoped

        # 3. 스코핑 실패 시 전체 소스 반환
        logger.warning(f"포스트 ID '{post_id}'를 소스에서 찾지 못함, 전체 소스 사용")
        return page_source

    def _try_javascript_extraction(self, result: dict) -> None:
        """
        JavaScript를 통한 데이터 추출 시도 (포스트 ID 스코핑 적용)

        Args:
            result: 결과 딕셔너리 (업데이트됨)
        """
        try:
            page_source = self.driver.page_source

            # 타겟 포스트 ID 기반 스코핑 (추천 게시물 데이터 혼입 방지)
            search_source = self._scope_source_by_post_id(page_source, result.get("url", ""))

            # 스코핑 결과 저장 (교차검증에서 활용)
            self._last_scoped_source = search_source
            self._scoping_succeeded = (search_source != page_source)

            # 반응 수 추출 (다양한 패턴 시도)
            reaction_patterns = [
                # 2025-2026년 Facebook Reel/Video 패턴 (한국어 천/만/억 포함)
                r'"reaction_count_reduced":"([\d,\.KMkm천만억]+)"',
                r'"video_reaction_count":(\d+)',
                r'"ufi_reaction_count":(\d+)',
                # 2026년 새 패턴 - i18n_reaction_count (JSON escape 버전, 한국어 포함)
                r'\"i18n_reaction_count\":\"([\d,\.KMkm천만억]+)\"',
                r'"i18n_reaction_count":"([\d,\.KMkm천만억]+)"',
                # reaction_count (JSON escape 버전)
                r'\"reaction_count\":\{\"count\":(\d+)',
                r'"reaction_count":\{"count":(\d+)',
                # 기존 패턴들
                r'"reaction_count"\s*:\s*\{"count"\s*:\s*(\d+)',
                r'"likecount"\s*:\s*(\d+)',
                r'"like_count"\s*:\s*(\d+)',
                # Reel/Short 동영상 패턴
                r'"feedback_reaction_count":(\d+)',
                r'"reactors":\{"count":(\d+)',
                # 추가 패턴
                r'"total_count":(\d+).*?"reaction"',
                # 단순 count 패턴 (마지막 fallback)
                r'"unified_reactors":\{"count":(\d+)',
            ]
            for pattern in reaction_patterns:
                match = re.search(pattern, search_source, re.IGNORECASE)
                if match:
                    count = self._parse_count(str(match.group(1)))
                    if count > 0:
                        result["likes"] = count
                        logger.info(f"JSON에서 반응 수 추출 (스코핑): {count}")
                        break

            # 댓글 수 추출 (스코핑된 소스에서 검색)
            comment_patterns = [
                # 2025-2026년 Video/Reel 패턴 (가장 정확)
                r'"video_comment_count":(\d+)',
                r'"feedback_comment_count":(\d+)',
                # total_count가 명시된 패턴 (정확)
                r'\"comment_count\":\{\"total_count\":(\d+)',
                r'"comment_count"\s*:\s*\{"total_count"\s*:\s*(\d+)',
                r'"comments"\s*:\s*\{"count"\s*:\s*(\d+)',
                # 단순 패턴 (fallback)
                r'"comments_count":(\d+)',
                r'"commentcount"\s*:\s*(\d+)',
            ]
            for pattern in comment_patterns:
                match = re.search(pattern, search_source, re.IGNORECASE)
                if match:
                    count = self._parse_count(str(match.group(1)))
                    if count > 0 and count < 1000000:
                        result["comments"] = count
                        logger.info(f"JSON에서 댓글 수 추출 (스코핑): {count}")
                        break
                    elif count >= 1000000:
                        logger.warning(f"비정상적으로 큰 댓글 수 무시: {count}")

            # 공유 수 추출 (스코핑된 소스에서 검색)
            share_patterns = [
                r'\"share_count\":\{\"count\":(\d+)',
                r'"share_count"\s*:\s*\{"count"\s*:\s*(\d+)',
                r'"sharecount"\s*:\s*(\d+)',
            ]
            for pattern in share_patterns:
                match = re.search(pattern, search_source, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    if count > 0:
                        result["shares"] = count
                        logger.info(f"JSON에서 공유 수 추출 (스코핑): {count}")
                        break

        except Exception as e:
            logger.debug(f"JavaScript 데이터 추출 실패: {e}")

    def crawl_post(self, url: str, require_login: bool = False) -> dict:
        """
        Facebook 게시물 데이터 크롤링

        Args:
            url: Facebook 게시물 URL
            require_login: 로그인 필요 여부

        Returns:
            {
                "platform": "facebook",
                "url": str,
                "author": str,
                "content": str,
                "likes": int,  # reactions
                "comments": int,
                "shares": int,
                "views": int or None,
                "crawled_at": str (ISO format)
            }
        """
        # URL 유효성 검사 - ValueError 대신 에러 dict 반환 (verify-bot 프로토콜)
        if not url or "facebook.com" not in url:
            logger.error(f"유효하지 않은 Facebook URL: {url}")
            return {
                "platform": "facebook",
                "url": url or "",
                "author": None,
                "content": None,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": f"유효하지 않은 Facebook URL입니다. facebook.com 도메인이 포함된 URL을 입력해주세요.",
                "error_type": "validation_error"
            }

        # share/v 단축 URL 처리 - 실제 URL로 리다이렉트
        original_url = url
        is_share_url = "/share/v/" in url or "/share/p/" in url or "/share/r/" in url
        if is_share_url:
            logger.info(f"Facebook 단축 URL 감지, 리다이렉트 처리: {url}")
            try:
                # requests로 리다이렉트 따라가기
                response = requests.head(url, allow_redirects=True, timeout=15,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                if response.url and response.url != url and "/share/" not in response.url:
                    url = response.url
                    is_share_url = False
                    logger.info(f"리다이렉트된 URL: {url}")
                else:
                    logger.info(f"requests 리다이렉트 미발생 (JS 기반), 로그인 후 Selenium에서 처리")
            except Exception as e:
                logger.warning(f"리다이렉트 처리 실패, 로그인 후 Selenium에서 처리: {e}")

        # 1. API 방식 우선 시도 (쿠키 인증 포함)
        if self.use_api and self.session:
            logger.info("requests API로 크롤링 시도...")
            result = self._crawl_via_api(url)
            if result and (result.get('likes', 0) > 0 or result.get('author')):
                logger.info(f"API 크롤링 성공: likes={result.get('likes')}, author={result.get('author')}")
                # API 경로는 thumbnail이 없으므로 별도로 og:image 추출
                if not result.get('thumbnail'):
                    try:
                        thumb_resp = self.session.get(
                            url, timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        )
                        if thumb_resp.status_code == 200:
                            og_match = re.search(
                                r'<meta\s+property=["\']og:image["\']\s+content=["\'](https?://[^"\']+)',
                                thumb_resp.text, re.IGNORECASE
                            )
                            if not og_match:
                                og_match = re.search(
                                    r'content=["\'](https?://[^"\']+)["\'].*?property=["\']og:image',
                                    thumb_resp.text, re.IGNORECASE
                                )
                            if og_match:
                                thumb = og_match.group(1)
                                if thumb and ('fbcdn' in thumb or 'facebook' in thumb) and 'emoji' not in thumb:
                                    result['thumbnail'] = thumb
                                    logger.info(f"API 경로에서 og:image 썸네일 추출: {thumb[:60]}...")
                    except Exception as e:
                        logger.debug(f"API 경로 썸네일 추출 실패: {e}")
                return self._sanitize_result(result)
            logger.info("API 방식 실패, facebook-scraper fallback 시도...")

        # 2. facebook-scraper 시도
        if self.use_scraper:
            logger.info("facebook-scraper로 크롤링 시도...")
            result = self._crawl_via_scraper(url)
            if result and (result.get('likes', 0) > 0 or result.get('comments', 0) > 0):
                logger.info(f"facebook-scraper 성공: likes={result['likes']}, comments={result['comments']}")
                return self._sanitize_result(result)
            logger.info("facebook-scraper 실패 또는 데이터 없음, Selenium fallback 시도...")

        # 3. Cloud 환경에서 모든 방법 실패 시 - 기본 결과 반환
        if IS_CLOUD:
            logger.warning("Cloud 환경에서 Facebook 크롤링 제한적 - 기본 응답 반환")
            return {
                "platform": "facebook",
                "url": url,
                "author": None,
                "content": None,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "cloud_scraping_limited",
            }

        # 4. Selenium fallback (로컬 환경)
        if self.driver is None:
            self.driver = self._create_driver()

            # 저장된 쿠키 로드 시도
            if self.cookie_file.exists():
                logger.info("저장된 쿠키로 Selenium 세션 복원 시도...")
                if self._load_cookies():
                    # 로그인 상태 확인 (Instagram 크롤러와 동일한 패턴)
                    if self._check_login_status():
                        self.is_logged_in = True
                        logger.info("쿠키로 로그인 상태 복원 성공")
                    else:
                        logger.warning("쿠키가 만료되었거나 유효하지 않음")
                else:
                    logger.warning("쿠키 로드 실패")

        # 로그인 필요시
        if require_login and not self.is_logged_in:
            if not self.login():
                raise FacebookLoginError("로그인에 실패했습니다.")

        # 게시물 데이터 추출
        result = self._sanitize_result(self._extract_post_data_from_page(url))

        # 썸네일 후처리: 어떤 경로든 thumbnail이 없으면 별도 requests로 og:image 추출
        if result and not result.get('thumbnail'):
            try:
                thumb_url = result.get('url') or url
                thumb_resp = requests.get(
                    thumb_url, timeout=10, allow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )
                if thumb_resp.status_code == 200:
                    og_match = re.search(
                        r'<meta\s+property=["\']og:image["\']\s+content=["\'](https?://[^"\']+)',
                        thumb_resp.text, re.IGNORECASE
                    )
                    if not og_match:
                        og_match = re.search(
                            r'content=["\'](https?://[^"\']+)["\'].*?property=["\']og:image',
                            thumb_resp.text, re.IGNORECASE
                        )
                    if og_match:
                        thumb = og_match.group(1)
                        if thumb and ('fbcdn' in thumb or 'facebook' in thumb) and 'emoji' not in thumb:
                            result['thumbnail'] = thumb
                            logger.info(f"후처리에서 og:image 썸네일 추출: {thumb[:60]}...")
            except Exception as e:
                logger.debug(f"썸네일 후처리 실패: {e}")

        return result

    def crawl_posts(
        self,
        urls: List[str],
        require_login: bool = False,
        delay: float = 3.0
    ) -> List[dict]:
        """
        여러 게시물 데이터 크롤링

        Args:
            urls: 게시물 URL 리스트
            require_login: 로그인 필요 여부
            delay: 요청 간 딜레이 (초)

        Returns:
            게시물 데이터 딕셔너리 리스트
        """
        results = []

        # Rate limiting 설정 업데이트
        self.MIN_REQUEST_DELAY = max(delay, self.MIN_REQUEST_DELAY)

        for i, url in enumerate(urls):
            try:
                logger.info(f"크롤링 중 ({i+1}/{len(urls)}): {url}")
                result = self.crawl_post(url, require_login=require_login)
                results.append(result)

            except FacebookRateLimitError:
                logger.warning("Rate limit 감지. 잠시 대기 후 재시도...")
                time.sleep(60)  # 1분 대기
                try:
                    result = self.crawl_post(url, require_login=require_login)
                    results.append(result)
                except Exception as e:
                    results.append({
                        "platform": "facebook",
                        "url": url,
                        "error": str(e),
                        "crawled_at": datetime.now().isoformat(),
                    })

            except Exception as e:
                logger.error(f"크롤링 실패 ({url}): {e}")
                results.append({
                    "platform": "facebook",
                    "url": url,
                    "error": str(e),
                    "crawled_at": datetime.now().isoformat(),
                })

        return results

    def close(self) -> None:
        """브라우저 종료"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("브라우저 종료 완료")
            except Exception as e:
                logger.warning(f"브라우저 종료 중 오류: {e}")
            finally:
                self.driver = None
                self.is_logged_in = False

    def __enter__(self):
        """Context manager 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager 종료"""
        self.close()


# === 편의 함수 ===

def crawl_facebook_post(url: str, headless: bool = False, require_login: bool = False) -> dict:
    """
    Facebook 게시물 데이터 크롤링 (단일 함수)

    Args:
        url: Facebook 게시물 URL
        headless: 헤드리스 모드 (로그인 시에는 False 권장)
        require_login: 로그인 필요 여부

    Returns:
        {
            "platform": "facebook",
            "url": str,
            "author": str,
            "content": str,
            "likes": int,  # reactions
            "comments": int,
            "shares": int,
            "views": int or None,
            "crawled_at": str
        }
    """
    with FacebookCrawler(headless=headless) as crawler:
        return crawler.crawl_post(url, require_login=require_login)


def crawl_facebook_posts(
    urls: List[str],
    headless: bool = False,
    require_login: bool = False,
    delay: float = 3.0
) -> List[dict]:
    """
    여러 Facebook 게시물 데이터 크롤링 (단일 함수)

    Args:
        urls: 게시물 URL 리스트
        headless: 헤드리스 모드 (로그인 시에는 False 권장)
        require_login: 로그인 필요 여부
        delay: 요청 간 딜레이 (초)

    Returns:
        게시물 데이터 딕셔너리 리스트
    """
    with FacebookCrawler(headless=headless) as crawler:
        return crawler.crawl_posts(urls, require_login=require_login, delay=delay)


# === 테스트 코드 ===

if __name__ == "__main__":
    # 로깅 레벨 설정
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("Facebook 크롤러 테스트")
    print("=" * 60)

    # 테스트 URL
    test_url = input("테스트할 Facebook 게시물 URL을 입력하세요: ").strip()

    if test_url:
        try:
            result = crawl_facebook_post(test_url, headless=False)
            print("\n크롤링 결과:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"\n오류 발생: {e}")
    else:
        print("URL이 입력되지 않았습니다.")
