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


def decode_unicode_escapes(text: str) -> str:
    """유니코드 이스케이프 시퀀스를 디코딩 (\\uXXXX -> 실제 문자)

    이모지 등 surrogate pair도 올바르게 처리합니다.
    """
    if not text:
        return text
    try:
        # 방법 1: Python 내장 decode 사용 (surrogate pair 자동 처리)
        try:
            decoded = text.encode('utf-8').decode('unicode_escape')
            # surrogate pair가 있으면 UTF-16으로 재인코딩하여 해결
            decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
        except (UnicodeDecodeError, UnicodeEncodeError):
            # fallback: 수동 변환
            def replace_unicode(match):
                return chr(int(match.group(1), 16))
            decoded = re.sub(r'\\u([0-9a-fA-F]{4})', replace_unicode, text)
            try:
                decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
            except:
                pass
        # 추가 이스케이프 처리
        decoded = decoded.replace('\\n', '\n').replace('\\r', '\r')
        decoded = decoded.replace('\\t', '\t').replace('\\"', '"')
        decoded = decoded.replace('\\/', '/')
        return decoded
    except Exception:
        return text

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

# undetected_chromedriver for bot detection bypass
try:
    import undetected_chromedriver as uc
    HAS_UNDETECTED = True
except ImportError:
    HAS_UNDETECTED = False

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

            # 썸네일 이미지 추출 (og:image 메타 태그)
            thumbnail_patterns = [
                r'<meta\s+property="og:image"\s+content="([^"]+)"',
                r'<meta\s+content="([^"]+)"\s+property="og:image"',
                r'<img[^>]+src="(https://[^"]+fbcdn[^"]+)"',  # Facebook CDN 이미지
            ]
            for pattern in thumbnail_patterns:
                match = re.search(pattern, html)
                if match:
                    result["thumbnail"] = match.group(1)
                    break

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
            r'/(\d+)/?$',  # 끝의 숫자
            r'fbid=(\d+)',  # fbid=123456
            r'story_fbid=(\d+)',  # story_fbid=123456
            r'/permalink/(\d+)',  # /permalink/123456
            r'/videos/(\d+)',  # /videos/123456
            r'/photos/[^/]+/(\d+)',  # /photos/xxx/123456
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

            # get_posts로 특정 게시물 가져오기
            posts = get_posts(
                page_name,
                pages=1,
                options={
                    "posts_per_page": 10,
                    "allow_extra_requests": False,
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
        return {
            "platform": "facebook",
            "url": url,
            "author": post.get('username') or post.get('user_id') or "Unknown",
            "content": (post.get('text') or "")[:500],
            "likes": post.get('likes', 0) or post.get('reactions', 0) or 0,
            "comments": post.get('comments', 0) or 0,
            "shares": post.get('shares', 0) or 0,
            "views": post.get('video_views'),
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
            logger.info("undetected_chromedriver 사용")
            uc_options = uc.ChromeOptions()
            uc_options.add_argument("--no-sandbox")
            uc_options.add_argument("--disable-dev-shm-usage")
            if self.headless:
                uc_options.add_argument("--headless=new")
            driver = uc.Chrome(options=uc_options, use_subprocess=True)
            logger.info("Stealth Chrome WebDriver 생성 완료 (undetected)")
            return driver

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

        # www.facebook.com -> m.facebook.com
        url = url.replace("www.facebook.com", "m.facebook.com")
        url = url.replace("web.facebook.com", "m.facebook.com")

        # mbasic 버전으로 변환 (더 가벼움)
        # url = url.replace("m.facebook.com", "mbasic.facebook.com")

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

        text = text.strip()
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

    def _extract_reactions(self, driver: webdriver.Chrome) -> int:
        """
        반응(좋아요 등) 수 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            반응 수
        """
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
                    if text:
                        count = self._parse_count(text)
                        if count > 0:
                            return count
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        return 0

    def _extract_comments_count(self, driver: webdriver.Chrome) -> int:
        """
        댓글 수 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            댓글 수
        """
        comment_selectors = [
            # 모바일 버전
            "//a[contains(@href, 'comment')]//span",
            "//div[contains(@data-sigil, 'comment')]",
            # 데스크톱 버전
            "//span[contains(text(), '댓글') or contains(text(), 'comment')]",
            "//a[contains(text(), '댓글')]",
            "//div[contains(@aria-label, '댓글')]",
        ]

        for selector in comment_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for elem in elements:
                    text = elem.text.strip()
                    if text and ("댓글" in text or "comment" in text.lower()):
                        count = self._parse_count(text)
                        if count > 0:
                            return count
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        return 0

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
                # "영상제목 | 페이지명 | Facebook" 또는 "영상제목 | 페이지명"
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
                        logger.info(f"Title 태그에서 작성자 추출: {author}")
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
                # Watch 동영상 소유자 패턴 (가장 정확)
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
                # __typename이 Page인 경우
                r'"__typename"\s*:\s*"Page"[^}]*"name"\s*:\s*"([^"]+)"',
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

        # 너무 긴 이름 (보통 작성자 이름은 50자 이내)
        if len(name) > 100:
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
        # 1. JSON 데이터에서 내용 추출 시도 (가장 정확)
        try:
            page_source = driver.page_source
            content_patterns = [
                # === Watch 동영상 전용 패턴 (우선순위 높음) ===
                r'"savable_description":\{"text":"((?:[^"\\]|\\.)*)"}',
                r'"video_title":"((?:[^"\\]|\\.)*)"',
                r'"title":\{"text":"((?:[^"\\]|\\.)*)"}',
                r'"name":"((?:[^"\\]|\\.){10,500})"',  # 동영상 제목
                # 2026년 페이스북 Reel/Video 패턴
                r'"attachments"[^}]*"title":"((?:[^"\\]|\\.)*)"',
                r'"creation_story"[^}]*"message"[^}]*"text":"((?:[^"\\]|\\.)*)"',
                # 포스트 메시지/텍스트
                r'"message":\{"text":"((?:[^"\\]|\\.)*)"}',
                r'"text":"((?:[^"\\]|\\.){10,500})"[^}]*"__typename":"TextWithEntities"',
                r'"post_text":"((?:[^"\\]|\\.)*)"',
                # Watch 페이지 설명
                r'"video"[^}]*"description":\{"text":"((?:[^"\\]|\\.)*)"}',
                r'"description_with_entities"[^}]*"text":"((?:[^"\\]|\\.)*)"}',
                # 캡션
                r'"caption":"((?:[^"\\]|\\.)*)"',
                r'"description":\{"text":"((?:[^"\\]|\\.)*)"}',
                # 추가 패턴
                r'"story_attachment"[^}]*"description"[^}]*"text":"((?:[^"\\]|\\.)*)"}',
                # og:description 메타 태그
                r'<meta\s+property="og:description"\s+content="([^"]+)"',
                r'<meta\s+content="([^"]+)"\s+property="og:description"',
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
                            logger.info(f"JSON에서 내용 추출: {content[:50]}...")
                            return content[:500] if len(content) > 500 else content
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
                    # 최대 500자로 제한
                    return text[:500] if len(text) > 500 else text
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

            # 추가 대기 (동적 콘텐츠 로드)
            time.sleep(2)

            # 스크롤해서 콘텐츠 로드 유도
            self.driver.execute_script("window.scrollTo(0, 300)")
            time.sleep(2)

            # 로그인 필요 여부 확인
            current_url = self.driver.current_url
            if "login" in current_url.lower() or "checkpoint" in current_url.lower():
                logger.warning("로그인이 필요한 게시물입니다.")
                result["error"] = "login_required"
                return result

            # === Watch 동영상 전용: 다양한 방식으로 제목과 작성자 추출 ===
            page_source = self.driver.page_source

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
                            # 작성자: Facebook 직전 파트
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
                    video_owner_patterns = [
                        r'"video"[^}]*"owner"[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"video_owner"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"owner"\s*:\s*\{[^}]*"__typename"\s*:\s*"(?:Page|User)"[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"owning_page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"page"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                        r'"channelName"\s*:\s*"([^"]+)"',
                    ]
                    for pattern in video_owner_patterns:
                        match = re.search(pattern, page_source)
                        if match:
                            owner = decode_unicode_escapes(match.group(1))
                            if owner and self._is_valid_author_name(owner):
                                result["author"] = owner
                                logger.info(f"JSON video_owner에서 작성자 추출: {owner}")
                                break
                except Exception as e:
                    logger.debug(f"JSON video_owner 추출 실패: {e}")

            # 방법 1: document.title (JavaScript) - fallback
            if not result["author"] or not result["title"]:
                try:
                    doc_title = self.driver.execute_script("return document.title;")
                    if doc_title and '|' in doc_title:
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
                                        logger.info(f"document.title에서 작성자 추출: {parts[i]}")
                                        break
                except Exception as e:
                    logger.debug(f"document.title 추출 실패: {e}")

            # 방법 2: DOM에서 페이지/채널 이름 직접 추출 (작성자가 없을 때)
            if not result["author"]:
                try:
                    # Watch 페이지의 채널/페이지 이름 링크
                    author_js = self.driver.execute_script("""
                        // 방법 A: aria-label이 있는 링크에서 추출
                        var links = document.querySelectorAll('a[role="link"]');
                        for (var link of links) {
                            var href = link.getAttribute('href') || '';
                            if (href.includes('/watch/') || href.includes('/page/') || href.includes('/profile')) {
                                var text = link.innerText.trim();
                                if (text && text.length > 1 && text.length < 50 && !text.match(/^[0-9\\s\\.:]+$/)) {
                                    return text;
                                }
                            }
                        }
                        // 방법 B: 동영상 아래 페이지 이름
                        var spans = document.querySelectorAll('span[dir="auto"]');
                        for (var span of spans) {
                            var parent = span.closest('a');
                            if (parent && parent.href && (parent.href.includes('/watch/') || parent.href.includes('/'))) {
                                var text = span.innerText.trim();
                                if (text && text.length > 1 && text.length < 50 && !text.match(/^[0-9\\s\\.:년월일시분]+$/)) {
                                    return text;
                                }
                            }
                        }
                        return null;
                    """)
                    if author_js and self._is_valid_author_name(author_js):
                        result["author"] = author_js
                        logger.info(f"DOM JavaScript에서 작성자 추출: {author_js}")
                except Exception as e:
                    logger.debug(f"DOM 작성자 추출 실패: {e}")

            # og:title에서 추출 실패시 기존 방식 시도
            if not result["author"]:
                author_raw = self._extract_author(self.driver)
                result["author"] = decode_unicode_escapes(author_raw) if author_raw else None

            if not result["content"]:
                content_raw = self._extract_content(self.driver)
                result["content"] = decode_unicode_escapes(content_raw) if content_raw else None
                # title은 content의 첫 줄에서 추출
                if result["content"] and not result["title"]:
                    first_line = result["content"].split('\n')[0].strip()
                    # 날짜/시간 패턴이면 제외
                    if not self._is_date_time_text(first_line) and len(first_line) > 3:
                        result["title"] = first_line[:100] if len(first_line) > 100 else first_line

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

            # === 썸네일 추출 (동영상/게시물 이미지만, 메신저 제외) ===
            page_source = self.driver.page_source

            # 동영상 전용 썸네일 패턴 (우선순위 높음)
            video_thumbnail_patterns = [
                r'"video_preview_image":\{"uri":"([^"]+)"',
                r'"preferred_thumbnail":\{"image":\{"uri":"([^"]+)"',
                r'"thumbnailImage":\{"uri":"([^"]+)"',
                r'"playable_url_quality_hd".*?"thumbnail":\{"uri":"([^"]+)"',
                r'"video"[^}]*"thumbnailImage"[^}]*"uri":"([^"]+)"',
            ]

            for pattern in video_thumbnail_patterns:
                match = re.search(pattern, page_source)
                if match:
                    thumbnail = match.group(1)
                    thumbnail = thumbnail.replace('\\/', '/').replace('&amp;', '&')
                    # 메신저/채팅 관련 URL 제외
                    if 'messenger' not in thumbnail.lower() and 'chat' not in thumbnail.lower():
                        result["thumbnail"] = thumbnail
                        logger.info(f"동영상 썸네일 추출 성공: {thumbnail[:50]}...")
                        break

            # 동영상 썸네일 없으면 일반 이미지 시도
            if not result.get("thumbnail"):
                general_patterns = [
                    r'<meta\s+property="og:image"\s+content="([^"]+)"',
                    r'<meta\s+content="([^"]+)"\s+property="og:image"',
                ]
                for pattern in general_patterns:
                    match = re.search(pattern, page_source)
                    if match:
                        thumbnail = match.group(1)
                        thumbnail = thumbnail.replace('\\/', '/').replace('&amp;', '&')
                        # 메신저/채팅/프로필 관련 URL 제외
                        if ('messenger' not in thumbnail.lower() and
                            'chat' not in thumbnail.lower() and
                            '/p50x50/' not in thumbnail and  # 작은 프로필 이미지 제외
                            'profile' not in thumbnail.lower()):
                            result["thumbnail"] = thumbnail
                            logger.info(f"og:image 썸네일 추출 성공: {thumbnail[:50]}...")
                            break

            # === JSON 패턴으로 engagement 데이터 추출 ===
            self._try_javascript_extraction(result)

            # === 댓글 내용 추출 시도 (강화 버전) ===
            if result["comments"] > 0 or True:  # 항상 시도
                try:
                    comments_list = []

                    # === 1단계: "댓글 보기" 버튼 클릭하여 댓글 펼치기 ===
                    logger.info("Facebook 댓글 로딩을 위해 버튼 클릭 시도...")
                    comment_button_selectors = [
                        # 2024-2026 Facebook 댓글 버튼
                        "//div[@aria-label='댓글' or @aria-label='Comment' or @aria-label='留言']",
                        "//span[contains(text(), '댓글') and not(contains(text(), '없'))]",
                        "//span[contains(text(), 'comment') or contains(text(), 'Comment')]",
                        "//div[@role='button'][contains(., '댓글')]",
                        "//a[contains(@href, 'comment')]",
                        # "모두 보기" 버튼
                        "//span[contains(text(), '모두 보기')]",
                        "//span[contains(text(), 'View more') or contains(text(), 'See all')]",
                        "//div[contains(@class, 'comment')]//span[contains(text(), '더')]",
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
                                        time.sleep(1.5)
                                except:
                                    pass
                            if clicked >= 2:
                                break
                        except:
                            pass

                    if clicked > 0:
                        logger.info(f"댓글 버튼 {clicked}회 클릭")
                        time.sleep(2)

                    # === 2단계: 댓글 영역으로 스크롤 및 추가 로딩 ===
                    try:
                        # 페이지 하단으로 스크롤하여 댓글 로드
                        for i in range(5):
                            self.driver.execute_script("window.scrollBy(0, 500);")
                            time.sleep(0.8)

                        # "더 보기" 또는 "이전 댓글 보기" 클릭
                        more_selectors = [
                            "//span[contains(text(), '이전 댓글')]",
                            "//span[contains(text(), 'previous') or contains(text(), 'Previous')]",
                            "//div[@role='button'][contains(., '더 보기') or contains(., 'more')]",
                        ]
                        for sel in more_selectors:
                            try:
                                more_btns = self.driver.find_elements(By.XPATH, sel)
                                for btn in more_btns[:2]:
                                    if btn.is_displayed():
                                        btn.click()
                                        time.sleep(1)
                            except:
                                pass
                    except:
                        pass

                    # 페이지 소스 새로 가져오기 (댓글 로드 후)
                    page_source = self.driver.page_source

                    # === 3단계: 페이지 소스에서 댓글 JSON 추출 (작성자 포함) ===
                    # 작성자와 텍스트를 함께 추출하는 패턴
                    comment_with_author_patterns = [
                        # Facebook 2024-2026 댓글 구조: author와 body가 함께 있는 패턴
                        r'"author":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        r'"commenter":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}[^}]*"body":\{"text":"((?:[^"\\]|\\.)*)"\}',
                        # 역순 패턴
                        r'"body":\{"text":"((?:[^"\\]|\\.)*)"\}[^}]*"author":\{[^}]*"name":"((?:[^"\\]|\\.)*)"\}',
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

                                if text and len(text) > 5 and not any(c.get('text') == text[:200] for c in comments_list):
                                    if not any(skip in text.lower() for skip in ['좋아요', 'like', 'reply', '답글', '공유', 'share']):
                                        comments_list.append({
                                            "author": author if author else "user",
                                            "text": text[:200],
                                            "likes": 0
                                        })
                        if len(comments_list) >= 10:
                            break

                    # 작성자+텍스트 패턴 실패시, 텍스트만 추출하고 주변에서 작성자 찾기
                    if len(comments_list) < 5:
                        comment_patterns = [
                            r'"body":\{"text":"((?:[^"\\]|\\.)*)"}',
                            r'"comment_body":\{"text":"((?:[^"\\]|\\.)*)"}',
                        ]
                        for pattern in comment_patterns:
                            matches = re.findall(pattern, page_source)
                            for match in matches[:15]:
                                if match and len(match) > 5:
                                    text = decode_unicode_escapes(match)
                                    if text and len(text) > 5 and not any(c.get('text') == text[:200] for c in comments_list):
                                        # UI 텍스트 필터링
                                        if not any(skip in text.lower() for skip in ['좋아요', 'like', 'reply', '답글', '공유', 'share']):
                                            # 텍스트 위치에서 작성자 찾기 시도
                                            text_pos = page_source.find(f'"{match}"')
                                            author_name = "user"
                                            if text_pos > 0:
                                                # 앞쪽 500자에서 author name 찾기
                                                nearby = page_source[max(0, text_pos-500):text_pos]
                                                author_match = re.search(r'"name":"((?:[^"\\]|\\.){1,50})"', nearby)
                                                if author_match:
                                                    potential_author = decode_unicode_escapes(author_match.group(1))
                                                    # 유효한 작성자명인지 확인
                                                    if potential_author and len(potential_author) > 1 and len(potential_author) < 50:
                                                        if not any(skip in potential_author.lower() for skip in ['facebook', 'video', 'comment', 'like']):
                                                            author_name = potential_author

                                            comments_list.append({
                                                "author": author_name,
                                                "text": text[:200],
                                                "likes": 0
                                            })
                            if len(comments_list) >= 10:
                                break

                    # === 4단계: DOM에서 댓글 요소 추출 (fallback) ===
                    if len(comments_list) < 5:
                        # 먼저 댓글 작성자 목록 추출 시도
                        dom_author_names = []
                        try:
                            author_selectors = [
                                "//div[contains(@data-testid, 'comment')]//a[contains(@href, '/profile') or contains(@href, '/user')]//span",
                                "//div[contains(@aria-label, 'omment')]//a//span[@dir='auto']",
                            ]
                            for sel in author_selectors:
                                author_elems = self.driver.find_elements(By.XPATH, sel)
                                for ae in author_elems[:20]:
                                    try:
                                        name = ae.text.strip()
                                        if name and len(name) > 1 and len(name) < 50:
                                            if not any(skip in name.lower() for skip in ['facebook', 'video', 'comment', 'like', '좋아요', '답글']):
                                                dom_author_names.append(name)
                                    except:
                                        pass
                        except:
                            pass

                        dom_selectors = [
                            # Facebook 2024-2026 댓글 구조
                            "//div[contains(@data-testid, 'comment')]//span[string-length(text()) > 10]",
                            "//div[@aria-label='댓글' or @aria-label='Comment']//div//span[string-length(text()) > 15]",
                            "//ul//li//div//span[string-length(text()) > 15 and string-length(text()) < 300]",
                            # 일반 텍스트 패턴
                            "//div[contains(@class, 'comment')]//span[string-length(text()) > 10]",
                        ]

                        author_idx = 0
                        for selector in dom_selectors:
                            if len(comments_list) >= 10:
                                break
                            try:
                                elements = self.driver.find_elements(By.XPATH, selector)
                                for elem in elements[:15]:
                                    try:
                                        text = elem.text.strip()
                                        if text and len(text) > 10 and len(text) < 300:
                                            # UI 텍스트 필터링
                                            if not any(skip in text.lower() for skip in ['좋아요', 'like', 'reply', '답글', '공유', 'share', '시간', 'hour', 'day', '분', 'min']):
                                                if not any(c.get('text') == text[:200] for c in comments_list):
                                                    # 작성자 이름 할당 (있으면)
                                                    author = "user"
                                                    if author_idx < len(dom_author_names):
                                                        author = dom_author_names[author_idx]
                                                        author_idx += 1

                                                    comments_list.append({
                                                        "author": author,
                                                        "text": decode_unicode_escapes(text[:200]),
                                                        "likes": 0
                                                    })
                                                    if len(comments_list) >= 10:
                                                        break
                                    except:
                                        continue
                            except:
                                continue

                    if comments_list:
                        result["comments_list"] = comments_list
                        logger.info(f"Facebook 댓글 {len(comments_list)}개 수집")
                except Exception as ce:
                    logger.debug(f"Facebook 댓글 추출 실패: {ce}")

            # JSON에서 engagement를 찾았으면 DOM 파싱 스킵
            if result["likes"] > 0 or result["comments"] > 0:
                logger.info("JSON 데이터 추출 성공")
                return result

            # === DOM 기반 engagement 데이터 추출 (fallback) ===
            result["likes"] = self._extract_reactions(self.driver)
            result["comments"] = self._extract_comments_count(self.driver)
            result["shares"] = self._extract_shares_count(self.driver)
            result["views"] = self._extract_views_count(self.driver)

            logger.info(
                f"데이터 추출 완료: likes={result['likes']}, "
                f"comments={result['comments']}, shares={result['shares']}"
            )
            return result

        except TimeoutException:
            logger.error(f"페이지 로드 타임아웃: {url}")
            result["error"] = "timeout"
            return result
        except Exception as e:
            logger.error(f"데이터 추출 중 오류: {e}")
            result["error"] = str(e)
            return result

    def _try_javascript_extraction(self, result: dict) -> None:
        """
        JavaScript를 통한 데이터 추출 시도

        Args:
            result: 결과 딕셔너리 (업데이트됨)
        """
        try:
            page_source = self.driver.page_source

            # 반응 수 추출 (다양한 패턴 시도)
            reaction_patterns = [
                # 2025-2026년 Facebook Reel/Video 패턴
                r'"reaction_count_reduced":"([\d,\.KMkm]+)"',
                r'"video_reaction_count":(\d+)',
                r'"ufi_reaction_count":(\d+)',
                # 2026년 새 패턴 - i18n_reaction_count (JSON escape 버전)
                r'\"i18n_reaction_count\":\"([\d,\.KMkm]+)\"',
                r'"i18n_reaction_count":"([\d,\.KMkm]+)"',
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
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    # 가장 큰 값 사용 (여러 값이 있을 경우) - K/M 표기 지원
                    parsed_counts = [self._parse_count(str(m)) for m in matches]
                    max_count = max(parsed_counts) if parsed_counts else 0
                    if max_count > 0:
                        result["likes"] = max_count
                        logger.info(f"JSON에서 반응 수 추출: {max_count}")
                        break

            # 댓글 수 추출 (첫 번째 유효한 값 사용 - max 사용 시 잘못된 값 선택 위험)
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
                match = re.search(pattern, page_source, re.IGNORECASE)
                if match:
                    count = self._parse_count(str(match.group(1)))
                    # 검증: 댓글 수가 100만 이상이면 의심스러움 (일반적으로 댓글은 좋아요보다 훨씬 적음)
                    if count > 0 and count < 1000000:
                        result["comments"] = count
                        logger.info(f"JSON에서 댓글 수 추출: {count}")
                        break
                    elif count >= 1000000:
                        logger.warning(f"비정상적으로 큰 댓글 수 무시: {count}")

            # 공유 수 추출
            share_patterns = [
                r'\"share_count\":\{\"count\":(\d+)',
                r'"share_count"\s*:\s*\{"count"\s*:\s*(\d+)',
                r'"sharecount"\s*:\s*(\d+)',
            ]
            for pattern in share_patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    max_count = max(int(m) for m in matches)
                    if max_count > 0:
                        result["shares"] = max_count
                        logger.info(f"JSON에서 공유 수 추출: {max_count}")
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

        # 1. API 방식 우선 시도 (쿠키 인증 포함)
        if self.use_api and self.session:
            logger.info("requests API로 크롤링 시도...")
            result = self._crawl_via_api(url)
            if result and (result.get('likes', 0) > 0 or result.get('author')):
                logger.info(f"API 크롤링 성공: likes={result.get('likes')}, author={result.get('author')}")
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
        return self._sanitize_result(self._extract_post_data_from_page(url))

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
