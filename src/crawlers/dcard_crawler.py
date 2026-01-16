"""
Dcard (대만 커뮤니티 플랫폼) 크롤러 모듈

API 기반 크롤링 (Cloudflare 우회)
- cloudscraper를 사용한 Dcard API v2 직접 호출
- Selenium 없이 서버리스 환경에서 동작
- 좋아요, 댓글 수 등 수집

Streamlit Cloud 호환:
- cloudscraper + API 방식으로 Cloudflare 우회
- Selenium fallback 지원 (로컬 환경용)
"""

import json
import logging
import os
import platform
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

# cloudscraper25 for Cloudflare v2/v3 bypass (enhanced fork)
try:
    import cloudscraper25 as cloudscraper
    HAS_CLOUDSCRAPER = True
    CLOUDSCRAPER_VERSION = "v25"
except ImportError:
    try:
        import cloudscraper
        HAS_CLOUDSCRAPER = True
        CLOUDSCRAPER_VERSION = "legacy"
    except ImportError:
        HAS_CLOUDSCRAPER = False
        CLOUDSCRAPER_VERSION = None

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


class DcardCrawlerError(Exception):
    """Dcard 크롤러 기본 예외"""
    pass


class DcardAPIError(DcardCrawlerError):
    """API 호출 관련 예외"""
    pass


class DcardPostNotFoundError(DcardCrawlerError):
    """게시물을 찾을 수 없음"""
    pass


class DcardCloudflareError(DcardCrawlerError):
    """Cloudflare 차단"""
    pass


class DcardCrawler:
    """
    Dcard 크롤러 클래스

    cloudscraper를 사용한 API 기반 크롤링 (Cloudflare 우회)
    Selenium은 로컬 환경 fallback으로 사용

    Cloud 환경:
    - cloudscraper + Dcard API v2 사용
    - Selenium 없이 동작

    로컬 환경:
    - cloudscraper 우선 시도
    - 실패 시 Selenium fallback
    """

    # 기본 URL
    BASE_URL = "https://www.dcard.tw"
    API_URL = "https://www.dcard.tw/service/api/v2/posts"

    # 기본 설정
    DEFAULT_TIMEOUT = 30
    PAGE_LOAD_WAIT = 5
    CLOUDFLARE_WAIT = 30  # Cloudflare 인증 대기 시간

    # 쿠키 파일 경로
    COOKIE_DIR = Path(__file__).parent.parent.parent / "data" / "cookies"
    COOKIE_FILE = COOKIE_DIR / "dcard_cookies.json"

    def __init__(
        self,
        headless: bool = False,  # GUI 모드 권장
        timeout: int = DEFAULT_TIMEOUT,
        cookie_file: Optional[str] = None,
        use_api: bool = True,  # API 방식 우선 사용
    ):
        """
        크롤러 초기화

        Args:
            headless: 헤드리스 모드 (Selenium 사용 시)
            timeout: 기본 타임아웃 (초)
            cookie_file: 쿠키 저장 파일 경로
            use_api: API 방식 우선 사용 여부
        """
        self.headless = headless
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.use_api = use_api

        self.driver = None
        self.scraper = None  # cloudscraper 인스턴스

        # 쿠키 디렉토리 생성
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)

        # cloudscraper 초기화
        if HAS_CLOUDSCRAPER and use_api:
            self._init_scraper()

        logger.info(f"DcardCrawler 초기화 완료 (use_api={use_api}, headless={headless})")

    def _init_scraper(self) -> None:
        """cloudscraper 인스턴스 초기화 (v2/v3 지원 강화)"""
        try:
            # cloudscraper25는 더 강력한 옵션 지원
            scraper_options = {
                'browser': {
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True,
                },
                'delay': 5,  # 초기 딜레이
            }

            # cloudscraper25 전용 옵션 (v2/v3 지원)
            if CLOUDSCRAPER_VERSION == "v25":
                scraper_options.update({
                    'interpreter': 'nodejs',  # JS 해석기 (더 정확)
                    'allow_brotli': True,
                    'debug': False,
                })

            self.scraper = cloudscraper.create_scraper(**scraper_options)

            # 헤더 설정 (더 자연스러운 브라우저처럼)
            self.scraper.headers.update({
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.dcard.tw/',
                'Origin': 'https://www.dcard.tw',
                'Sec-Ch-Ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
            })
            logger.info(f"cloudscraper 초기화 완료 (version={CLOUDSCRAPER_VERSION})")
        except Exception as e:
            logger.warning(f"cloudscraper 초기화 실패: {e}")
            self.scraper = None

    def _crawl_via_api(self, post_id: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
        """
        Dcard API를 통한 게시물 데이터 크롤링 (재시도 로직 포함)

        Args:
            post_id: 게시물 ID
            max_retries: 최대 재시도 횟수

        Returns:
            게시물 데이터 또는 None (실패 시)
        """
        if not self.scraper:
            return None

        api_url = f"{self.API_URL}/{post_id}"

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    wait_time = 3 * (attempt + 1)  # 점진적 대기: 6초, 9초...
                    logger.info(f"재시도 {attempt + 1}/{max_retries} - {wait_time}초 대기...")
                    print(f"[Dcard] 재시도 {attempt + 1}/{max_retries} - {wait_time}초 대기...")
                    time.sleep(wait_time)

                logger.info(f"Dcard API 호출: {api_url}")
                response = self.scraper.get(api_url, timeout=self.timeout)

                if response.status_code == 200:
                    data = response.json()
                    result = {
                        "platform": "dcard",
                        "url": f"{self.BASE_URL}/f/{data.get('forumAlias', 'all')}/p/{post_id}",
                        "post_id": str(post_id),
                        "author": data.get("school") or "Anonymous",
                        "title": data.get("title", ""),
                        "likes": data.get("likeCount", 0) or 0,
                        "comments": data.get("commentCount", 0) or 0,
                        "shares": data.get("shareCount"),
                        "views": data.get("viewCount"),
                        "forum": data.get("forumAlias", ""),
                        "created_at": data.get("createdAt", ""),
                        "crawled_at": datetime.now().isoformat(),
                    }
                    logger.info(f"API 크롤링 성공: likes={result['likes']}, comments={result['comments']}")
                    return result

                elif response.status_code == 404:
                    logger.error(f"게시물을 찾을 수 없음: {post_id}")
                    raise DcardPostNotFoundError(f"게시물 ID {post_id}를 찾을 수 없습니다.")

                elif response.status_code == 403:
                    logger.warning(f"Cloudflare 차단 감지 (403) - 재시도 중...")
                    print(f"[Dcard] Cloudflare 보안 감지됨 - 재시도 중...")
                    # 재시도를 위해 continue

                elif response.status_code == 429:
                    logger.warning(f"Rate limit 초과 (429) - 대기 후 재시도...")
                    print(f"[Dcard] 요청 제한 감지됨 - 잠시 대기...")
                    time.sleep(10)  # Rate limit은 더 오래 대기

                else:
                    logger.warning(f"API 응답 오류: {response.status_code}")

            except DcardPostNotFoundError:
                raise
            except Exception as e:
                logger.warning(f"API 크롤링 시도 {attempt + 1} 실패: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"API 크롤링 최종 실패: {e}")

        return None

    def _create_driver(self):
        """
        Chrome WebDriver 생성 (스텔스 옵션 강화)
        - 로컬: undetected-chromedriver 사용 (최대 스텔스)
        - Cloud: 일반 Selenium 사용 (fallback)

        Returns:
            Chrome WebDriver 인스턴스
        """
        # Cloud 환경에서는 일반 Selenium 사용
        if IS_CLOUD:
            return self._create_cloud_driver()

        # 로컬: undetected-chromedriver 시도 (스텔스 옵션 강화)
        try:
            import undetected_chromedriver as uc

            options = uc.ChromeOptions()

            if self.headless:
                options.add_argument("--headless=new")

            # === 스텔스 옵션 강화 ===
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--start-maximized")

            # 언어 설정 (대만 중국어)
            options.add_argument("--lang=zh-TW")

            # 봇 탐지 우회 옵션
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-infobars")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-popup-blocking")

            # GPU 관련 (일부 환경에서 탐지 방지)
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-software-rasterizer")

            # 실제 브라우저처럼 보이게
            options.add_argument("--disable-features=VizDisplayCompositor")
            options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")

            # 프로필 설정 (더 자연스럽게)
            options.add_argument("--disable-background-networking")
            options.add_argument("--disable-default-apps")
            options.add_argument("--disable-sync")

            # undetected-chromedriver 특수 옵션
            driver = uc.Chrome(
                options=options,
                use_subprocess=True,  # 서브프로세스 사용 (탐지 회피)
                version_main=None,  # 자동 버전 매칭
            )

            # 추가 스텔스 JavaScript 실행
            driver.execute_script("""
                // WebDriver 속성 숨기기
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // 플러그인 배열 조작
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });

                // 언어 설정
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-TW', 'zh', 'en-US', 'en']
                });

                // Chrome 객체 확인
                window.chrome = {
                    runtime: {}
                };
            """)

            logger.info("undetected-chromedriver 생성 완료 (스텔스 모드)")
            return driver

        except ImportError:
            logger.warning("undetected-chromedriver 없음, 일반 Selenium 사용")
            return self._create_cloud_driver()
        except Exception as e:
            logger.warning(f"undetected-chromedriver 실패: {e}, 일반 Selenium 사용")
            return self._create_cloud_driver()

    def _create_cloud_driver(self):
        """
        Streamlit Cloud용 일반 Selenium WebDriver 생성

        Returns:
            Chrome WebDriver 인스턴스
        """
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        from webdriver_manager.core.os_manager import ChromeType

        options = Options()

        # Cloud 환경이면 headless 강제
        if self.headless or IS_CLOUD:
            options.add_argument("--headless=new")

        # 기본 옵션
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        # 봇 탐지 우회
        options.add_argument("--disable-blink-features=AutomationControlled")

        # 언어 설정 (대만 중국어)
        options.add_argument("--lang=zh-TW")

        # User-Agent 설정
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        try:
            if IS_CLOUD:
                service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
            else:
                service = Service(ChromeDriverManager().install())

            driver = webdriver.Chrome(service=service, options=options)

            # 봇 탐지 우회 JavaScript
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

            logger.info("Cloud용 Chrome WebDriver 생성 완료")
            return driver
        except Exception as e:
            logger.error(f"WebDriver 생성 실패: {e}")
            raise DcardCrawlerError(f"Chrome WebDriver를 생성할 수 없습니다: {e}")

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
                except Exception:
                    pass  # 개별 쿠키 실패는 무시

            logger.info("쿠키 로드 완료")
            return True

        except Exception as e:
            logger.warning(f"쿠키 로드 실패: {e}")
            return False

    def _wait_for_cloudflare(self) -> bool:
        """
        Cloudflare 인증 대기

        Returns:
            인증 성공 여부
        """
        logger.info("=" * 50)
        logger.info("Cloudflare 인증이 필요할 수 있습니다.")
        logger.info("브라우저 창에서 인증을 완료해주세요.")
        logger.info(f"대기 시간: {self.CLOUDFLARE_WAIT}초")
        logger.info("=" * 50)

        start_time = time.time()

        while time.time() - start_time < self.CLOUDFLARE_WAIT:
            page_source = self.driver.page_source

            # Cloudflare 체크 완료 확인
            if "Just a moment" not in page_source and "Cloudflare" not in self.driver.title:
                # 실제 Dcard 페이지인지 확인
                if "dcard" in self.driver.title.lower() or "__NEXT_DATA__" in page_source:
                    logger.info("Cloudflare 인증 성공!")
                    self._save_cookies()
                    return True

            remaining = int(self.CLOUDFLARE_WAIT - (time.time() - start_time))
            if remaining % 5 == 0:
                logger.info(f"Cloudflare 인증 대기 중... 남은 시간: {remaining}초")

            time.sleep(1)

        logger.warning("Cloudflare 인증 시간 초과")
        return False

    def _extract_post_id(self, url: str) -> str:
        """
        URL에서 게시물 ID 추출

        Args:
            url: Dcard 게시물 URL

        Returns:
            게시물 ID
        """
        parsed = urlparse(url)
        path = parsed.path

        # /f/{forum}/p/{post_id} 형식
        match = re.search(r'/p/(\d+)', path)
        if match:
            return match.group(1)

        # /@{username}/{post_id} 형식
        match = re.search(r'/@[^/]+/(\d+)', path)
        if match:
            return match.group(1)

        # 숫자만 있는 경우
        if url.isdigit():
            return url

        # URL 끝에서 숫자 추출
        match = re.search(r'/(\d+)(?:-[^/]*)?/?$', path)
        if match:
            return match.group(1)

        raise ValueError(f"게시물 ID를 추출할 수 없습니다: {url}")

    def _parse_count(self, text: str) -> int:
        """
        숫자 텍스트 파싱 (예: "1.2K" -> 1200)

        Args:
            text: 숫자 텍스트

        Returns:
            정수 값
        """
        if not text:
            return 0

        text = text.strip()

        if text.isdigit():
            return int(text)

        # 만 단위 (萬/万)
        if "萬" in text or "万" in text:
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 10000)

        # K 단위
        if "k" in text.lower():
            num = re.search(r"[\d.]+", text)
            if num:
                return int(float(num.group()) * 1000)

        # 그 외 숫자 추출
        num = re.search(r"[\d,]+", text.replace(",", ""))
        if num:
            return int(num.group().replace(",", ""))

        return 0

    def _extract_post_data(self, url: str) -> Dict[str, Any]:
        """
        게시물 데이터 추출

        Args:
            url: 게시물 URL

        Returns:
            게시물 데이터 딕셔너리
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException

        result = {
            "platform": "dcard",
            "url": url,
            "post_id": None,
            "author": None,
            "title": None,
            "likes": 0,
            "comments": 0,
            "shares": None,
            "views": None,
            "forum": None,
            "created_at": None,
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # 게시물 ID 추출
            result["post_id"] = self._extract_post_id(url)

            # 게시물 페이지 로드
            self.driver.get(url)
            time.sleep(self.PAGE_LOAD_WAIT)

            # Cloudflare 체크
            if "Just a moment" in self.driver.page_source or "Cloudflare" in self.driver.title:
                if not self._wait_for_cloudflare():
                    raise DcardCloudflareError(
                        "Cloudflare 인증 실패. GUI 모드(headless=False)로 다시 시도하세요."
                    )
                # 페이지 다시 로드
                self.driver.get(url)
                time.sleep(self.PAGE_LOAD_WAIT)

            # 페이지가 완전히 로드될 때까지 대기
            try:
                WebDriverWait(self.driver, self.timeout).until(
                    EC.presence_of_element_located((By.TAG_NAME, "article"))
                )
            except TimeoutException:
                logger.warning("article 태그를 찾지 못함, 계속 진행")

            time.sleep(2)

            # === __NEXT_DATA__에서 데이터 추출 ===
            try:
                script = self.driver.find_element(
                    By.XPATH,
                    "//script[@id='__NEXT_DATA__']"
                )
                data = json.loads(script.get_attribute("innerHTML"))
                props = data.get("props", {}).get("pageProps", {})
                post = props.get("post") or props.get("initialPost", {})

                if post:
                    result["title"] = post.get("title", "")
                    result["likes"] = post.get("likeCount", 0) or 0
                    result["comments"] = post.get("commentCount", 0) or 0
                    result["forum"] = post.get("forumAlias", "")
                    result["created_at"] = post.get("createdAt", "")

                    # 작성자 정보
                    if "member" in post and post["member"]:
                        result["author"] = post["member"].get("nickname", "Anonymous")
                    elif "school" in post:
                        result["author"] = post.get("school", "Anonymous")
                    else:
                        result["author"] = "Anonymous"

                    logger.info("__NEXT_DATA__에서 데이터 추출 성공")
                    return result

            except (NoSuchElementException, json.JSONDecodeError) as e:
                logger.debug(f"__NEXT_DATA__ 추출 실패: {e}")

            # === 페이지 소스에서 JSON 패턴 추출 ===
            logger.info("페이지 소스에서 JSON 패턴 추출 시도")
            page_source = self.driver.page_source

            # likeCount, commentCount 패턴 찾기
            like_match = re.search(r'"likeCount"\s*:\s*(\d+)', page_source)
            comment_match = re.search(r'"commentCount"\s*:\s*(\d+)', page_source)
            title_match = re.search(r'"title"\s*:\s*"([^"]+)"', page_source)
            share_match = re.search(r'"shareCount"\s*:\s*(\d+)', page_source)
            created_match = re.search(r'"createdAt"\s*:\s*"([^"]+)"', page_source)

            if like_match:
                result["likes"] = int(like_match.group(1))
            if comment_match:
                result["comments"] = int(comment_match.group(1))
            if title_match:
                result["title"] = title_match.group(1)
            if share_match:
                result["shares"] = int(share_match.group(1))
            if created_match:
                result["created_at"] = created_match.group(1)

            # 데이터가 추출되었으면 반환
            if result["likes"] > 0 or result["comments"] > 0 or result["title"]:
                logger.info(f"JSON 패턴에서 데이터 추출 성공: likes={result['likes']}, comments={result['comments']}")
                # 포럼 (URL에서)
                forum_match = re.search(r'/f/([^/]+)/p/', url)
                if forum_match:
                    result["forum"] = forum_match.group(1)
                if not result["author"]:
                    result["author"] = "Anonymous"
                return result

            # === DOM에서 직접 추출 (최종 백업) ===
            logger.info("DOM에서 직접 데이터 추출 시도")

            # 제목
            try:
                title_elem = self.driver.find_element(By.XPATH, "//h1")
                result["title"] = title_elem.text.strip()
            except NoSuchElementException:
                pass

            # 포럼 (URL에서)
            forum_match = re.search(r'/f/([^/]+)/p/', url)
            if forum_match:
                result["forum"] = forum_match.group(1)

            if not result["author"]:
                result["author"] = "Anonymous"

            logger.info(
                f"데이터 추출 완료: likes={result['likes']}, "
                f"comments={result['comments']}"
            )
            return result

        except DcardCloudflareError:
            raise
        except TimeoutException:
            logger.error(f"페이지 로드 타임아웃: {url}")
            raise DcardPostNotFoundError(f"게시물 페이지 로드 시간 초과: {url}")
        except Exception as e:
            logger.error(f"데이터 추출 중 오류: {e}")
            raise DcardCrawlerError(f"게시물 데이터 추출 실패: {e}")

    def crawl_post(self, url: str) -> Dict[str, Any]:
        """
        Dcard 게시물 데이터 크롤링

        Args:
            url: Dcard 게시물 URL 또는 게시물 ID

        Returns:
            {
                "platform": "dcard",
                "url": str,
                "post_id": str,
                "author": str,
                "title": str,
                "likes": int,
                "comments": int,
                "shares": int or None,
                "views": int or None,
                "forum": str,
                "created_at": str,
                "crawled_at": str
            }
        """
        # URL 유효성 검사
        if not url:
            raise ValueError("URL이 비어있습니다")

        # 게시물 ID 추출
        post_id = None
        if url.isdigit():
            post_id = url
            url = f"{self.BASE_URL}/f/all/p/{url}"
        elif "dcard.tw" not in url:
            raise ValueError(f"유효하지 않은 Dcard URL: {url}")
        else:
            post_id = self._extract_post_id(url)

        # 1. API 방식 우선 시도 (cloudscraper25 v2/v3 지원)
        if self.use_api and self.scraper and post_id:
            print(f"[Dcard] API로 크롤링 시도 중... (cloudscraper {CLOUDSCRAPER_VERSION})")
            logger.info(f"API 방식으로 크롤링 시도 (version={CLOUDSCRAPER_VERSION})...")
            result = self._crawl_via_api(post_id, max_retries=3)
            if result:
                return result
            print("[Dcard] API 방식 실패, 브라우저 모드로 전환 중...")
            logger.info("API 방식 실패, Selenium fallback...")

        # 2. Cloud 환경에서는 API만 지원 (Selenium은 Cloudflare 우회 불가)
        if IS_CLOUD:
            # API를 시도했으나 실패한 경우
            if self.use_api and self.scraper:
                logger.warning("Cloud 환경에서 Dcard API 실패 - Cloudflare 차단 가능성")
                return {
                    "platform": "dcard",
                    "url": url,
                    "post_id": post_id,
                    "author": None,
                    "title": None,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "views": None,
                    "crawled_at": datetime.now().isoformat(),
                    "error": "cloud_api_blocked",
                }
            # cloudscraper가 설치되지 않은 경우
            raise DcardCloudflareError(
                "Cloud 환경에서는 cloudscraper + API 방식만 지원됩니다. "
                "cloudscraper 라이브러리가 설치되어 있는지 확인하세요."
            )

        # 3. Selenium fallback (로컬 환경)
        if self.driver is None:
            print("[Dcard] Chrome 브라우저 시작 중...")
            logger.info("Chrome WebDriver 생성 중...")
            self.driver = self._create_driver()

            # 저장된 쿠키로 세션 복원 시도
            if self.cookie_file.exists():
                self._load_cookies()

        # 게시물 데이터 추출
        print("[Dcard] 페이지 로드 중...")
        try:
            result = self._extract_post_data(url)

            # Cloudflare/IP 차단 확인 (여러 패턴 검사)
            if result.get('likes', 0) == 0 and result.get('comments', 0) == 0 and not result.get('title'):
                page_source = self.driver.page_source.lower() if self.driver else ""
                page_title = self.driver.title.lower() if self.driver else ""

                # 차단 패턴 검사
                block_patterns = [
                    "blocked", "차단", "access denied", "forbidden",
                    "cloudflare", "just a moment", "checking your browser",
                    "ray id", "security check", "captcha"
                ]

                is_blocked = any(pattern in page_source or pattern in page_title for pattern in block_patterns)

                if is_blocked:
                    print("")
                    print("=" * 60)
                    print("[Dcard] ⚠️ Cloudflare 보안에 의해 차단되었습니다.")
                    print("")
                    print("해결 방법:")
                    print("  1. VPN을 사용하세요 (대만 서버 권장)")
                    print("  2. 다른 네트워크로 변경하세요 (휴대폰 핫스팟 등)")
                    print("  3. 잠시 후 다시 시도하세요 (10-30분)")
                    print("")
                    print("참고: Dcard는 대만 플랫폼으로 Cloudflare Enterprise 보안을")
                    print("      사용하여 해외 접속이 불안정할 수 있습니다.")
                    print("=" * 60)
                    print("")

                    result["error"] = (
                        "Cloudflare 보안 차단 - "
                        "VPN(대만 서버) 사용 또는 다른 네트워크로 시도해주세요. "
                        "Dcard는 대만 플랫폼으로 해외 접속이 제한될 수 있습니다."
                    )

            return result

        except Exception as e:
            error_msg = str(e)
            print(f"[Dcard] 오류: {error_msg}")

            # 에러 유형별 안내
            if "timeout" in error_msg.lower():
                error_detail = "페이지 로드 시간 초과 - 네트워크 상태를 확인하세요."
            elif "cloudflare" in error_msg.lower():
                error_detail = "Cloudflare 보안 차단 - VPN 사용을 권장합니다."
            else:
                error_detail = f"크롤링 실패: {error_msg}"

            return {
                "platform": "dcard",
                "url": url,
                "post_id": post_id,
                "author": None,
                "title": None,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": error_detail,
            }

    def crawl_posts(
        self,
        urls: List[str],
        delay: float = 2.0,
        continue_on_error: bool = True
    ) -> List[Dict[str, Any]]:
        """
        여러 게시물 데이터 크롤링

        Args:
            urls: 게시물 URL 리스트
            delay: 요청 간 딜레이 (초)
            continue_on_error: 오류 시 계속 진행 여부

        Returns:
            게시물 데이터 딕셔너리 리스트
        """
        results = []

        for i, url in enumerate(urls):
            try:
                logger.info(f"크롤링 중 ({i + 1}/{len(urls)}): {url}")
                result = self.crawl_post(url)
                results.append(result)

            except Exception as e:
                logger.error(f"크롤링 실패 ({url}): {e}")

                if continue_on_error:
                    results.append({
                        "platform": "dcard",
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
        """브라우저 종료"""
        if self.driver:
            try:
                self._save_cookies()  # 종료 전 쿠키 저장
                self.driver.quit()
                logger.info("브라우저 종료 완료")
            except Exception as e:
                logger.warning(f"브라우저 종료 중 오류: {e}")
            finally:
                self.driver = None

    def __enter__(self):
        """Context manager 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager 종료"""
        self.close()


# === 편의 함수 ===

def crawl_dcard_post(url: str, headless: bool = False) -> Dict[str, Any]:
    """
    Dcard 게시물 데이터 크롤링 (단일 함수)

    Args:
        url: Dcard 게시물 URL
        headless: 헤드리스 모드 (False 권장 - Cloudflare 우회 위해)

    Returns:
        {
            "platform": "dcard",
            "url": str,
            "author": str,
            "title": str,
            "likes": int,
            "comments": int,
            "shares": int or None,
            "views": int or None,
            "crawled_at": str
        }

    Note:
        Cloudflare 보호로 인해 headless=False (GUI 모드)를 권장합니다.
        첫 실행 시 브라우저에서 Cloudflare 인증을 수동으로 완료해야 할 수 있습니다.
    """
    with DcardCrawler(headless=headless) as crawler:
        return crawler.crawl_post(url)


def crawl_dcard_posts(
    urls: List[str],
    headless: bool = False,
    delay: float = 2.0
) -> List[Dict[str, Any]]:
    """
    여러 Dcard 게시물 데이터 크롤링 (단일 함수)

    Args:
        urls: 게시물 URL 리스트
        headless: 헤드리스 모드 (False 권장)
        delay: 요청 간 딜레이 (초)

    Returns:
        게시물 데이터 딕셔너리 리스트
    """
    with DcardCrawler(headless=headless) as crawler:
        return crawler.crawl_posts(urls, delay=delay)


# === 테스트 코드 ===

if __name__ == "__main__":
    # 로깅 레벨 설정
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("Dcard 크롤러 테스트")
    print("=" * 60)
    print()
    print("주의: Dcard는 Cloudflare 보호가 강력합니다.")
    print("브라우저 창에서 Cloudflare 인증을 완료해야 합니다.")
    print()

    # 테스트 URL
    test_url = input("테스트할 Dcard 게시물 URL을 입력하세요: ").strip()

    if test_url:
        try:
            # GUI 모드로 실행 (Cloudflare 인증 가능)
            result = crawl_dcard_post(test_url, headless=False)
            print("\n크롤링 결과:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"\n오류 발생: {e}")
    else:
        print("URL이 입력되지 않았습니다.")
