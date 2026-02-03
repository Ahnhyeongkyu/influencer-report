"""
프로젝트 설정 모듈

크롤러 및 애플리케이션 전역 설정
"""

import os
from pathlib import Path
from typing import Optional

# === 경로 설정 ===
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
COOKIES_DIR = DATA_DIR / "cookies"

# 디렉토리 생성
for dir_path in [DATA_DIR, LOGS_DIR, COOKIES_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)


# === 크롤러 공통 설정 ===
class CrawlerSettings:
    """크롤러 공통 설정"""

    # 타임아웃 (초)
    DEFAULT_TIMEOUT: int = 30
    PAGE_LOAD_TIMEOUT: int = 60
    ELEMENT_WAIT_TIMEOUT: int = 10

    # Rate Limiting
    REQUEST_DELAY: float = 2.0  # 요청 간 딜레이 (초)
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 5.0

    # 브라우저 설정
    HEADLESS_DEFAULT: bool = False  # QR 인증 등을 위해 기본 False
    WINDOW_SIZE: tuple = (1920, 1080)

    # User-Agent
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


# === 샤오홍슈 설정 ===
class XHSSettings:
    """샤오홍슈 크롤러 설정"""

    BASE_URL: str = "https://www.xiaohongshu.com"
    COOKIE_FILE: Path = COOKIES_DIR / "xhs_cookies.json"

    # QR 인증 대기 시간 (초)
    QR_AUTH_TIMEOUT: int = 120

    # 페이지 로드 대기 시간 (초)
    PAGE_LOAD_WAIT: float = 3.0

    # 요청 딜레이 (초) - 봇 탐지 방지
    REQUEST_DELAY: float = 2.0


# === 페이스북 설정 ===
class FacebookSettings:
    """페이스북 크롤러 설정"""

    BASE_URL: str = "https://www.facebook.com"
    COOKIE_FILE: Path = COOKIES_DIR / "facebook_cookies.json"

    # 로그인 대기 시간 (초)
    LOGIN_TIMEOUT: int = 60

    # 요청 딜레이 (초)
    REQUEST_DELAY: float = 3.0


# === 인스타그램 설정 ===
class InstagramSettings:
    """인스타그램 크롤러 설정"""

    BASE_URL: str = "https://www.instagram.com"
    COOKIE_FILE: Path = COOKIES_DIR / "instagram_cookies.json"

    # 로그인 대기 시간 (초)
    LOGIN_TIMEOUT: int = 60

    # 요청 딜레이 (초) - 인스타그램은 더 엄격함
    REQUEST_DELAY: float = 5.0


# === 유튜브 설정 ===
class YouTubeSettings:
    """유튜브 크롤러 설정"""

    BASE_URL: str = "https://www.youtube.com"
    COOKIE_FILE: Path = COOKIES_DIR / "youtube_cookies.json"

    # API 키 (선택적)
    API_KEY: Optional[str] = os.getenv("YOUTUBE_API_KEY")

    # 요청 딜레이 (초)
    REQUEST_DELAY: float = 1.0


# === 디카드 설정 ===
class DcardSettings:
    """디카드 크롤러 설정"""

    BASE_URL: str = "https://www.dcard.tw"
    COOKIE_FILE: Path = COOKIES_DIR / "dcard_cookies.json"

    # 요청 딜레이 (초)
    REQUEST_DELAY: float = 2.0


# === 로깅 설정 ===
class LoggingSettings:
    """로깅 설정"""

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE: Path = LOGS_DIR / "crawler.log"

    # 로그 파일 최대 크기 (bytes)
    MAX_LOG_SIZE: int = 10 * 1024 * 1024  # 10MB

    # 로그 파일 백업 개수
    BACKUP_COUNT: int = 5


# === Streamlit 앱 설정 ===
class AppSettings:
    """Streamlit 애플리케이션 설정"""

    APP_TITLE: str = "인플루언서 캠페인 성과 리포트"
    APP_ICON: str = "📊"

    # 인증 설정 (환경 변수 필수 — 하드코딩 금지)
    AUTH_ENABLED: bool = True
    AUTH_USERNAME: str = os.getenv("APP_USERNAME", "")
    AUTH_PASSWORD: str = os.getenv("APP_PASSWORD", "")

    # 세션 만료 시간 (초)
    SESSION_TIMEOUT: int = 3600  # 1시간


# === 환경 변수에서 설정 로드 ===
def load_env_settings():
    """환경 변수에서 설정 로드"""
    from dotenv import load_dotenv

    env_file = BASE_DIR / "config" / ".env"
    if env_file.exists():
        load_dotenv(env_file)


# 시작 시 환경 변수 로드 시도
try:
    load_env_settings()
except ImportError:
    pass  # python-dotenv가 없으면 무시


# === 설정 인스턴스 (편의용) ===
crawler_settings = CrawlerSettings()
xhs_settings = XHSSettings()
facebook_settings = FacebookSettings()
instagram_settings = InstagramSettings()
youtube_settings = YouTubeSettings()
dcard_settings = DcardSettings()
logging_settings = LoggingSettings()
app_settings = AppSettings()
