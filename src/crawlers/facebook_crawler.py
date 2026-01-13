"""
Facebook 크롤러 모듈

공개 게시물/페이지 포스트 데이터 크롤링
- Stealth 모드 적용 (봇 탐지 우회)
- 모바일 버전(m.facebook.com) 우선 사용
- 쿠키 저장/재사용으로 세션 유지
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

    Stealth 모드를 적용한 공개 게시물 데이터 수집
    모바일 버전 우선 사용으로 크롤링 효율성 향상
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

    def __init__(
        self,
        headless: bool = False,
        chrome_driver_path: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        cookie_file: Optional[str] = None,
        use_mobile: bool = True,
    ):
        """
        크롤러 초기화

        Args:
            headless: 헤드리스 모드 (로그인 시에는 False 권장)
            chrome_driver_path: ChromeDriver 경로 (None이면 자동 탐색)
            timeout: 기본 타임아웃 (초)
            cookie_file: 쿠키 저장 파일 경로 (None이면 기본 경로 사용)
            use_mobile: 모바일 버전 사용 여부 (True 권장)
        """
        self.headless = headless
        self.chrome_driver_path = chrome_driver_path
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.use_mobile = use_mobile

        self.driver: Optional[webdriver.Chrome] = None
        self.is_logged_in = False
        self._last_request_time = 0

        # 쿠키 디렉토리 생성
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("FacebookCrawler 초기화 완료")

    def _create_driver(self) -> webdriver.Chrome:
        """
        Stealth 모드가 적용된 Chrome WebDriver 생성

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

        # K/천 단위 (먼저 체크)
        if "k" in text_lower or "천" in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000)

        # M 단위 (million) - 영어권
        if "m" in text_lower and "만" not in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000000)

        # 만 단위 (한국어)
        if "만" in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 10000)

        # B/억 단위
        if "b" in text_lower or "억" in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 100000000)

        # 숫자만 있는 경우 (접미사 처리 후)
        clean_text = re.sub(r"[^\d.]", "", text.replace(",", ""))
        if clean_text:
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
        author_selectors = [
            # 모바일 버전
            "//header//strong//a",
            "//h3//a",
            "//div[contains(@data-sigil, 'actor')]//a",
            # 데스크톱 버전
            "//strong//a[contains(@href, '/')]",
            "//span[contains(@class, 'author')]",
            "//a[contains(@class, 'profileLink')]",
            # 일반 패턴
            "//h2//a",
            "//div[@role='article']//strong//a",
        ]

        for selector in author_selectors:
            try:
                elem = driver.find_element(By.XPATH, selector)
                text = elem.text.strip()
                if text and len(text) > 1:
                    return text
            except NoSuchElementException:
                continue

        return None

    def _extract_content(self, driver: webdriver.Chrome) -> Optional[str]:
        """
        게시물 내용 추출

        Args:
            driver: WebDriver 인스턴스

        Returns:
            게시물 내용 또는 None
        """
        content_selectors = [
            # 모바일 버전
            "//div[contains(@data-sigil, 'message')]",
            "//div[contains(@data-sigil, 'expose')]//span",
            # 데스크톱 버전
            "//div[@data-ad-preview='message']",
            "//div[contains(@class, 'userContent')]",
            # 일반 패턴
            "//div[@role='article']//div[contains(@style, 'text-align')]",
            "//p[contains(@class, 'text')]",
        ]

        for selector in content_selectors:
            try:
                elem = driver.find_element(By.XPATH, selector)
                text = elem.text.strip()
                if text and len(text) > 5:
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
            "content": None,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
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

            # 로그인 필요 여부 확인
            current_url = self.driver.current_url
            if "login" in current_url.lower() or "checkpoint" in current_url.lower():
                logger.warning("로그인이 필요한 게시물입니다.")
                result["error"] = "login_required"
                return result

            # === 데이터 추출 ===
            result["author"] = self._extract_author(self.driver)
            result["content"] = self._extract_content(self.driver)
            result["likes"] = self._extract_reactions(self.driver)
            result["comments"] = self._extract_comments_count(self.driver)
            result["shares"] = self._extract_shares_count(self.driver)
            result["views"] = self._extract_views_count(self.driver)

            # JavaScript로 추가 데이터 추출 시도
            if result["likes"] == 0:
                self._try_javascript_extraction(result)

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

            # 반응 수 추출
            reaction_patterns = [
                r'"reaction_count"\s*:\s*\{"count"\s*:\s*(\d+)',
                r'"likecount"\s*:\s*(\d+)',
                r'"like_count"\s*:\s*(\d+)',
            ]
            for pattern in reaction_patterns:
                match = re.search(pattern, page_source, re.IGNORECASE)
                if match:
                    result["likes"] = int(match.group(1))
                    break

            # 댓글 수 추출
            comment_patterns = [
                r'"comment_count"\s*:\s*\{"total_count"\s*:\s*(\d+)',
                r'"commentcount"\s*:\s*(\d+)',
                r'"comments"\s*:\s*\{"count"\s*:\s*(\d+)',
            ]
            for pattern in comment_patterns:
                match = re.search(pattern, page_source, re.IGNORECASE)
                if match:
                    result["comments"] = int(match.group(1))
                    break

            # 공유 수 추출
            share_patterns = [
                r'"share_count"\s*:\s*\{"count"\s*:\s*(\d+)',
                r'"sharecount"\s*:\s*(\d+)',
            ]
            for pattern in share_patterns:
                match = re.search(pattern, page_source, re.IGNORECASE)
                if match:
                    result["shares"] = int(match.group(1))
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
        # URL 유효성 검사
        if not url or "facebook.com" not in url:
            raise ValueError(f"유효하지 않은 Facebook URL: {url}")

        # 드라이버 초기화
        if self.driver is None:
            self.driver = self._create_driver()

        # 로그인 필요시
        if require_login and not self.is_logged_in:
            if not self.login():
                raise FacebookLoginError("로그인에 실패했습니다.")

        # 게시물 데이터 추출
        return self._extract_post_data_from_page(url)

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
