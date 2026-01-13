"""
인스타그램(Instagram) 크롤러 모듈

공개 게시물 데이터 크롤링 (로그인 없이 시도 -> 필요시 로그인)
- 좋아요, 댓글, 조회수, 작성자, 캡션 수집
- 봇 탐지 우회를 위한 Stealth 모드 적용
- 쿠키 저장/재사용으로 세션 유지
"""

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

    공개 게시물 데이터 수집 (비로그인 우선, 필요시 로그인)
    Stealth 모드를 통한 봇 탐지 우회
    쿠키 저장/로드를 통한 세션 재사용 지원
    """

    # 인스타그램 기본 URL
    BASE_URL = "https://www.instagram.com"

    # 기본 설정
    DEFAULT_TIMEOUT = 30
    PAGE_LOAD_WAIT = 3
    LOGIN_TIMEOUT = 60

    # 쿠키 파일 경로
    COOKIE_DIR = Path(__file__).parent.parent.parent / "data" / "cookies"
    COOKIE_FILE = COOKIE_DIR / "instagram_cookies.json"

    # Rate Limiting 설정
    MIN_REQUEST_DELAY = 3.0
    MAX_REQUEST_DELAY = 7.0

    def __init__(
        self,
        headless: bool = False,
        chrome_driver_path: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        cookie_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
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
        """
        self.headless = headless
        self.chrome_driver_path = chrome_driver_path
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.username = username or os.getenv("INSTAGRAM_USERNAME")
        self.password = password or os.getenv("INSTAGRAM_PASSWORD")

        self.driver: Optional[webdriver.Chrome] = None
        self.is_logged_in = False

        # 쿠키 디렉토리 생성
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("InstagramCrawler 초기화 완료")

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
        patterns = [
            r"instagram\.com/p/([A-Za-z0-9_-]+)",
            r"instagram\.com/reel/([A-Za-z0-9_-]+)",
            r"instagram\.com/tv/([A-Za-z0-9_-]+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

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
            "caption": None,
            "likes": 0,
            "comments": 0,
            "views": None,
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # 게시물 페이지 로드
            self.driver.get(url)
            self._random_delay(3, 5)

            # 페이지가 완전히 로드될 때까지 대기
            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "article"))
            )

            # 팝업 처리
            self._handle_login_popup()

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
            caption_selectors = [
                "//article//div[contains(@class, 'Caption')]",
                "//article//span[contains(@class, 'caption')]",
                "//article//h1/following-sibling::span",
                "//div[@role='dialog']//h1",
                "//article//ul/li[1]//span[not(contains(@class, 'username'))]",
            ]
            for selector in caption_selectors:
                try:
                    caption_elem = self.driver.find_element(By.XPATH, selector)
                    caption_text = caption_elem.text.strip()
                    if caption_text and len(caption_text) > 5:
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

            logger.info(
                f"데이터 추출 완료: author={result['author']}, "
                f"likes={result['likes']}, comments={result['comments']}, "
                f"views={result['views']}"
            )
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
        # URL 유효성 검사
        if not url or "instagram.com" not in url:
            raise ValueError(f"유효하지 않은 인스타그램 URL: {url}")

        # 드라이버 초기화
        if self.driver is None:
            self.driver = self._create_driver()

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
