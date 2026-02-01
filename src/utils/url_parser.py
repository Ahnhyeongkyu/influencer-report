"""
URL 파싱 및 플랫폼 감지 모듈

URL을 분석하여 플랫폼을 자동 감지하고 유효성을 검증
"""

import re
import logging
from typing import Optional, List, Tuple, Dict
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# 플랫폼별 URL 패턴
PLATFORM_PATTERNS: Dict[str, List[str]] = {
    "xiaohongshu": [
        r"xiaohongshu\.com",
        r"xhslink\.com",
        r"小红书",
    ],
    "youtube": [
        r"youtube\.com/watch",
        r"youtube\.com/shorts",
        r"youtu\.be/",
        r"youtube\.com/embed",
        r"youtube\.com/v/",
    ],
    "instagram": [
        r"instagram\.com/p/",
        r"instagram\.com/reel/",
        r"instagram\.com/tv/",
        r"instagram\.com/[^/]+/p/",  # /{username}/p/{shortcode} 형식
        r"instagram\.com/[^/]+/reel/",  # /{username}/reel/{shortcode} 형식
    ],
    "facebook": [
        r"facebook\.com",
        r"fb\.com",
        r"fb\.watch",
    ],
    "dcard": [
        r"dcard\.tw",
    ],
}

# 플랫폼 표시명
PLATFORM_DISPLAY_NAMES: Dict[str, str] = {
    "xiaohongshu": "샤오홍슈 (RED)",
    "youtube": "유튜브",
    "instagram": "인스타그램",
    "facebook": "페이스북",
    "dcard": "디카드 (Dcard)",
}

# 플랫폼 아이콘 (이모지)
PLATFORM_ICONS: Dict[str, str] = {
    "xiaohongshu": "",
    "youtube": "",
    "instagram": "",
    "facebook": "",
    "dcard": "",
}


def detect_platform(url: str) -> Optional[str]:
    """
    URL에서 플랫폼 감지

    Args:
        url: 분석할 URL

    Returns:
        플랫폼 이름 (xiaohongshu, youtube, instagram, facebook, dcard) 또는 None
    """
    if not url:
        return None

    url_lower = url.lower().strip()

    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url_lower):
                return platform

    return None


def validate_url(url: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    URL 유효성 검증

    Args:
        url: 검증할 URL

    Returns:
        (유효 여부, 플랫폼명, 에러 메시지)
    """
    if not url or not url.strip():
        return False, None, "URL이 비어있습니다"

    url = url.strip()

    # URL 형식 검사
    if not url.startswith(("http://", "https://")):
        # 프로토콜 추가 시도
        url = "https://" + url

    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return False, None, "유효하지 않은 URL 형식입니다"
    except Exception:
        return False, None, "URL 파싱에 실패했습니다"

    # 플랫폼 감지
    platform = detect_platform(url)
    if not platform:
        return False, None, "지원하지 않는 플랫폼입니다"

    return True, platform, None


def normalize_url(url: str) -> str:
    """
    URL 정규화

    Args:
        url: 정규화할 URL

    Returns:
        정규화된 URL
    """
    if not url:
        return url

    url = url.strip()

    # 프로토콜 추가
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # 후행 슬래시 정리
    url = url.rstrip("/")

    # 특수 케이스: 유튜브 단축 URL 처리
    if "youtu.be" in url:
        match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
        if match:
            video_id = match.group(1)
            url = f"https://www.youtube.com/watch?v={video_id}"

    return url


def parse_urls(text: str) -> List[Dict[str, str]]:
    """
    텍스트에서 URL 추출 및 분석

    한 줄에 하나의 URL이 있다고 가정

    Args:
        text: URL이 포함된 텍스트

    Returns:
        [{"url": str, "platform": str, "valid": bool, "error": str or None}, ...]
    """
    results = []

    if not text:
        return results

    lines = text.strip().split("\n")

    for line in lines:
        line = line.strip()

        # 빈 줄 스킵
        if not line:
            continue

        # 주석 스킵 (# 또는 // 로 시작하는 줄)
        if line.startswith("#") or line.startswith("//"):
            continue

        # URL 추출 (줄에서 첫 번째 URL만)
        # URL 패턴 정규식
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        match = re.search(url_pattern, line)

        if match:
            url = match.group(0)
        else:
            # http(s)가 없는 경우에도 도메인으로 시작하면 URL로 간주
            domain_pattern = r'^[\w.-]+\.(com|tw|be|co|net|org)[^\s]*'
            match = re.search(domain_pattern, line)
            if match:
                url = match.group(0)
            else:
                url = line  # 그냥 입력된 값 사용

        # URL 정규화
        normalized_url = normalize_url(url)

        # 유효성 검증
        is_valid, platform, error = validate_url(normalized_url)

        results.append({
            "url": normalized_url,
            "original": line,
            "platform": platform,
            "valid": is_valid,
            "error": error,
        })

    return results


def parse_csv_urls(csv_content: str, url_column: str = None) -> List[Dict[str, str]]:
    """
    CSV 내용에서 URL 추출 및 분석

    Args:
        csv_content: CSV 파일 내용
        url_column: URL이 있는 컬럼명 (None이면 자동 감지)

    Returns:
        URL 정보 리스트
    """
    import csv
    from io import StringIO

    results = []

    try:
        reader = csv.DictReader(StringIO(csv_content))

        # URL 컬럼 자동 감지
        if not url_column and reader.fieldnames:
            url_keywords = ["url", "link", "주소", "링크", "URL", "Link"]
            for field in reader.fieldnames:
                for keyword in url_keywords:
                    if keyword.lower() in field.lower():
                        url_column = field
                        break
                if url_column:
                    break

            # 못 찾으면 첫 번째 컬럼 사용
            if not url_column:
                url_column = reader.fieldnames[0]

        for row in reader:
            url = row.get(url_column, "").strip()
            if url:
                normalized_url = normalize_url(url)
                is_valid, platform, error = validate_url(normalized_url)

                results.append({
                    "url": normalized_url,
                    "original": url,
                    "platform": platform,
                    "valid": is_valid,
                    "error": error,
                })

    except Exception as e:
        logger.error(f"CSV 파싱 실패: {e}")
        raise ValueError(f"CSV 파일을 파싱할 수 없습니다: {e}")

    return results


def get_platform_display_name(platform: str) -> str:
    """
    플랫폼 표시명 반환

    Args:
        platform: 플랫폼 코드

    Returns:
        표시명
    """
    return PLATFORM_DISPLAY_NAMES.get(platform, platform)


def get_platform_icon(platform: str) -> str:
    """
    플랫폼 아이콘 반환

    Args:
        platform: 플랫폼 코드

    Returns:
        아이콘 (이모지)
    """
    return PLATFORM_ICONS.get(platform, "")


def count_by_platform(urls: List[Dict[str, str]]) -> Dict[str, int]:
    """
    플랫폼별 URL 개수 집계

    Args:
        urls: parse_urls() 결과

    Returns:
        {"youtube": 3, "instagram": 5, ...}
    """
    counts = {}
    for item in urls:
        if item.get("valid") and item.get("platform"):
            platform = item["platform"]
            counts[platform] = counts.get(platform, 0) + 1
    return counts
