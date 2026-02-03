"""
인스타그램(Instagram) 크롤러 모듈

requests 기반 API 크롤링 + Selenium fallback
- Instagram GraphQL API를 통한 데이터 수집
- Selenium 없이 서버리스 환경에서 동작
- 좋아요, 댓글, 조회수, 작성자, 캡션 수집

Streamlit Cloud 호환:
- requests + GraphQL API 우선 사용
- Selenium fallback 지원 (로컬 환경용)
"""

import codecs
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse

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
        # \\uXXXX 형식을 실제 이스케이프로 변환 후 디코딩
        try:
            decoded = text.encode('utf-8').decode('unicode_escape')
            # surrogate pair가 있으면 UTF-16으로 재인코딩하여 해결
            decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
        except (UnicodeDecodeError, UnicodeEncodeError):
            # fallback: 수동 변환
            def replace_unicode(match):
                return chr(int(match.group(1), 16))
            decoded = re.sub(r'\\u([0-9a-fA-F]{4})', replace_unicode, text)
            # surrogate pair 처리
            try:
                decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
            except Exception:
                pass

        # 추가 이스케이프 처리
        decoded = decoded.replace('\\n', '\n').replace('\\r', '\r')
        decoded = decoded.replace('\\t', '\t').replace('\\"', '"')
        decoded = decoded.replace('\\/', '/')
        return decoded
    except Exception:
        return text

# httpx for better async support (optional)
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

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
    HAS_UNDETECTED = False  # ChromeDriver 버전 충돌 방지
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


class InstagramCrawlerError(Exception):
    """인스타그램 크롤러 기본 예외"""
    pass


class InstagramLoginError(InstagramCrawlerError):
    """로그인 관련 예외"""
    pass


class InstagramPostLoadError(InstagramCrawlerError):
    """게시물 로드 관련 예외"""
    pass


class InstagramRateLimitError(InstagramCrawlerError):
    """Rate Limit 관련 예외"""
    pass


class InstagramCrawler:
    """
    인스타그램 크롤러 클래스

    requests 기반 API 크롤링을 우선 사용 (Selenium 없이 동작)
    실패 시 Selenium fallback으로 전환
    """

    # 인스타그램 기본 URL
    BASE_URL = "https://www.instagram.com"

    # 기본 설정
    DEFAULT_TIMEOUT = 45  # 30 -> 45초로 증가 (Instagram 봇 탐지 대응)
    PAGE_LOAD_WAIT = 5    # 3 -> 5초로 증가
    LOGIN_TIMEOUT = 60

    # 쿠키 파일 경로
    COOKIE_DIR = Path(__file__).parent.parent.parent / "data" / "cookies"
    COOKIE_FILE = COOKIE_DIR / "instagram_cookies.json"

    # Rate Limiting 설정
    MIN_REQUEST_DELAY = 3.0
    MAX_REQUEST_DELAY = 7.0

    # Instagram API 헤더
    API_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.instagram.com/',
        'X-IG-App-ID': '936619743392459',  # Instagram Web App ID
    }

    def __init__(
        self,
        headless: bool = False,
        chrome_driver_path: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        cookie_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_api: bool = True,  # API 방식 우선 사용
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
            username: 인스타그램 사용자명 (로그인 필요시)
            password: 인스타그램 비밀번호 (로그인 필요시)
            use_api: requests 기반 API 방식 우선 사용 여부
            collect_comments: 댓글 내용 수집 여부
            max_comments: 수집할 최대 댓글 수
        """
        self.headless = headless
        self.chrome_driver_path = chrome_driver_path
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.username = username or os.getenv("INSTAGRAM_USERNAME")
        self.password = password or os.getenv("INSTAGRAM_PASSWORD")
        self.use_api = use_api
        self.collect_comments = collect_comments
        self.max_comments = max_comments

        self.driver: Optional[webdriver.Chrome] = None
        self.is_logged_in = False
        self.session: Optional[requests.Session] = None

        # 쿠키 디렉토리 생성
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

        # requests 세션 초기화
        if use_api:
            self._init_session()

        logger.info(f"InstagramCrawler 초기화 완료 (use_api={use_api}, collect_comments={collect_comments})")

    def _init_session(self) -> None:
        """requests 세션 초기화 및 쿠키 로드"""
        self.session = requests.Session()
        self.session.headers.update(self.API_HEADERS)

        # 저장된 쿠키 파일에서 쿠키 로드
        if self.cookie_file.exists():
            try:
                with open(self.cookie_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)

                for cookie in cookies:
                    # requests.Session에 쿠키 추가
                    cookie_name = cookie.get("name")
                    cookie_value = cookie.get("value")
                    cookie_domain = cookie.get("domain", ".instagram.com")

                    if cookie_name and cookie_value:
                        self.session.cookies.set(
                            cookie_name,
                            cookie_value,
                            domain=cookie_domain.lstrip("."),
                            path=cookie.get("path", "/")
                        )

                # 주요 쿠키 확인 로깅
                important_cookies = ["sessionid", "ds_user_id", "csrftoken"]
                found_cookies = [c for c in important_cookies if c in self.session.cookies.keys()]
                logger.info(f"쿠키 로드 완료: {found_cookies}")

                # csrftoken이 있으면 헤더에도 추가
                csrftoken = self.session.cookies.get("csrftoken")
                if csrftoken:
                    self.session.headers["X-CSRFToken"] = csrftoken

            except Exception as e:
                logger.warning(f"쿠키 파일 로드 실패: {e}")

    def _crawl_via_api(self, url: str) -> Optional[Dict[str, Any]]:
        """
        requests를 통한 API 기반 크롤링

        Instagram 페이지의 HTML에서 embedded JSON 데이터 추출

        Args:
            url: Instagram 게시물 URL

        Returns:
            게시물 데이터 또는 None
        """
        if not self.session:
            return None

        try:
            shortcode = self._extract_shortcode_from_url(url)
            if not shortcode:
                logger.warning("shortcode를 추출할 수 없습니다.")
                return None

            logger.info(f"requests API로 크롤링: shortcode={shortcode}")

            # 쿠키 상태 로깅 (디버깅용)
            cookie_names = list(self.session.cookies.keys())
            if cookie_names:
                logger.info(f"적용된 쿠키: {cookie_names}")
            else:
                logger.info("쿠키 없음 - 비로그인 상태로 시도")

            # 페이지 HTML 요청
            page_url = f"{self.BASE_URL}/p/{shortcode}/"
            response = self.session.get(page_url, timeout=self.timeout)

            if response.status_code != 200:
                logger.warning(f"페이지 요청 실패: {response.status_code}")
                # 401/403인 경우 쿠키 문제일 가능성
                if response.status_code in [401, 403]:
                    logger.warning("인증 거부 - 쿠키가 만료되었거나 유효하지 않을 수 있습니다")
                return None

            html = response.text

            # 1. script 태그에서 JSON 데이터 추출 시도
            result = self._extract_data_from_html(html, url, shortcode)
            if result and (result.get('likes', 0) > 0 or result.get('author')):
                return result

            # 2. GraphQL API 직접 호출 시도
            result = self._fetch_via_graphql(shortcode, url)
            if result:
                return result

            return None

        except Exception as e:
            logger.warning(f"API 크롤링 실패: {e}")
            return None

    def _extract_data_from_html(self, html: str, url: str, shortcode: str) -> Optional[Dict[str, Any]]:
        """
        HTML에서 embedded JSON 데이터 추출

        Args:
            html: 페이지 HTML
            url: 원본 URL
            shortcode: 게시물 shortcode

        Returns:
            게시물 데이터 또는 None
        """
        result = {
            "platform": "instagram",
            "url": url,
            "shortcode": shortcode,
            "author": None,
            "caption": None,
            "likes": 0,
            "comments": 0,
            "views": None,
            "comments_list": [],  # 댓글 내용 리스트
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # 방법 1: window._sharedData에서 추출
            shared_data_match = re.search(r'window\._sharedData\s*=\s*({.+?});</script>', html)
            if shared_data_match:
                data = json.loads(shared_data_match.group(1))
                media = self._find_media_in_shared_data(data)
                if media:
                    # shortcode 일치 검증 (다른 게시물 데이터 혼입 방지)
                    media_sc = media.get('shortcode', '')
                    if media_sc and media_sc != shortcode:
                        logger.warning(f"sharedData shortcode 불일치: 요청={shortcode}, HTML={media_sc}")
                    else:
                        self._populate_result_from_media(result, media)
                        if result.get('likes', 0) > 0 or result.get('author'):
                            return result

            # 방법 2: __additionalDataLoaded에서 추출
            additional_match = re.search(r'window\.__additionalDataLoaded\([^,]+,\s*({.+?})\);</script>', html)
            if additional_match:
                data = json.loads(additional_match.group(1))
                media = self._find_media_in_additional_data(data)
                if media:
                    # shortcode 일치 검증
                    media_sc = media.get('shortcode', '')
                    if media_sc and media_sc != shortcode:
                        logger.warning(f"additionalData shortcode 불일치: 요청={shortcode}, HTML={media_sc}")
                    else:
                        self._populate_result_from_media(result, media)
                        if result.get('likes', 0) > 0 or result.get('author'):
                            return result

            # 방법 3: 정규식으로 직접 추출 (shortcode 스코핑 적용)
            # HTML에서 타겟 shortcode 주변만 검색하여 다른 게시물 데이터 혼입 방지
            search_html = html
            if shortcode:
                sc_pos = html.find(f'"{shortcode}"')
                if sc_pos == -1:
                    sc_pos = html.find(shortcode)
                if sc_pos >= 0:
                    start = max(0, sc_pos - 5000)
                    end = min(len(html), sc_pos + 5000)
                    search_html = html[start:end]
                    logger.debug(f"regex 스코핑: shortcode '{shortcode}' 주변 {end - start} chars")

            patterns = {
                'likes': [
                    r'"edge_media_preview_like"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
                    r'"like_count"\s*:\s*(\d+)',
                    r'"edge_liked_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
                ],
                'comments': [
                    r'"edge_media_preview_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
                    r'"comment_count"\s*:\s*(\d+)',
                    r'"edge_media_to_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
                ],
                'views': [
                    r'"video_view_count"\s*:\s*(\d+)',
                    r'"view_count"\s*:\s*(\d+)',
                ],
                'author': [
                    r'"username"\s*:\s*"([^"]+)"',
                    r'"owner"\s*:\s*\{[^}]*"username"\s*:\s*"([^"]+)"',
                ],
            }

            for key, pattern_list in patterns.items():
                for pattern in pattern_list:
                    match = re.search(pattern, search_html)
                    if match:
                        value = match.group(1)
                        if key in ['likes', 'comments', 'views']:
                            result[key] = int(value)
                        else:
                            result[key] = value
                        break

            return result if result.get('likes', 0) > 0 or result.get('author') else None

        except Exception as e:
            logger.debug(f"HTML 데이터 추출 실패: {e}")
            return None

    def _find_media_in_shared_data(self, data: dict) -> Optional[dict]:
        """sharedData에서 미디어 데이터 찾기"""
        try:
            return data.get('entry_data', {}).get('PostPage', [{}])[0].get('graphql', {}).get('shortcode_media')
        except (KeyError, IndexError):
            return None

    def _find_media_in_additional_data(self, data: dict) -> Optional[dict]:
        """additionalData에서 미디어 데이터 찾기"""
        try:
            return data.get('graphql', {}).get('shortcode_media') or data.get('shortcode_media')
        except (KeyError, TypeError):
            return None

    def _populate_result_from_media(self, result: dict, media: dict) -> None:
        """미디어 데이터로 결과 채우기"""
        try:
            # 좋아요 수
            if 'edge_media_preview_like' in media:
                result['likes'] = media['edge_media_preview_like'].get('count', 0)
            elif 'edge_liked_by' in media:
                result['likes'] = media['edge_liked_by'].get('count', 0)

            # 댓글 수
            if 'edge_media_preview_comment' in media:
                result['comments'] = media['edge_media_preview_comment'].get('count', 0)
            elif 'edge_media_to_comment' in media:
                result['comments'] = media['edge_media_to_comment'].get('count', 0)

            # 조회수 (동영상)
            result['views'] = media.get('video_view_count')

            # 작성자
            owner = media.get('owner', {})
            result['author'] = owner.get('username')

            # 캡션
            edges = media.get('edge_media_to_caption', {}).get('edges', [])
            if edges:
                result['caption'] = edges[0].get('node', {}).get('text', '')[:500]

            # 썸네일 이미지 추출
            thumbnail = media.get('display_url') or media.get('thumbnail_src')
            if not thumbnail:
                # display_resources에서 최적 크기 선택
                resources = media.get('display_resources', [])
                if resources:
                    # 중간 크기 선택 (너무 크지 않은 것)
                    for res in resources:
                        if res.get('config_width', 0) <= 640:
                            thumbnail = res.get('src')
                            break
                    if not thumbnail:
                        thumbnail = resources[0].get('src')
            result['thumbnail'] = thumbnail

            # 댓글 내용 추출
            comments_list = []
            if self.collect_comments:
                comment_edges = []
                if 'edge_media_to_parent_comment' in media:
                    comment_edges = media['edge_media_to_parent_comment'].get('edges', [])
                elif 'edge_media_to_comment' in media:
                    comment_edges = media['edge_media_to_comment'].get('edges', [])
                elif 'edge_media_preview_comment' in media:
                    comment_edges = media['edge_media_preview_comment'].get('edges', [])

                for comment_edge in comment_edges[:self.max_comments]:
                    comment_node = comment_edge.get('node', {})
                    comment_text = comment_node.get('text', '')
                    comment_author = comment_node.get('owner', {}).get('username', '익명')
                    comment_likes = comment_node.get('edge_liked_by', {}).get('count', 0)

                    if comment_text:
                        # 유니코드 디코딩
                        comment_text = decode_unicode_escapes(comment_text)
                        comments_list.append({
                            "author": comment_author,
                            "text": comment_text[:200],  # 최대 200자
                            "likes": comment_likes,
                        })

                if comments_list:
                    logger.info(f"Instagram 댓글 {len(comments_list)}개 수집됨")

            result['comments_list'] = comments_list

        except Exception as e:
            logger.debug(f"미디어 데이터 파싱 실패: {e}")
            result['comments_list'] = []

    def _fetch_via_graphql(self, shortcode: str, url: str) -> Optional[Dict[str, Any]]:
        """
        GraphQL API로 직접 데이터 가져오기

        Args:
            shortcode: 게시물 shortcode
            url: 원본 URL

        Returns:
            게시물 데이터 또는 None
        """
        try:
            # GraphQL 쿼리 (Instagram public API)
            query_hash = "b3055c01b4b222b8a47dc12b090e4e64"  # media query hash
            variables = json.dumps({
                "shortcode": shortcode,
                "child_comment_count": 3,
                "fetch_comment_count": 40,
                "parent_comment_count": 24,
                "has_threaded_comments": True
            })

            api_url = f"{self.BASE_URL}/graphql/query/"
            params = {
                "query_hash": query_hash,
                "variables": variables
            }

            response = self.session.get(api_url, params=params, timeout=self.timeout)

            if response.status_code == 200:
                data = response.json()
                media = data.get('data', {}).get('shortcode_media')
                if media:
                    result = {
                        "platform": "instagram",
                        "url": url,
                        "shortcode": shortcode,
                        "author": None,
                        "caption": None,
                        "likes": 0,
                        "comments": 0,
                        "views": None,
                        "crawled_at": datetime.now().isoformat(),
                    }
                    self._populate_result_from_media(result, media)
                    return result

        except Exception as e:
            logger.debug(f"GraphQL API 호출 실패: {e}")

        return None

    def _random_delay(self, min_delay: float = None, max_delay: float = None) -> None:
        """
        랜덤 딜레이 (봇 탐지 우회)

        Args:
            min_delay: 최소 딜레이 (초)
            max_delay: 최대 딜레이 (초)
        """
        min_d = min_delay or self.MIN_REQUEST_DELAY
        max_d = max_delay or self.MAX_REQUEST_DELAY
        delay = random.uniform(min_d, max_d)
        time.sleep(delay)

    def _create_driver(self) -> webdriver.Chrome:
        """
        Chrome WebDriver 생성 (Stealth 모드 적용)

        Returns:
            Chrome WebDriver 인스턴스
        """
        # 로컬 환경에서 undetected_chromedriver 사용 (Instagram 봇 탐지 우회에 효과적)
        if HAS_UNDETECTED and not IS_CLOUD:
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
            logger.info(f"Chrome WebDriver 생성 완료 (undetected, ver={chrome_ver})")
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
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")

        # === Stealth 모드 설정 (봇 탐지 우회) ===
        options.add_argument("--disable-blink-features=AutomationControlled")

        # experimental_option은 로컬에서만 사용 (Cloud에서 호환성 문제)
        if not IS_CLOUD:
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

        # 추가 Stealth 옵션
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins-discovery")
        options.add_argument("--disable-infobars")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--ignore-ssl-errors")

        # 실제 브라우저처럼 보이도록 설정
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")

        # User-Agent 설정 (최신 Chrome 버전으로)
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        ]
        options.add_argument(f"user-agent={random.choice(user_agents)}")

        # 언어 설정
        options.add_argument("--lang=ko-KR,ko,en-US,en")

        # Prefs 설정 (로컬에서만)
        if not IS_CLOUD:
            prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                "profile.default_content_settings.popups": 0,
            }
            options.add_experimental_option("prefs", prefs)

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

            # === Stealth JavaScript 삽입 ===
            stealth_js = """
                // navigator.webdriver 제거
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Chrome 관련 속성 숨기기
                window.chrome = {
                    runtime: {}
                };

                // Permissions API 수정
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // Plugins 배열 수정
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });

                // Languages 설정
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ko-KR', 'ko', 'en-US', 'en']
                });

                // Hardware Concurrency
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8
                });

                // DeviceMemory
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8
                });

                // WebGL Vendor & Renderer
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) {
                        return 'Intel Inc.';
                    }
                    if (parameter === 37446) {
                        return 'Intel Iris OpenGL Engine';
                    }
                    return getParameter.call(this, parameter);
                };
            """

            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": stealth_js}
            )

            # 추가 CDP 명령
            driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {
                    "userAgent": driver.execute_script("return navigator.userAgent"),
                    "platform": "Win32",
                    "acceptLanguage": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
                }
            )

            logger.info("Chrome WebDriver 생성 완료 (Stealth 모드)")
            return driver

        except WebDriverException as e:
            logger.error(f"WebDriver 생성 실패: {e}")
            raise InstagramCrawlerError(f"Chrome WebDriver를 생성할 수 없습니다: {e}")

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
            # 먼저 도메인에 접속해야 쿠키 설정 가능
            self.driver.get(self.BASE_URL)
            self._random_delay(2, 4)

            with open(self.cookie_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)

            for cookie in cookies:
                # 일부 쿠키 속성 정리
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
            self.driver.get(self.BASE_URL)
            self._random_delay(2, 4)

            # 로그인 관련 요소 확인
            try:
                # 로그인 버튼이 있으면 미로그인 상태
                login_btn = self.driver.find_element(
                    By.XPATH,
                    "//a[contains(@href, '/accounts/login')] | //button[contains(text(), 'Log in')] | //button[contains(text(), '로그인')]"
                )
                return False
            except NoSuchElementException:
                pass

            # 프로필 링크나 사용자 아바타 확인
            try:
                self.driver.find_element(
                    By.XPATH,
                    "//a[contains(@href, '/direct/')] | //span[@role='link' and contains(@class, 'avatar')] | //img[contains(@alt, 'profile')]"
                )
                logger.info("로그인 상태 확인됨")
                return True
            except NoSuchElementException:
                pass

            # 세션 쿠키 확인
            cookies = self.driver.get_cookies()
            for cookie in cookies:
                if cookie.get("name") == "sessionid" and cookie.get("value"):
                    logger.info("로그인 세션 쿠키 발견")
                    return True

            return False

        except Exception as e:
            logger.warning(f"로그인 상태 확인 중 오류: {e}")
            return False

    def _handle_login_popup(self) -> None:
        """로그인 팝업 또는 쿠키 동의 처리"""
        try:
            # 쿠키 동의 버튼
            cookie_selectors = [
                "//button[contains(text(), 'Accept')]",
                "//button[contains(text(), '동의')]",
                "//button[contains(text(), 'Allow')]",
                "//button[contains(text(), '허용')]",
            ]
            for selector in cookie_selectors:
                try:
                    btn = self.driver.find_element(By.XPATH, selector)
                    btn.click()
                    self._random_delay(1, 2)
                    break
                except NoSuchElementException:
                    continue

            # 로그인 팝업 닫기
            close_selectors = [
                "//button[contains(@aria-label, 'Close')]",
                "//button[contains(@aria-label, '닫기')]",
                "//*[name()='svg' and @aria-label='Close']/..",
                "//div[@role='dialog']//button[1]",
            ]
            for selector in close_selectors:
                try:
                    btn = self.driver.find_element(By.XPATH, selector)
                    btn.click()
                    self._random_delay(1, 2)
                    break
                except NoSuchElementException:
                    continue

            # "Not Now" 버튼 처리
            not_now_selectors = [
                "//button[contains(text(), 'Not Now')]",
                "//button[contains(text(), '나중에')]",
                "//a[contains(text(), 'Not Now')]",
            ]
            for selector in not_now_selectors:
                try:
                    btn = self.driver.find_element(By.XPATH, selector)
                    btn.click()
                    self._random_delay(1, 2)
                    break
                except NoSuchElementException:
                    continue

        except Exception as e:
            logger.debug(f"팝업 처리 중 오류 (무시): {e}")

    def login(self, force_login: bool = False) -> bool:
        """
        인스타그램 로그인

        저장된 쿠키가 있으면 재사용, 없으면 계정 로그인 수행

        Args:
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
                    logger.info("저장된 쿠키가 만료됨. 계정 로그인 필요.")

        # 계정 정보 확인
        if not self.username or not self.password:
            logger.warning("로그인 자격 증명이 없습니다. 비로그인 모드로 진행.")
            return False

        # 로그인 페이지로 이동
        logger.info("로그인 페이지로 이동...")

        try:
            self.driver.get(f"{self.BASE_URL}/accounts/login/")
            self._random_delay(3, 5)

            self._handle_login_popup()

            # 사용자명 입력
            username_input = WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//input[@name='username' or @aria-label='Phone number, username, or email']"
                ))
            )

            # 인간처럼 타이핑
            username_input.clear()
            for char in self.username:
                username_input.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            self._random_delay(0.5, 1)

            # 비밀번호 입력
            password_input = self.driver.find_element(
                By.XPATH,
                "//input[@name='password' or @aria-label='Password']"
            )
            password_input.clear()
            for char in self.password:
                password_input.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            self._random_delay(1, 2)

            # 로그인 버튼 클릭
            login_button = self.driver.find_element(
                By.XPATH,
                "//button[@type='submit'] | //button[contains(text(), 'Log in')] | //button[contains(text(), '로그인')]"
            )
            login_button.click()

            self._random_delay(3, 5)

            # 로그인 성공 확인
            try:
                WebDriverWait(self.driver, 15).until(
                    lambda d: "login" not in d.current_url.lower()
                )
                logger.info("로그인 성공!")

                # 팝업 처리
                self._handle_login_popup()

                # 쿠키 저장
                self._save_cookies()
                self.is_logged_in = True
                return True

            except TimeoutException:
                # 에러 메시지 확인
                try:
                    error_elem = self.driver.find_element(
                        By.XPATH,
                        "//*[contains(@class, 'error')] | //*[@role='alert']"
                    )
                    logger.error(f"로그인 실패: {error_elem.text}")
                except NoSuchElementException:
                    logger.error("로그인 타임아웃")

                raise InstagramLoginError("로그인에 실패했습니다.")

        except InstagramLoginError:
            raise
        except Exception as e:
            logger.error(f"로그인 중 오류 발생: {e}")
            raise InstagramLoginError(f"로그인 실패: {e}")

    def _parse_count(self, text: str) -> int:
        """
        숫자 텍스트 파싱 (예: "1.2K" -> 1200, "1.5M" -> 1500000)

        Args:
            text: 숫자 텍스트

        Returns:
            정수 값
        """
        if not text:
            return 0

        text = text.strip().replace(",", "").replace(" ", "")

        # 숫자만 있는 경우
        if text.isdigit():
            return int(text)

        # K (천) 단위
        if "K" in text.upper():
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000)

        # M (백만) 단위
        if "M" in text.upper():
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000000)

        # B (10억) 단위
        if "B" in text.upper():
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000000000)

        # 만 단위 (한국어)
        if "만" in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 10000)

        # 그 외 숫자 추출
        num = re.search(r"[\d]+", text)
        if num:
            return int(num.group())

        return 0

    def _extract_shortcode_from_url(self, url: str) -> Optional[str]:
        """
        URL에서 게시물 shortcode 추출

        Args:
            url: 인스타그램 게시물 URL

        Returns:
            shortcode 또는 None
        """
        # 다양한 URL 패턴 처리
        # 예: instagram.com/p/ABC, instagram.com/username/p/ABC, instagram.com/reel/ABC
        patterns = [
            r"instagram\.com/(?:[^/]+/)?p/([A-Za-z0-9_-]+)",
            r"instagram\.com/(?:[^/]+/)?reel/([A-Za-z0-9_-]+)",
            r"instagram\.com/(?:[^/]+/)?tv/([A-Za-z0-9_-]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def _extract_from_embed_page(self, shortcode: str, result: dict) -> bool:
        """
        Embed 페이지에서 데이터 추출 (fallback 방법)

        Instagram embed 페이지는 더 단순한 HTML 구조를 가지고 있어서
        데이터 추출이 더 쉬움

        Args:
            shortcode: 게시물 shortcode
            result: 결과 딕셔너리 (업데이트됨)

        Returns:
            True if data extracted successfully, False otherwise
        """
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
        logger.info(f"Embed 페이지 fallback 시도: {embed_url}")

        try:
            # Embed 페이지 로드
            self.driver.get(embed_url)
            self._random_delay(3, 5)

            page_source = self.driver.page_source

            # === Embed 페이지 JSON 데이터 추출 ===
            # embed 페이지에는 보통 __additionalDataLoaded 또는 window.__initialData 가 있음

            # 방법 1: Embed 페이지 내 JSON 패턴
            json_patterns = [
                r'window\.__additionalDataLoaded\s*\(\s*[\'"][^\'"]+[\'"]\s*,\s*(\{.*?\})\s*\)\s*;',
                r'"edge_liked_by":\{"count":(\d+)',
                r'"like_count":(\d+)',
                r'"edge_media_preview_like":\{"count":(\d+)',
            ]

            # 좋아요 수 - 2025/2026 Instagram 구조 대응
            like_patterns = [
                r'"edge_liked_by":\{"count":(\d+)',
                r'"like_count":(\d+)',
                r'"edge_media_preview_like":\{"count":(\d+)',
                r'좋아요\s*([\d,]+)\s*개',  # 한국어: "좋아요 666,826개"
                r'([\d,]+)\s*likes?',    # 영어: "666,826 likes"
            ]
            for pattern in like_patterns:
                match = re.search(pattern, page_source)
                if match:
                    likes_str = match.group(1).replace(',', '').strip()
                    if likes_str and likes_str.isdigit():
                        result["likes"] = int(likes_str)
                        logger.info(f"Embed에서 좋아요 수 추출: {result['likes']}")
                        break

            # 댓글 수 - 2025/2026 Instagram 구조 대응
            comment_patterns = [
                r'"edge_media_to_comment":\{"count":(\d+)',
                r'"comment_count":(\d+)',
                r'"edge_media_to_parent_comment":\{"count":(\d+)',
                r'댓글\s*([\d,]+)\s*개\s*모두\s*보기',  # 한국어: "댓글 2,804개 모두 보기"
                r'View all\s*([\d,]+)\s*comments',   # 영어
                r'([\d,]+)\s*comments?',
            ]
            for pattern in comment_patterns:
                match = re.search(pattern, page_source)
                if match:
                    comments_str = match.group(1).replace(',', '').strip()
                    if comments_str and comments_str.isdigit():
                        result["comments"] = int(comments_str)
                        logger.info(f"Embed에서 댓글 수 추출: {result['comments']}")
                        break

            # 조회수
            for pattern in [r'"video_view_count":(\d+)', r'"play_count":(\d+)', r'"view_count":(\d+)']:
                match = re.search(pattern, page_source)
                if match:
                    result["views"] = int(match.group(1))
                    logger.info(f"Embed JSON에서 조회수 추출: {result['views']}")
                    break

            # 작성자 - owner 컨텍스트에서 추출 (로그인 사용자가 아닌 포스트 작성자)
            author_patterns = [
                r'"owner":\s*\{[^}]*"username":\s*"([^"]+)"',  # owner 객체 내 username
                r'"user":\s*\{[^}]*"username":\s*"([^"]+)"',   # user 객체 내 username
                r'<a[^>]*class="[^"]*author[^"]*"[^>]*>([^<]+)</a>',  # embed의 author 링크
                r'data-owner-username="([^"]+)"',  # data 속성
            ]
            for pattern in author_patterns:
                match = re.search(pattern, page_source)
                if match:
                    username = match.group(1).strip()
                    # 유효한 사용자명인지 확인 (p, reel, tv, 로그인 사용자 등 제외)
                    excluded = ['p', 'reel', 'tv', 'explore', 'stories', 'houng8087']
                    if username and username not in excluded and len(username) > 1:
                        result["author"] = username
                        logger.info(f"Embed에서 작성자 추출: {result['author']}")
                        break

            # fallback: embed 페이지 링크에서 추출 (첫 번째 프로필 링크)
            if not result.get("author"):
                profile_match = re.search(r'href="https://www\.instagram\.com/([a-zA-Z0-9_.]+)/?"[^>]*>.*?</a>', page_source)
                if profile_match:
                    username = profile_match.group(1)
                    if username not in ['p', 'reel', 'tv', 'explore', 'stories', 'houng8087']:
                        result["author"] = username
                        logger.info(f"Embed 링크에서 작성자 추출: {result['author']}")

            # === 방법 2: DOM 파싱 (embed 페이지 구조) ===
            if result["likes"] == 0:
                try:
                    # Embed 페이지의 좋아요 버튼/텍스트
                    like_elements = self.driver.find_elements(
                        By.XPATH,
                        "//span[contains(@class, 'Likes') or contains(text(), 'like') or contains(text(), '좋아요')]"
                    )
                    for elem in like_elements:
                        text = elem.text.strip()
                        # "1,234 likes" 또는 숫자만 있는 경우
                        count_match = re.search(r'([\d,]+)', text)
                        if count_match:
                            count_str = count_match.group(1).replace(',', '').strip()
                            if count_str and count_str.isdigit():
                                count = int(count_str)
                                if count > 0:
                                    result["likes"] = count
                                    logger.info(f"Embed DOM에서 좋아요 추출: {count}")
                                    break
                except Exception as e:
                    logger.debug(f"Embed DOM 좋아요 추출 실패: {e}")

            # 조회수 DOM 파싱 (비디오/릴스)
            if result["views"] is None:
                try:
                    view_elements = self.driver.find_elements(
                        By.XPATH,
                        "//span[contains(text(), 'View') or contains(text(), 'view') or contains(text(), '조회')]"
                    )
                    for elem in view_elements:
                        text = elem.text.strip()
                        # "1,234 views" 패턴
                        count_match = re.search(r'([\d,]+)', text)
                        if count_match:
                            count_str = count_match.group(1).replace(',', '').strip()
                            if count_str and count_str.isdigit():
                                count = int(count_str)
                                if count > 0:
                                    result["views"] = count
                                    logger.info(f"Embed DOM에서 조회수 추출: {count}")
                                    break
                except Exception as e:
                    logger.debug(f"Embed DOM 조회수 추출 실패: {e}")

            # 성공 여부 판단
            if result["likes"] > 0 or result["comments"] > 0 or result["views"] is not None:
                logger.info("Embed 페이지에서 데이터 추출 성공")
                return True

            logger.warning("Embed 페이지에서도 데이터 추출 실패")
            return False

        except Exception as e:
            logger.error(f"Embed 페이지 추출 실패: {e}")
            return False

    def _extract_post_data_from_page(self, url: str) -> dict:
        """
        페이지에서 직접 게시물 데이터 추출 (DOM 파싱)

        Args:
            url: 게시물 URL

        Returns:
            게시물 데이터 딕셔너리
        """
        result = {
            "platform": "instagram",
            "url": url,
            "author": None,
            "title": None,  # 캡션 첫 줄
            "caption": None,  # 전체 캡션 (content 역할)
            "content": None,  # caption의 alias (호환성)
            "likes": 0,
            "comments": 0,
            "views": None,
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # SPA 캐시 완전 초기화 - 같은 작성자의 다른 게시물 데이터 혼입 방지
            try:
                # 1) 브라우저 캐시 및 스토리지 클리어 (CDP)
                self.driver.execute_cdp_cmd('Network.clearBrowserCache', {})
                self.driver.execute_script("window.sessionStorage.clear(); window.localStorage.clear();")
            except Exception:
                pass
            try:
                # 2) 빈 페이지로 이동하여 이전 페이지 DOM/JS 상태 완전 제거
                self.driver.get("about:blank")
                time.sleep(0.5)
            except Exception:
                pass

            # 게시물 페이지 로드
            self.driver.get(url)
            self._random_delay(5, 7)

            # URL 검증 - 중복 버그 방지
            current_url = self.driver.current_url
            expected_shortcode = self._extract_shortcode_from_url(url)
            if expected_shortcode and expected_shortcode not in current_url:
                logger.warning(f"URL 불일치 감지! 요청: {url}, 현재: {current_url}")
                # 강제 새 탭 수준의 리로드
                self.driver.execute_cdp_cmd('Network.clearBrowserCache', {})
                self.driver.get(url)
                self._random_delay(5, 7)
                current_url = self.driver.current_url
                if expected_shortcode not in current_url:
                    logger.error(f"URL 불일치 지속 - 요청: {expected_shortcode}, 현재: {current_url}")

            # 삭제/비공개 게시물 감지
            try:
                page_text = self.driver.page_source
                not_found_indicators = [
                    "이 페이지를 사용할 수 없습니다",
                    "Sorry, this page isn't available",
                    "The link you followed may be broken",
                    "링크가 잘못되었을 수 있습니다",
                    "This page isn't available",
                    "Content Unavailable",
                ]
                for indicator in not_found_indicators:
                    if indicator in page_text:
                        logger.warning(f"삭제/비공개 게시물 감지: {indicator}")
                        result["error"] = "게시물이 삭제되었거나 비공개 상태입니다"
                        result["error_type"] = "not_found"
                        return result
            except Exception:
                pass

            # 스크롤해서 동적 콘텐츠 로드 유도 (Instagram은 더 이상 article 태그 사용 안함)
            self.driver.execute_script("window.scrollTo(0, 500)")
            self._random_delay(2, 3)
            self.driver.execute_script("window.scrollTo(0, 0)")
            self._random_delay(3, 5)

            # 팝업 처리
            self._handle_login_popup()

            # === HTML에서 JSON 데이터 직접 추출 ===
            page_source = self.driver.page_source

            # 방법 1: 기존 JSON 패턴 (여전히 작동할 수 있음)
            like_patterns = [
                r'"like_count":(\d+)',
                r'"edge_media_preview_like":\{"count":(\d+)',
                r'"edge_liked_by":\{"count":(\d+)',
            ]
            for pattern in like_patterns:
                like_match = re.search(pattern, page_source)
                if like_match:
                    result["likes"] = int(like_match.group(1))
                    logger.info(f"JSON에서 좋아요 수 추출: {result['likes']}")
                    break

            comment_patterns = [
                r'"comment_count":(\d+)',
                r'"edge_media_to_comment":\{"count":(\d+)',
                r'"edge_media_to_parent_comment":\{"count":(\d+)',
            ]
            for pattern in comment_patterns:
                comment_match = re.search(pattern, page_source)
                if comment_match:
                    result["comments"] = int(comment_match.group(1))
                    logger.info(f"JSON에서 댓글 수 추출: {result['comments']}")
                    break

            view_patterns = [
                r'"play_count":(\d+)',
                r'"video_view_count":(\d+)',
                r'"view_count":(\d+)',
            ]
            for pattern in view_patterns:
                view_match = re.search(pattern, page_source)
                if view_match:
                    result["views"] = int(view_match.group(1))
                    logger.info(f"JSON에서 조회수 추출: {result['views']}")
                    break

            # 작성자 추출 (여러 패턴 시도)
            author_patterns = [
                r'"owner":\s*\{[^}]*"username":\s*"([^"]+)"',
                r'"user":\s*\{[^}]*"username":\s*"([^"]+)"',
                r'"username":"([^"]+)".*?"is_verified"',
            ]
            for pattern in author_patterns:
                author_match = re.search(pattern, page_source)
                if author_match:
                    result["author"] = author_match.group(1)
                    logger.info(f"JSON에서 작성자 추출: {result['author']}")
                    break

            # 캡션 추출 (JSON 패턴 - early return 전에 시도)
            caption_patterns = [
                r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"([^"]+)"',
                r'"caption":\{[^}]*"text":"([^"]+)"',
                r'"caption":"([^"]+)"',
                r'"accessibility_caption":"([^"]+)"',
            ]
            for pattern in caption_patterns:
                caption_match = re.search(pattern, page_source)
                if caption_match:
                    caption = caption_match.group(1)
                    caption = decode_unicode_escapes(caption)  # 유니코드 디코딩
                    if caption and len(caption) > 5:
                        result["caption"] = caption[:500]
                        logger.info(f"JSON에서 캡션 추출: {caption[:50]}...")
                        break

            # 썸네일 추출 (JSON 패턴)
            if not result.get("thumbnail"):
                thumbnail_patterns = [
                    r'"display_url":"([^"]+)"',
                    r'"thumbnail_src":"([^"]+)"',
                    r'"src":"(https://[^"]*instagram[^"]*\.jpg[^"]*)"',
                    r'"image_versions2":\{[^}]*"url":"([^"]+)"',
                ]
                for pattern in thumbnail_patterns:
                    thumb_match = re.search(pattern, page_source)
                    if thumb_match:
                        thumbnail = thumb_match.group(1)
                        # URL 이스케이프 처리
                        thumbnail = thumbnail.replace('\\u0026', '&').replace('\\/', '/')
                        result["thumbnail"] = thumbnail
                        logger.info(f"JSON에서 썸네일 추출: {thumbnail[:50]}...")
                        break

            # 댓글 내용 추출 (JSON 패턴)
            if self.collect_comments and not result.get("comments_list"):
                comments_list = []
                # edge_media_to_comment 또는 edge_media_to_parent_comment에서 추출
                comment_pattern = r'"edge_media_to_(?:parent_)?comment":\{"count":\d+,"edges":\[(.*?)\]'
                comment_match = re.search(comment_pattern, page_source, re.DOTALL)
                if comment_match:
                    edges_str = comment_match.group(1)
                    # 각 댓글 노드 추출
                    node_pattern = r'\{"node":\{[^}]*"text":"([^"]*)"[^}]*"owner":\{"[^}]*"username":"([^"]*)"'
                    for node_match in re.finditer(node_pattern, edges_str):
                        comment_text = decode_unicode_escapes(node_match.group(1))
                        comment_author = node_match.group(2)
                        if comment_text:
                            comments_list.append({
                                "author": comment_author,
                                "text": comment_text[:200],
                                "likes": 0
                            })
                        if len(comments_list) >= 10:
                            break
                if comments_list:
                    result["comments_list"] = comments_list
                    logger.info(f"JSON에서 댓글 {len(comments_list)}개 추출")

            # 댓글이 없으면 "댓글 더 보기" 버튼 클릭 후 DOM에서 추출 시도
            if self.collect_comments and not result.get("comments_list"):
                try:
                    # === 1. "모든 댓글 보기" 또는 "댓글 더 보기" 버튼 찾아서 클릭 ===
                    logger.info("Instagram 댓글 로딩을 위해 버튼 클릭 시도...")
                    load_more_selectors = [
                        # 2024-2026 Instagram 댓글 버튼 패턴들
                        "//span[contains(text(), '댓글') and contains(text(), '보기')]",
                        "//span[contains(text(), 'View all') and contains(text(), 'comment')]",
                        "//span[contains(text(), 'all') and contains(text(), 'comment')]",
                        "//button[contains(@class, 'comment')]//span",
                        "//a[contains(@href, '/comments/')]",
                        "//div[contains(@role, 'button')][contains(., 'comment')]",
                        # "더 보기" 패턴
                        "//span[contains(text(), '더 보기')]",
                        "//span[contains(text(), 'more')]",
                        "//button[contains(text(), 'Load')]",
                    ]

                    clicked_count = 0
                    for selector in load_more_selectors:
                        try:
                            buttons = self.driver.find_elements(By.XPATH, selector)
                            for btn in buttons[:3]:  # 최대 3번 클릭
                                try:
                                    if btn.is_displayed():
                                        btn.click()
                                        clicked_count += 1
                                        self._random_delay(1.5, 2.5)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    if clicked_count > 0:
                        logger.info(f"댓글 로드 버튼 {clicked_count}회 클릭")
                        self._random_delay(2, 3)

                    # === 2. 댓글 영역으로 스크롤 ===
                    try:
                        self.driver.execute_script("""
                            const commentSection = document.querySelector('ul[class*="comment"], div[class*="Comment"], section ul');
                            if (commentSection) {
                                commentSection.scrollIntoView({behavior: 'smooth', block: 'center'});
                            }
                        """)
                        self._random_delay(1, 2)
                    except Exception:
                        pass

                    # === 3. 추가 "더 보기" 버튼 클릭 (댓글 내 접힌 텍스트 펼치기) ===
                    try:
                        more_buttons = self.driver.find_elements(
                            By.XPATH,
                            "//button[contains(., '더 보기') or contains(., 'more')]"
                        )
                        for btn in more_buttons[:5]:
                            try:
                                if btn.is_displayed():
                                    btn.click()
                                    self._random_delay(0.5, 1)
                            except Exception:
                                pass
                    except Exception:
                        pass

                except Exception as click_err:
                    logger.debug(f"댓글 버튼 클릭 실패: {click_err}")

                # === 4. 페이지 소스에서 댓글 추출 (정규식) ===
                try:
                    comments_list = []
                    page_source = self.driver.page_source

                    # Instagram 2026 JSON 구조: "user":{"username":"작성자"}, ..., "text":"댓글"
                    # 패턴1: user.username → text (작성자명 포함)
                    user_text_pattern = r'"user"\s*:\s*\{[^}]*"username"\s*:\s*"([^"]+)"[^}]*\}[^}]*"text"\s*:\s*"((?:[^"\\]|\\.){5,200})"'
                    matches_with_user = re.findall(user_text_pattern, page_source)

                    seen_texts = set()
                    skip_patterns = ['좋아요', 'like', 'reply', '답글', 'follow', 'share',
                                     'comment', 'view', 'photo shared', 'video shared']

                    # 먼저 작성자명이 있는 댓글 추출
                    for username, text_raw in matches_with_user:
                        if len(comments_list) >= 10:
                            break
                        if text_raw and len(text_raw) > 3:
                            text = decode_unicode_escapes(text_raw)
                            if text in seen_texts:
                                continue
                            text_lower = text.lower()
                            if any(skip in text_lower for skip in skip_patterns):
                                continue
                            seen_texts.add(text)
                            comments_list.append({
                                "author": username,
                                "text": text[:200],
                                "likes": 0
                            })

                    # 작성자명 있는 댓글이 부족하면 text만 추출 (fallback)
                    if len(comments_list) < 10:
                        text_pattern = r'"text"\s*:\s*"((?:[^"\\]|\\.)+)"'
                        matches = re.findall(text_pattern, page_source)

                        for match in matches:
                            if len(comments_list) >= 10:
                                break
                            if match and len(match) > 15 and len(match) < 500:
                                text = decode_unicode_escapes(match)
                                if text in seen_texts:
                                    continue
                                text_lower = text.lower()
                                if any(skip in text_lower for skip in skip_patterns):
                                    continue
                                if text.startswith('@') or len(text) >= 20:
                                    seen_texts.add(text)
                                    comments_list.append({
                                        "author": "user",
                                        "text": text[:200],
                                        "likes": 0
                                    })

                    if comments_list:
                        result["comments_list"] = comments_list
                        logger.info(f"Instagram 댓글 {len(comments_list)}개 추출")

                except Exception as ce:
                    logger.debug(f"Instagram 댓글 추출 실패: {ce}")

            # JSON에서 engagement 데이터를 찾았으면 DOM 파싱 스킵
            if result["likes"] > 0 or result["comments"] > 0:
                logger.info("JSON 데이터 추출 성공, DOM 파싱 스킵")
                return result

            # 방법 2: span 요소에서 숫자 추출 (2026년 Instagram 구조)
            logger.info("JSON 패턴 없음, DOM 직접 파싱 시도...")
            try:
                # 좋아요 수 - "좋아요 N개", "N likes", "N만" 패턴
                like_elements = self.driver.find_elements(
                    By.XPATH,
                    "//span[contains(text(), '좋아요') or contains(text(), 'like')]"
                )
                for elem in like_elements:
                    text = elem.text.strip()
                    # _parse_count를 사용해 다양한 형식 처리 (66.7만, 1.2K, 1,234개 등)
                    count = self._parse_count(text)
                    if count > 0:
                        result["likes"] = count
                        logger.info(f"DOM에서 좋아요 수 추출: {count}")
                        break
            except Exception as e:
                logger.debug(f"DOM 좋아요 추출 실패: {e}")

            # 방법 3: section 내의 숫자 span 탐색 (2026년 Instagram 구조)
            if result["likes"] == 0:
                try:
                    sections = self.driver.find_elements(By.TAG_NAME, "section")
                    for section in sections:
                        spans = section.find_elements(By.TAG_NAME, "span")
                        for span in spans:
                            text = span.text.strip()
                            # "N만", "N.NK", "N,NNN", "N개" 등 다양한 숫자 패턴
                            if re.match(r'^[\d,.]+[만KMkm개]?$', text):
                                count = self._parse_count(text)
                                if count > 0 and result["likes"] == 0:
                                    result["likes"] = count
                                    logger.info(f"section span에서 좋아요 추출: {count}")
                                    break
                        if result["likes"] > 0:
                            break
                except Exception as e:
                    logger.debug(f"section 스캔 실패: {e}")

            self._random_delay(2, 3)

            # === 작성자 정보 ===
            author_selectors = [
                "//article//header//a[contains(@href, '/')]",
                "//header//span//a[contains(@href, '/')]",
                "//a[contains(@class, 'author')]",
                "//div[@role='dialog']//header//a",
            ]
            for selector in author_selectors:
                try:
                    author_elem = self.driver.find_element(By.XPATH, selector)
                    href = author_elem.get_attribute("href")
                    if href:
                        # URL에서 사용자명 추출
                        match = re.search(r"instagram\.com/([^/]+)", href)
                        if match:
                            result["author"] = match.group(1)
                            break
                    text = author_elem.text.strip()
                    if text and not text.startswith("http"):
                        result["author"] = text
                        break
                except (NoSuchElementException, StaleElementReferenceException):
                    continue

            # === 캡션 ===
            # 1. JSON 데이터에서 캡션 추출 시도 (가장 정확)
            try:
                page_source = self.driver.page_source
                caption_patterns = [
                    # edge_media_to_caption 구조
                    r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"([^"]+)"',
                    # caption 직접
                    r'"caption":\{[^}]*"text":"([^"]+)"',
                    r'"caption":"([^"]+)"',
                    # accessibility_caption (이미지 설명)
                    r'"accessibility_caption":"([^"]+)"',
                ]
                for pattern in caption_patterns:
                    match = re.search(pattern, page_source)
                    if match:
                        caption = match.group(1)
                        # 유니코드 이스케이프 디코딩
                        caption = decode_unicode_escapes(caption)
                        if caption and len(caption) > 5:
                            result["caption"] = caption[:500]
                            logger.info(f"JSON에서 캡션 추출: {caption[:50]}...")
                            break
            except Exception as e:
                logger.debug(f"JSON 캡션 추출 실패: {e}")

            # 2. DOM에서 캡션 추출 (fallback)
            if not result.get("caption"):
                caption_selectors = [
                    # 2025-2026 최신 인스타그램 구조
                    "//article//h1[contains(@class, '_ap3a')]",
                    "//article//span[contains(@class, '_ap3a')]",
                    "//div[contains(@class, 'x1cy8zhl')]//span[@dir='auto']",
                    "//div[@role='button'][contains(@class, 'x1i10hfl')]//span",
                    # 기존 패턴
                    "//article//div[contains(@class, 'Caption')]",
                    "//article//span[contains(@class, 'caption')]",
                    "//article//h1/following-sibling::span",
                    "//div[@role='dialog']//h1",
                    "//article//ul/li[1]//span[not(contains(@class, 'username'))]",
                    # 추가 패턴
                    "//article//div[@dir='auto']//span[string-length(text()) > 10]",
                    "//main//article//li//div//span",
                ]
                for selector in caption_selectors:
                    try:
                        caption_elem = self.driver.find_element(By.XPATH, selector)
                        caption_text = caption_elem.text.strip()
                        # 유효한 캡션인지 확인 (날짜, 좋아요 수 등 제외)
                        if caption_text and len(caption_text) > 5 and not caption_text.startswith('http') and not re.match(r'^\d+\s*(likes?|좋아요)', caption_text, re.IGNORECASE):
                            result["caption"] = caption_text[:500]  # 최대 500자
                            break
                    except (NoSuchElementException, StaleElementReferenceException):
                        continue

            # === 좋아요 수 ===
            like_selectors = [
                "//section//a[contains(@href, 'liked_by')]//span",
                "//section//button[contains(@class, 'like')]//span",
                "//button[contains(text(), 'like')]//parent::section//span",
                "//*[contains(text(), 'likes') or contains(text(), '좋아요')]",
                "//a[contains(text(), 'likes')]",
                "//span[contains(text(), ' likes')]",
            ]
            for selector in like_selectors:
                try:
                    like_elem = self.driver.find_element(By.XPATH, selector)
                    text = like_elem.text.strip()
                    count = self._parse_count(text)
                    if count > 0:
                        result["likes"] = count
                        break
                except (NoSuchElementException, StaleElementReferenceException):
                    continue

            # 좋아요 수를 못 찾은 경우 다른 방법 시도
            if result["likes"] == 0:
                try:
                    # aria-label에서 좋아요 수 추출
                    like_elem = self.driver.find_element(
                        By.XPATH,
                        "//*[contains(@aria-label, 'like') or contains(@aria-label, '좋아요')]"
                    )
                    label = like_elem.get_attribute("aria-label")
                    if label:
                        count = self._parse_count(label)
                        if count > 0:
                            result["likes"] = count
                except NoSuchElementException:
                    pass

            # === 조회수 (릴스/비디오인 경우) ===
            view_selectors = [
                "//span[contains(text(), 'views') or contains(text(), '조회')]",
                "//*[contains(@aria-label, 'view')]//span",
                "//span[contains(@class, 'views')]",
            ]
            for selector in view_selectors:
                try:
                    view_elem = self.driver.find_element(By.XPATH, selector)
                    text = view_elem.text.strip()
                    count = self._parse_count(text)
                    if count > 0:
                        result["views"] = count
                        break
                except (NoSuchElementException, StaleElementReferenceException):
                    continue

            # 비디오인지 확인하고 조회수 없으면 좋아요를 조회수로 간주할 수도 있음
            try:
                video_elem = self.driver.find_element(By.XPATH, "//video")
                if video_elem and result["views"] is None:
                    # 릴스는 조회수가 표시되는 경우가 많음
                    pass
            except NoSuchElementException:
                pass

            # === 댓글 수 ===
            comment_selectors = [
                "//a[contains(@href, '/comments/')]",
                "//button[contains(text(), 'comment') or contains(text(), '댓글')]//span",
                "//span[contains(text(), 'comment')]",
                "//*[contains(text(), 'View all') and contains(text(), 'comment')]",
            ]
            for selector in comment_selectors:
                try:
                    comment_elem = self.driver.find_element(By.XPATH, selector)
                    text = comment_elem.text.strip()
                    # "View all 123 comments" 형식 처리
                    count = self._parse_count(text)
                    if count > 0:
                        result["comments"] = count
                        break
                except (NoSuchElementException, StaleElementReferenceException):
                    continue

            # === JavaScript로 데이터 추출 시도 (백업) ===
            if result["likes"] == 0:
                try:
                    # 페이지 내 JSON 데이터 추출 시도
                    page_data = self.driver.execute_script("""
                        // window.__additionalDataLoaded 또는 유사한 전역 객체에서 데이터 추출
                        if (window._sharedData) {
                            return JSON.stringify(window._sharedData);
                        }
                        if (window.__additionalDataLoaded) {
                            return JSON.stringify(window.__additionalDataLoaded);
                        }
                        // 페이지 소스에서 JSON 데이터 찾기
                        var scripts = document.querySelectorAll('script[type="application/json"]');
                        for (var i = 0; i < scripts.length; i++) {
                            var text = scripts[i].textContent;
                            if (text.includes('edge_media_preview_like') || text.includes('like_count')) {
                                return text;
                            }
                        }
                        // script 태그 내용 검색
                        var allScripts = document.querySelectorAll('script');
                        for (var i = 0; i < allScripts.length; i++) {
                            var text = allScripts[i].textContent;
                            if (text.includes('"like_count"') || text.includes('"edge_liked_by"')) {
                                // JSON 부분만 추출
                                var start = text.indexOf('{');
                                var end = text.lastIndexOf('}');
                                if (start !== -1 && end !== -1) {
                                    return text.substring(start, end + 1);
                                }
                            }
                        }
                        return null;
                    """)

                    if page_data:
                        self._parse_json_data(page_data, result)

                except Exception as e:
                    logger.debug(f"JavaScript 데이터 추출 실패: {e}")

            # === Embed 페이지 Fallback ===
            # 모든 방법이 실패한 경우 embed 페이지에서 추출 시도
            if result["likes"] == 0 and result["comments"] == 0:
                logger.info("기본 페이지에서 데이터 추출 실패, Embed 페이지 fallback 시도...")
                shortcode = self._extract_shortcode_from_url(url)
                if shortcode:
                    self._extract_from_embed_page(shortcode, result)

            # caption에서 title과 content 설정
            if result.get("caption"):
                # title: 캡션의 첫 줄 (최대 100자)
                first_line = result["caption"].split('\n')[0].strip()
                result["title"] = first_line[:100] if len(first_line) > 100 else first_line
                # content: caption의 alias
                result["content"] = result["caption"]

            logger.info(
                f"데이터 추출 완료: author={result['author']}, "
                f"likes={result['likes']}, comments={result['comments']}, "
                f"views={result['views']}, caption={'있음' if result.get('caption') else '없음'}"
            )

            # 최종 안전장치: 모든 추출 시도 후에도 유효 데이터 없으면 에러 설정
            has_any_data = (
                result.get('author') or
                result.get('likes', 0) > 0 or
                result.get('comments', 0) > 0 or
                (result.get('views') or 0) > 0 or
                result.get('caption')
            )
            if not has_any_data and not result.get('error'):
                logger.warning("모든 추출 시도 실패 - 삭제/비공개 게시물 가능성")
                result["error"] = "게시물이 삭제되었거나 비공개 상태입니다"
                result["error_type"] = "not_found"

            return result

        except TimeoutException:
            logger.error(f"페이지 로드 타임아웃: {url}")
            raise InstagramPostLoadError(f"게시물 페이지 로드 시간 초과: {url}")
        except Exception as e:
            logger.error(f"데이터 추출 중 오류: {e}")
            raise InstagramCrawlerError(f"게시물 데이터 추출 실패: {e}")

    def _parse_json_data(self, json_str: str, result: dict) -> None:
        """
        JSON 데이터에서 정보 추출

        Args:
            json_str: JSON 문자열
            result: 결과 딕셔너리 (업데이트됨)
        """
        try:
            data = json.loads(json_str)

            # 다양한 JSON 구조 처리
            def extract_from_dict(d: dict) -> None:
                if isinstance(d, dict):
                    # 좋아요 수
                    if "like_count" in d:
                        result["likes"] = int(d["like_count"])
                    elif "edge_liked_by" in d and "count" in d["edge_liked_by"]:
                        result["likes"] = int(d["edge_liked_by"]["count"])
                    elif "edge_media_preview_like" in d and "count" in d["edge_media_preview_like"]:
                        result["likes"] = int(d["edge_media_preview_like"]["count"])

                    # 댓글 수
                    if "comment_count" in d:
                        result["comments"] = int(d["comment_count"])
                    elif "edge_media_to_comment" in d and "count" in d["edge_media_to_comment"]:
                        result["comments"] = int(d["edge_media_to_comment"]["count"])
                    elif "edge_media_preview_comment" in d and "count" in d["edge_media_preview_comment"]:
                        result["comments"] = int(d["edge_media_preview_comment"]["count"])

                    # 조회수
                    if "video_view_count" in d:
                        result["views"] = int(d["video_view_count"])
                    elif "view_count" in d:
                        result["views"] = int(d["view_count"])

                    # 작성자
                    if "owner" in d and isinstance(d["owner"], dict):
                        if "username" in d["owner"]:
                            result["author"] = d["owner"]["username"]

                    # 캡션
                    if "caption" in d and isinstance(d["caption"], dict):
                        if "text" in d["caption"]:
                            result["caption"] = d["caption"]["text"][:500]
                    elif "edge_media_to_caption" in d:
                        edges = d["edge_media_to_caption"].get("edges", [])
                        if edges and "node" in edges[0]:
                            result["caption"] = edges[0]["node"].get("text", "")[:500]

                    # 재귀적으로 탐색
                    for key, value in d.items():
                        if isinstance(value, dict):
                            extract_from_dict(value)
                        elif isinstance(value, list):
                            for item in value:
                                if isinstance(item, dict):
                                    extract_from_dict(item)

            extract_from_dict(data)

        except json.JSONDecodeError:
            # 정규식으로 추출 시도
            patterns = [
                (r'"like_count"\s*:\s*(\d+)', "likes"),
                (r'"edge_liked_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)', "likes"),
                (r'"comment_count"\s*:\s*(\d+)', "comments"),
                (r'"edge_media_to_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)', "comments"),
                (r'"video_view_count"\s*:\s*(\d+)', "views"),
                (r'"view_count"\s*:\s*(\d+)', "views"),
            ]

            for pattern, key in patterns:
                match = re.search(pattern, json_str)
                if match:
                    value = int(match.group(1))
                    if key == "views":
                        result[key] = value
                    elif result.get(key, 0) == 0:
                        result[key] = value

        except Exception as e:
            logger.debug(f"JSON 파싱 실패: {e}")

    def crawl_post(self, url: str, require_login: bool = False) -> dict:
        """
        인스타그램 게시물 데이터 크롤링

        Args:
            url: 인스타그램 게시물 URL
            require_login: True면 로그인 필수

        Returns:
            {
                "platform": "instagram",
                "url": str,
                "author": str,
                "caption": str,
                "likes": int,
                "comments": int,
                "views": int or None,
                "crawled_at": str (ISO format)
            }
        """
        # URL 유효성 검사 - ValueError 대신 에러 dict 반환 (verify-bot 프로토콜)
        if not url or "instagram.com" not in url:
            logger.error(f"유효하지 않은 인스타그램 URL: {url}")
            return {
                "platform": "instagram",
                "url": url or "",
                "author": None,
                "caption": None,
                "likes": 0,
                "comments": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": f"유효하지 않은 인스타그램 URL입니다. instagram.com 도메인이 포함된 URL을 입력해주세요.",
                "error_type": "validation_error"
            }

        # 1. API 방식 우선 시도 (Cloud 환경에서 효과적)
        if self.use_api and self.session:
            logger.info("requests API로 크롤링 시도...")
            result = self._crawl_via_api(url)
            # engagement 데이터(likes/comments)가 있어야 성공으로 간주
            if result and (result.get('likes', 0) > 0 or result.get('comments', 0) > 0):
                logger.info(f"API 크롤링 성공: likes={result.get('likes')}, comments={result.get('comments')}")
                return result
            logger.info("API 방식에서 engagement 데이터 없음, Selenium fallback 시도...")

        # 2. Cloud 환경에서 API 실패 시 - 제한적 응답 반환
        if IS_CLOUD:
            logger.warning("Cloud 환경에서 Instagram 크롤링 제한적")
            return {
                "platform": "instagram",
                "url": url,
                "author": None,
                "caption": None,
                "likes": 0,
                "comments": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "로그인 쿠키가 필요합니다. 사이드바에서 인스타그램 쿠키를 설정해주세요.",
            }

        # 3. Selenium fallback (로컬 환경)
        if self.driver is None:
            self.driver = self._create_driver()

            # 저장된 쿠키 로드 시도
            if self.cookie_file.exists():
                logger.info("저장된 쿠키로 Selenium 세션 복원 시도...")
                if self._load_cookies():
                    if self._check_login_status():
                        self.is_logged_in = True
                        logger.info("쿠키로 로그인 상태 복원 성공")
                    else:
                        logger.warning("쿠키가 만료되었거나 유효하지 않음")

        # 로그인이 필요한 경우
        if require_login and not self.is_logged_in:
            self.login()

        # 게시물 데이터 추출
        try:
            return self._extract_post_data_from_page(url)
        except InstagramPostLoadError:
            # 로그인이 필요할 수 있음
            if not self.is_logged_in:
                logger.info("로그인 시도 후 재시도...")
                if self.login():
                    return self._extract_post_data_from_page(url)
            raise

    def crawl_posts(
        self,
        urls: list,
        require_login: bool = False,
        delay: float = None
    ) -> list:
        """
        여러 게시물 데이터 크롤링

        Args:
            urls: 게시물 URL 리스트
            require_login: 로그인 필수 여부
            delay: 요청 간 딜레이 (초) - None이면 랜덤

        Returns:
            게시물 데이터 딕셔너리 리스트
        """
        results = []

        for i, url in enumerate(urls):
            try:
                logger.info(f"크롤링 중 ({i+1}/{len(urls)}): {url}")
                result = self.crawl_post(url, require_login=(i == 0 and require_login))
                results.append(result)

                # Rate limiting
                if i < len(urls) - 1:
                    if delay:
                        time.sleep(delay)
                    else:
                        self._random_delay()

            except InstagramRateLimitError as e:
                logger.error(f"Rate limit 감지: {e}")
                # 더 긴 대기 후 재시도
                time.sleep(60)
                try:
                    result = self.crawl_post(url, require_login=False)
                    results.append(result)
                except Exception as e2:
                    results.append({
                        "platform": "instagram",
                        "url": url,
                        "error": str(e2),
                        "crawled_at": datetime.now().isoformat(),
                    })

            except Exception as e:
                logger.error(f"크롤링 실패 ({url}): {e}")
                results.append({
                    "platform": "instagram",
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

def crawl_instagram_post(url: str, headless: bool = False) -> dict:
    """
    인스타그램 게시물 데이터 크롤링 (단일 함수)

    Args:
        url: 인스타그램 게시물 URL
        headless: 헤드리스 모드

    Returns:
        {
            "platform": "instagram",
            "url": str,
            "author": str,
            "caption": str,
            "likes": int,
            "comments": int,
            "views": int or None,
            "crawled_at": str
        }
    """
    with InstagramCrawler(headless=headless) as crawler:
        return crawler.crawl_post(url)


def crawl_instagram_posts(
    urls: list,
    headless: bool = False,
    delay: float = None
) -> list:
    """
    여러 인스타그램 게시물 데이터 크롤링 (단일 함수)

    Args:
        urls: 게시물 URL 리스트
        headless: 헤드리스 모드
        delay: 요청 간 딜레이 (초)

    Returns:
        게시물 데이터 딕셔너리 리스트
    """
    with InstagramCrawler(headless=headless) as crawler:
        return crawler.crawl_posts(urls, delay=delay)


# === 테스트 코드 ===

if __name__ == "__main__":
    # 로깅 레벨 설정
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("인스타그램 크롤러 테스트")
    print("=" * 60)

    # 테스트 URL (실제 URL로 교체 필요)
    test_url = input("테스트할 인스타그램 게시물 URL을 입력하세요: ").strip()

    if test_url:
        try:
            result = crawl_instagram_post(test_url, headless=False)
            print("\n크롤링 결과:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"\n오류 발생: {e}")
    else:
        print("URL이 입력되지 않았습니다.")
