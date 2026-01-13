"""
샤오홍슈(小红书/RED) 크롤러 모듈

requests 기반 API 크롤링 + Selenium fallback
- 비로그인 상태에서도 공개 게시물 데이터 수집 시도
- 좋아요, 즐겨찾기, 댓글, 공유, 조회수 수집
- 쿠키 저장/재사용으로 세션 유지

Streamlit Cloud 호환:
- requests 기반 HTML 파싱 우선 시도
- 실패 시 로컬 환경에서만 Selenium 사용
- QR 인증은 로컬 환경에서만 지원
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import platform
import requests

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
)

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


class XHSCrawlerError(Exception):
    """샤오홍슈 크롤러 기본 예외"""
    pass


class XHSLoginError(XHSCrawlerError):
    """로그인 관련 예외"""
    pass


class XHSPostLoadError(XHSCrawlerError):
    """게시물 로드 관련 예외"""
    pass


class XHSCrawler:
    """
    샤오홍슈 크롤러 클래스

    requests 기반 API 크롤링을 우선 사용 (Selenium 없이 동작)
    실패 시 Selenium fallback으로 전환 (로컬 환경에서만)
    """

    # 샤오홍슈 기본 URL
    BASE_URL = "https://www.xiaohongshu.com"
    LOGIN_URL = "https://www.xiaohongshu.com"

    # 기본 설정
    DEFAULT_TIMEOUT = 30
    PAGE_LOAD_WAIT = 3
    QR_AUTH_TIMEOUT = 120  # QR 인증 대기 시간 (2분)

    # 쿠키 파일 경로
    COOKIE_DIR = Path(__file__).parent.parent.parent / "data" / "cookies"
    COOKIE_FILE = COOKIE_DIR / "xhs_cookies.json"

    # API 헤더
    API_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.xiaohongshu.com/',
    }

    def __init__(
        self,
        headless: bool = False,
        chrome_driver_path: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        cookie_file: Optional[str] = None,
        use_api: bool = True,  # API 방식 우선 사용
    ):
        """
        크롤러 초기화

        Args:
            headless: 헤드리스 모드 (QR 인증 시에는 False 필수)
            chrome_driver_path: ChromeDriver 경로 (None이면 자동 탐색)
            timeout: 기본 타임아웃 (초)
            cookie_file: 쿠키 저장 파일 경로 (None이면 기본 경로 사용)
            use_api: requests 기반 API 방식 우선 사용 여부
        """
        self.headless = headless
        self.chrome_driver_path = chrome_driver_path
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.use_api = use_api

        self.driver: Optional[webdriver.Chrome] = None
        self.is_logged_in = False
        self.session: Optional[requests.Session] = None

        # 쿠키 디렉토리 생성
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

        # requests 세션 초기화
        if use_api:
            self._init_session()

        logger.info(f"XHSCrawler 초기화 완료 (use_api={use_api})")

    def _init_session(self) -> None:
        """requests 세션 초기화"""
        self.session = requests.Session()
        self.session.headers.update(self.API_HEADERS)

    def _extract_note_id(self, url: str) -> Optional[str]:
        """
        URL에서 노트 ID 추출

        Args:
            url: 샤오홍슈 게시물 URL

        Returns:
            노트 ID 또는 None
        """
        # /explore/xxxxx 형식
        match = re.search(r'/explore/([a-zA-Z0-9]+)', url)
        if match:
            return match.group(1)

        # /discovery/item/xxxxx 형식
        match = re.search(r'/discovery/item/([a-zA-Z0-9]+)', url)
        if match:
            return match.group(1)

        # 단축 URL에서 ID 추출
        match = re.search(r'xhslink\.com/([a-zA-Z0-9]+)', url)
        if match:
            return match.group(1)

        return None

    def _crawl_via_api(self, url: str) -> Optional[Dict[str, Any]]:
        """
        requests를 통한 API 기반 크롤링

        Args:
            url: 샤오홍슈 게시물 URL

        Returns:
            게시물 데이터 또는 None
        """
        if not self.session:
            return None

        try:
            note_id = self._extract_note_id(url)
            if not note_id:
                logger.warning("노트 ID를 추출할 수 없습니다.")
                return None

            logger.info(f"requests API로 크롤링: note_id={note_id}")

            # 페이지 HTML 요청
            page_url = f"{self.BASE_URL}/explore/{note_id}"
            response = self.session.get(page_url, timeout=self.timeout)

            if response.status_code != 200:
                logger.warning(f"페이지 요청 실패: {response.status_code}")
                return None

            html = response.text

            # HTML에서 데이터 추출
            result = self._extract_data_from_html(html, url, note_id)
            if result and (result.get('likes', 0) > 0 or result.get('author')):
                return result

            return None

        except Exception as e:
            logger.warning(f"API 크롤링 실패: {e}")
            return None

    def _extract_data_from_html(self, html: str, url: str, note_id: str) -> Optional[Dict[str, Any]]:
        """
        HTML에서 게시물 데이터 추출

        Args:
            html: 페이지 HTML
            url: 원본 URL
            note_id: 노트 ID

        Returns:
            게시물 데이터 또는 None
        """
        result = {
            "platform": "xiaohongshu",
            "url": url,
            "note_id": note_id,
            "author": None,
            "author_id": None,
            "title": None,
            "likes": 0,
            "favorites": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # __INITIAL_STATE__ 또는 유사한 JSON 데이터 추출
            state_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?})</script>', html, re.DOTALL)
            if state_match:
                try:
                    # JSON 파싱 시도
                    state_text = state_match.group(1)
                    # undefined를 null로 변환
                    state_text = re.sub(r'\bundefined\b', 'null', state_text)
                    data = json.loads(state_text)

                    note_data = data.get('note', {}).get('note', {})
                    if note_data:
                        result['title'] = note_data.get('title', '')
                        result['likes'] = note_data.get('likedCount', 0) or 0
                        result['favorites'] = note_data.get('collectedCount', 0) or 0
                        result['comments'] = note_data.get('commentCount', 0) or 0
                        result['shares'] = note_data.get('shareCount', 0) or 0

                        user = note_data.get('user', {})
                        result['author'] = user.get('nickname')
                        result['author_id'] = user.get('userId')

                        if result.get('likes', 0) > 0 or result.get('author'):
                            logger.info(f"__INITIAL_STATE__에서 데이터 추출 성공")
                            return result
                except json.JSONDecodeError:
                    pass

            # 정규식으로 직접 추출 시도
            patterns = {
                'likes': [
                    r'"likedCount"\s*:\s*(\d+)',
                    r'"liked_count"\s*:\s*(\d+)',
                ],
                'favorites': [
                    r'"collectedCount"\s*:\s*(\d+)',
                    r'"collected_count"\s*:\s*(\d+)',
                ],
                'comments': [
                    r'"commentCount"\s*:\s*(\d+)',
                    r'"comment_count"\s*:\s*(\d+)',
                ],
                'shares': [
                    r'"shareCount"\s*:\s*(\d+)',
                    r'"share_count"\s*:\s*(\d+)',
                ],
                'author': [
                    r'"nickname"\s*:\s*"([^"]+)"',
                ],
                'title': [
                    r'"title"\s*:\s*"([^"]+)"',
                ],
            }

            for key, pattern_list in patterns.items():
                for pattern in pattern_list:
                    match = re.search(pattern, html)
                    if match:
                        value = match.group(1)
                        if key in ['likes', 'favorites', 'comments', 'shares']:
                            result[key] = int(value)
                        else:
                            result[key] = value
                        break

            return result if result.get('likes', 0) > 0 or result.get('favorites', 0) > 0 or result.get('author') else None

        except Exception as e:
            logger.debug(f"HTML 데이터 추출 실패: {e}")
            return None

    def _create_driver(self) -> webdriver.Chrome:
        """
        Chrome WebDriver 생성

        Returns:
            Chrome WebDriver 인스턴스
        """
        options = Options()

        # Cloud 환경이면 headless 강제
        if self.headless or IS_CLOUD:
            options.add_argument("--headless=new")

        # 기본 옵션
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        # 봇 탐지 우회 설정
        options.add_argument("--disable-blink-features=AutomationControlled")

        # User-Agent 설정
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        # 언어 설정 (중국어)
        options.add_argument("--lang=zh-CN")

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

            # 봇 탐지 우회를 위한 JavaScript 실행
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """
                },
            )

            logger.info("Chrome WebDriver 생성 완료")
            return driver

        except WebDriverException as e:
            logger.error(f"WebDriver 생성 실패: {e}")
            raise XHSCrawlerError(f"Chrome WebDriver를 생성할 수 없습니다: {e}")

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
            time.sleep(2)

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
            time.sleep(self.PAGE_LOAD_WAIT)

            # 로그인 버튼이 없으면 로그인 된 상태
            # 또는 사용자 아바타/프로필이 있으면 로그인 상태
            try:
                # 로그인 버튼 찾기 시도
                login_btn = self.driver.find_element(
                    By.XPATH,
                    "//button[contains(text(), '登录') or contains(text(), '로그인')]"
                )
                # 로그인 버튼이 있으면 미로그인 상태
                return False
            except NoSuchElementException:
                pass

            # 사용자 관련 요소 확인
            try:
                # 사용자 아바타나 개인 페이지 링크 확인
                user_element = self.driver.find_element(
                    By.XPATH,
                    "//a[contains(@href, '/user/profile')] | //div[contains(@class, 'user')] | //img[contains(@class, 'avatar')]"
                )
                logger.info("로그인 상태 확인됨")
                return True
            except NoSuchElementException:
                pass

            # 쿠키에서 로그인 관련 토큰 확인 (web_session만 확인)
            cookies = self.driver.get_cookies()
            for cookie in cookies:
                if cookie.get("name") == "web_session" and cookie.get("value"):
                    # web_session 값이 충분히 긴지 확인 (실제 로그인 세션)
                    if len(cookie.get("value", "")) > 50:
                        logger.info("로그인 세션 쿠키 확인됨")
                        return True

            return False

        except Exception as e:
            logger.warning(f"로그인 상태 확인 중 오류: {e}")
            return False

    def _wait_for_qr_auth(self) -> bool:
        """
        QR 코드 인증 대기

        사용자가 QR 코드를 스캔할 때까지 대기

        Returns:
            인증 성공 여부
        """
        logger.info("=" * 50)
        logger.info("QR 코드 인증이 필요합니다!")
        logger.info("브라우저 창에서 QR 코드를 샤오홍슈 앱으로 스캔해주세요.")
        logger.info(f"대기 시간: {self.QR_AUTH_TIMEOUT}초")
        logger.info("=" * 50)

        start_time = time.time()

        while time.time() - start_time < self.QR_AUTH_TIMEOUT:
            try:
                # 로그인 성공 확인 (URL 변화 또는 특정 요소 출현)
                current_url = self.driver.current_url

                # 로그인 성공 시 메인 페이지나 다른 페이지로 리다이렉트
                if "login" not in current_url.lower():
                    # 추가 확인
                    try:
                        # QR 코드 요소가 사라졌는지 확인
                        qr_element = self.driver.find_element(
                            By.XPATH,
                            "//div[contains(@class, 'qrcode')] | //img[contains(@class, 'qr')]"
                        )
                        # QR 코드가 아직 있으면 계속 대기
                    except NoSuchElementException:
                        # QR 코드가 사라졌으면 로그인 성공으로 간주
                        logger.info("QR 인증 성공!")
                        time.sleep(2)  # 페이지 로드 대기
                        return True

                # 로그인 관련 쿠키 확인 (web_session 길이로 판단)
                cookies = self.driver.get_cookies()
                for cookie in cookies:
                    if cookie.get("name") == "web_session" and cookie.get("value"):
                        if len(cookie.get("value", "")) > 50:
                            logger.info("로그인 세션 쿠키 감지 - 인증 성공!")
                            time.sleep(2)
                            return True

                remaining = int(self.QR_AUTH_TIMEOUT - (time.time() - start_time))
                if remaining % 10 == 0:
                    logger.info(f"QR 인증 대기 중... 남은 시간: {remaining}초")

                time.sleep(1)

            except Exception as e:
                logger.debug(f"인증 확인 중 오류 (무시): {e}")
                time.sleep(1)

        logger.error("QR 인증 시간 초과")
        return False

    def login(self, force_login: bool = False) -> bool:
        """
        샤오홍슈 로그인

        저장된 쿠키가 있으면 재사용, 없으면 QR 인증 수행

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
                    logger.info("저장된 쿠키가 만료됨. QR 인증 필요.")

        # QR 코드 인증 페이지로 이동
        logger.info("QR 코드 로그인 페이지로 이동...")

        try:
            self.driver.get(self.BASE_URL)
            time.sleep(self.PAGE_LOAD_WAIT)

            # 로그인 버튼 클릭 시도
            try:
                login_btn = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((
                        By.XPATH,
                        "//button[contains(text(), '登录')] | //span[contains(text(), '登录')] | //div[contains(@class, 'login')]"
                    ))
                )
                login_btn.click()
                time.sleep(2)
            except TimeoutException:
                logger.info("로그인 버튼을 찾지 못함 - 이미 로그인 페이지일 수 있음")

            # QR 인증 대기
            if self._wait_for_qr_auth():
                # 로그인 성공 후 쿠키 저장
                self._save_cookies()
                self.is_logged_in = True
                return True
            else:
                raise XHSLoginError("QR 코드 인증 시간 초과. 다시 시도해주세요.")

        except XHSLoginError:
            raise
        except Exception as e:
            logger.error(f"로그인 중 오류 발생: {e}")
            raise XHSLoginError(f"로그인 실패: {e}")

    def _parse_count(self, text: str) -> int:
        """
        숫자 텍스트 파싱 (예: "1.2万" -> 12000)

        Args:
            text: 숫자 텍스트

        Returns:
            정수 값
        """
        if not text:
            return 0

        text = text.strip()

        # 숫자만 있는 경우
        if text.isdigit():
            return int(text)

        # 만 단위 (万)
        if "万" in text or "w" in text.lower():
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 10000)

        # 억 단위 (亿)
        if "亿" in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 100000000)

        # k 단위
        if "k" in text.lower():
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000)

        # 그 외 숫자 추출
        num = re.search(r"[\d,]+", text.replace(",", ""))
        if num:
            return int(num.group().replace(",", ""))

        return 0

    def _extract_post_data(self, url: str) -> dict:
        """
        게시물 데이터 추출

        Args:
            url: 게시물 URL

        Returns:
            게시물 데이터 딕셔너리
        """
        result = {
            "platform": "xiaohongshu",
            "url": url,
            "author": None,
            "author_id": None,
            "title": None,
            "likes": 0,
            "favorites": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # 게시물 페이지 로드
            self.driver.get(url)
            time.sleep(self.PAGE_LOAD_WAIT)

            # 페이지가 완전히 로드될 때까지 대기
            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # 추가 대기 (동적 콘텐츠 로드)
            time.sleep(2)

            # === 작성자 정보 ===
            author_selectors = [
                "//a[contains(@class, 'author')]//span",
                "//div[contains(@class, 'author')]//span[@class='username']",
                "//a[contains(@class, 'name')]",
                "//span[contains(@class, 'user-name')]",
                "//div[contains(@class, 'user-info')]//span",
            ]
            for selector in author_selectors:
                try:
                    author_elem = self.driver.find_element(By.XPATH, selector)
                    result["author"] = author_elem.text.strip()
                    if result["author"]:
                        break
                except NoSuchElementException:
                    continue

            # === 제목 ===
            title_selectors = [
                "//div[contains(@class, 'title')]",
                "//h1",
                "//div[contains(@class, 'note-content')]//div[1]",
            ]
            for selector in title_selectors:
                try:
                    title_elem = self.driver.find_element(By.XPATH, selector)
                    result["title"] = title_elem.text.strip()[:100]  # 최대 100자
                    if result["title"]:
                        break
                except NoSuchElementException:
                    continue

            # === 좋아요 수 ===
            like_selectors = [
                "//span[contains(@class, 'like-count')]",
                "//span[contains(@class, 'count') and preceding-sibling::*[contains(@class, 'like')]]",
                "//div[contains(@class, 'like')]//span[contains(@class, 'count')]",
                "//span[contains(@class, 'like')]/following-sibling::span",
                "//*[contains(@class, 'like-wrapper')]//span",
                "//button[contains(@class, 'like')]//span",
            ]
            for selector in like_selectors:
                try:
                    like_elem = self.driver.find_element(By.XPATH, selector)
                    result["likes"] = self._parse_count(like_elem.text)
                    if result["likes"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === 즐겨찾기/수집 수 ===
            favorite_selectors = [
                "//span[contains(@class, 'collect-count')]",
                "//span[contains(@class, 'count') and preceding-sibling::*[contains(@class, 'collect')]]",
                "//div[contains(@class, 'collect')]//span[contains(@class, 'count')]",
                "//*[contains(@class, 'collect-wrapper')]//span",
                "//button[contains(@class, 'collect')]//span",
                "//span[contains(@class, 'star')]/following-sibling::span",
            ]
            for selector in favorite_selectors:
                try:
                    fav_elem = self.driver.find_element(By.XPATH, selector)
                    result["favorites"] = self._parse_count(fav_elem.text)
                    if result["favorites"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === 댓글 수 ===
            comment_selectors = [
                "//span[contains(@class, 'comment-count')]",
                "//span[contains(@class, 'count') and preceding-sibling::*[contains(@class, 'comment')]]",
                "//div[contains(@class, 'comment')]//span[contains(@class, 'count')]",
                "//*[contains(@class, 'comment-wrapper')]//span",
                "//button[contains(@class, 'chat')]//span",
                "//span[contains(text(), '评论')]",
            ]
            for selector in comment_selectors:
                try:
                    comment_elem = self.driver.find_element(By.XPATH, selector)
                    text = comment_elem.text
                    # "评论 123" 형식 처리
                    if "评论" in text:
                        text = text.replace("评论", "").strip()
                    result["comments"] = self._parse_count(text)
                    if result["comments"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === 공유 수 (있는 경우) ===
            share_selectors = [
                "//span[contains(@class, 'share-count')]",
                "//div[contains(@class, 'share')]//span[contains(@class, 'count')]",
                "//*[contains(@class, 'share-wrapper')]//span",
            ]
            for selector in share_selectors:
                try:
                    share_elem = self.driver.find_element(By.XPATH, selector)
                    result["shares"] = self._parse_count(share_elem.text)
                    if result["shares"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === 조회수 (있는 경우) ===
            view_selectors = [
                "//span[contains(@class, 'view-count')]",
                "//span[contains(@class, 'read-count')]",
                "//span[contains(text(), '浏览')]",
                "//span[contains(text(), '阅读')]",
            ]
            for selector in view_selectors:
                try:
                    view_elem = self.driver.find_element(By.XPATH, selector)
                    result["views"] = self._parse_count(view_elem.text)
                    if result["views"] and result["views"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === JavaScript로 데이터 추출 시도 (백업) ===
            if result["likes"] == 0 and result["favorites"] == 0:
                try:
                    # 페이지 내 JSON 데이터 추출 시도
                    page_data = self.driver.execute_script("""
                        // window.__INITIAL_STATE__ 또는 유사한 전역 객체에서 데이터 추출
                        if (window.__INITIAL_STATE__) {
                            return JSON.stringify(window.__INITIAL_STATE__);
                        }
                        if (window.__NUXT__) {
                            return JSON.stringify(window.__NUXT__);
                        }
                        // 페이지 소스에서 JSON 데이터 찾기
                        var scripts = document.querySelectorAll('script');
                        for (var i = 0; i < scripts.length; i++) {
                            var text = scripts[i].textContent;
                            if (text.includes('noteData') || text.includes('interactInfo')) {
                                return text;
                            }
                        }
                        return null;
                    """)

                    if page_data:
                        self._parse_json_data(page_data, result)

                except Exception as e:
                    logger.debug(f"JavaScript 데이터 추출 실패: {e}")

            logger.info(f"데이터 추출 완료: likes={result['likes']}, favorites={result['favorites']}, comments={result['comments']}")
            return result

        except TimeoutException:
            logger.error(f"페이지 로드 타임아웃: {url}")
            raise XHSPostLoadError(f"게시물 페이지 로드 시간 초과: {url}")
        except Exception as e:
            logger.error(f"데이터 추출 중 오류: {e}")
            raise XHSCrawlerError(f"게시물 데이터 추출 실패: {e}")

    def _parse_json_data(self, json_str: str, result: dict) -> None:
        """
        JSON 데이터에서 정보 추출

        Args:
            json_str: JSON 문자열
            result: 결과 딕셔너리 (업데이트됨)
        """
        try:
            # JSON 객체 찾기
            json_match = re.search(r'\{[^{}]*"likedCount"[^{}]*\}', json_str)
            if json_match:
                data = json.loads(json_match.group())
                if "likedCount" in data:
                    result["likes"] = int(data["likedCount"])
                if "collectedCount" in data:
                    result["favorites"] = int(data["collectedCount"])
                if "commentCount" in data:
                    result["comments"] = int(data["commentCount"])
                if "shareCount" in data:
                    result["shares"] = int(data["shareCount"])
                return

            # 다른 형식 시도
            patterns = [
                (r'"liked[_]?[cC]ount"\s*:\s*(\d+)', "likes"),
                (r'"collected[_]?[cC]ount"\s*:\s*(\d+)', "favorites"),
                (r'"comment[_]?[cC]ount"\s*:\s*(\d+)', "comments"),
                (r'"share[_]?[cC]ount"\s*:\s*(\d+)', "shares"),
            ]

            for pattern, key in patterns:
                match = re.search(pattern, json_str, re.IGNORECASE)
                if match:
                    result[key] = int(match.group(1))

        except Exception as e:
            logger.debug(f"JSON 파싱 실패: {e}")

    def crawl_post(self, url: str, auto_login: bool = True) -> dict:
        """
        샤오홍슈 게시물 데이터 크롤링

        Args:
            url: 샤오홍슈 게시물 URL
            auto_login: 로그인되지 않은 경우 자동 로그인 시도

        Returns:
            {
                "platform": "xiaohongshu",
                "url": str,
                "author": str,
                "title": str,
                "likes": int,
                "favorites": int,
                "comments": int,
                "shares": int,
                "views": int or None,
                "crawled_at": str (ISO format)
            }
        """
        # URL 유효성 검사
        if not url or "xiaohongshu.com" not in url and "xhslink.com" not in url:
            raise ValueError(f"유효하지 않은 샤오홍슈 URL: {url}")

        # 1. API 방식 우선 시도 (Cloud 환경에서 효과적)
        if self.use_api and self.session:
            logger.info("requests API로 크롤링 시도...")
            result = self._crawl_via_api(url)
            if result and (result.get('likes', 0) > 0 or result.get('favorites', 0) > 0 or result.get('author')):
                logger.info(f"API 크롤링 성공: likes={result.get('likes')}, favorites={result.get('favorites')}")
                return result
            logger.info("API 방식 실패, Selenium fallback 시도...")

        # 2. Cloud 환경에서 API 실패 시 - 제한적 응답 반환 (QR 인증 불가)
        if IS_CLOUD:
            logger.warning("Cloud 환경에서 샤오홍슈 크롤링 제한적 - QR 인증 불가")
            return {
                "platform": "xiaohongshu",
                "url": url,
                "author": None,
                "title": None,
                "likes": 0,
                "favorites": 0,
                "comments": 0,
                "shares": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "cloud_qr_auth_required",
            }

        # 3. Selenium fallback (로컬 환경)
        if self.driver is None:
            self.driver = self._create_driver()

        # 로그인 확인 및 수행
        if not self.is_logged_in and auto_login:
            self.login()

        # 게시물 데이터 추출
        return self._extract_post_data(url)

    def crawl_posts(self, urls: list, auto_login: bool = True, delay: float = 2.0) -> list:
        """
        여러 게시물 데이터 크롤링

        Args:
            urls: 게시물 URL 리스트
            auto_login: 로그인되지 않은 경우 자동 로그인 시도
            delay: 요청 간 딜레이 (초)

        Returns:
            게시물 데이터 딕셔너리 리스트
        """
        results = []

        for i, url in enumerate(urls):
            try:
                logger.info(f"크롤링 중 ({i+1}/{len(urls)}): {url}")
                result = self.crawl_post(url, auto_login=(i == 0 and auto_login))
                results.append(result)

                # Rate limiting
                if i < len(urls) - 1:
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"크롤링 실패 ({url}): {e}")
                results.append({
                    "platform": "xiaohongshu",
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

def crawl_xhs_post(url: str, headless: bool = False) -> dict:
    """
    샤오홍슈 게시물 데이터 크롤링 (단일 함수)

    Args:
        url: 샤오홍슈 게시물 URL
        headless: 헤드리스 모드 (QR 인증 시에는 False 필수)

    Returns:
        {
            "platform": "xiaohongshu",
            "url": str,
            "author": str,
            "likes": int,
            "favorites": int,
            "comments": int,
            "shares": int,
            "views": int or None,
            "crawled_at": str
        }
    """
    with XHSCrawler(headless=headless) as crawler:
        return crawler.crawl_post(url)


def crawl_xhs_posts(urls: list, headless: bool = False, delay: float = 2.0) -> list:
    """
    여러 샤오홍슈 게시물 데이터 크롤링 (단일 함수)

    Args:
        urls: 게시물 URL 리스트
        headless: 헤드리스 모드 (QR 인증 시에는 False 필수)
        delay: 요청 간 딜레이 (초)

    Returns:
        게시물 데이터 딕셔너리 리스트
    """
    with XHSCrawler(headless=headless) as crawler:
        return crawler.crawl_posts(urls, delay=delay)


# === 테스트 코드 ===

if __name__ == "__main__":
    # 로깅 레벨 설정
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("샤오홍슈 크롤러 테스트")
    print("=" * 60)

    # 테스트 URL (실제 URL로 교체 필요)
    test_url = input("테스트할 샤오홍슈 게시물 URL을 입력하세요: ").strip()

    if test_url:
        try:
            result = crawl_xhs_post(test_url, headless=False)
            print("\n크롤링 결과:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"\n오류 발생: {e}")
    else:
        print("URL이 입력되지 않았습니다.")
