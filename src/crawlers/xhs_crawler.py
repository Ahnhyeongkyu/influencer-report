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
import sys
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

# 파일 핸들러 추가 (디버그용)
_log_dir = Path(__file__).parent.parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.FileHandler(_log_dir / "xhs_debug.log", encoding="utf-8", mode="w")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_file_handler)

# 콘솔 핸들러
if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def parse_chinese_number(value) -> int:
    """
    중국어 숫자 포맷을 정수로 변환
    예: "2.6万" -> 26000, "1.5亿" -> 150000000, "2627" -> 2627

    Args:
        value: 숫자 값 (문자열 또는 정수)

    Returns:
        정수 값
    """
    if value is None:
        return 0

    if isinstance(value, (int, float)):
        return int(value)

    if not isinstance(value, str):
        return 0

    value = value.strip()
    if not value:
        return 0

    try:
        # 순수 숫자인 경우
        return int(value)
    except ValueError:
        pass

    try:
        # 소수점 숫자인 경우
        return int(float(value))
    except ValueError:
        pass

    # 중국어 단위 처리
    multiplier = 1

    if '亿' in value:
        multiplier = 100000000  # 억
        value = value.replace('亿', '')
    elif '万' in value:
        multiplier = 10000  # 만
        value = value.replace('万', '')
    elif 'w' in value.lower():
        multiplier = 10000  # 만 (영어 약어)
        value = value.lower().replace('w', '')
    elif 'k' in value.lower():
        multiplier = 1000
        value = value.lower().replace('k', '')

    try:
        # 남은 숫자 파싱
        value = value.strip()
        if value:
            return int(float(value) * multiplier)
    except ValueError:
        pass

    return 0


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
    COOKIE_FILE = COOKIE_DIR / "xiaohongshu_cookies.json"

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
        collect_comments: bool = False,  # 댓글 수집 여부
        max_comments: int = 10,  # 최대 댓글 수
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

            # 쿠키 상태 로깅 (디버깅용)
            cookie_names = list(self.session.cookies.keys())
            if cookie_names:
                logger.info(f"적용된 쿠키: {cookie_names}")
            else:
                logger.info("쿠키 없음 - QR 로그인이 필요할 수 있습니다")

            # 페이지 HTML 요청
            page_url = f"{self.BASE_URL}/explore/{note_id}"
            response = self.session.get(page_url, timeout=self.timeout)

            if response.status_code != 200:
                logger.warning(f"페이지 요청 실패: {response.status_code}")
                if response.status_code in [401, 403]:
                    logger.warning("인증 거부 - 쿠키가 만료되었거나 유효하지 않을 수 있습니다")
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
            "content": None,  # 게시물 본문
            "likes": 0,
            "favorites": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
            "thumbnail": None,
            "comments_list": [],
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # __INITIAL_STATE__ JSON 데이터 추출 (여러 패턴 시도)
            state_patterns = [
                r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*</script>',
                r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;?\s*\n',
                r'<script>window\.__INITIAL_STATE__\s*=\s*(\{.+?\})</script>',
            ]

            state_text = None
            for pattern in state_patterns:
                state_match = re.search(pattern, html, re.DOTALL)
                if state_match:
                    state_text = state_match.group(1)
                    break

            if state_text:
                try:
                    # undefined를 null로 변환
                    state_text = re.sub(r'\bundefined\b', 'null', state_text)
                    # function() 같은 JS 코드 제거
                    state_text = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', 'null', state_text)
                    data = json.loads(state_text)

                    logger.debug(f"__INITIAL_STATE__ 파싱 성공, 키: {list(data.keys())}")

                    # noteDetailMap 구조에서 데이터 추출 (최신 샤오홍슈 구조)
                    note_detail_map = data.get('note', {}).get('noteDetailMap', {})
                    if note_detail_map:
                        # noteDetailMap에서 note_id에 해당하는 데이터 찾기
                        note_data = note_detail_map.get(note_id, {}).get('note', {})
                        if not note_data:
                            # note_id가 다를 수 있으므로 첫 번째 항목 사용
                            for key, value in note_detail_map.items():
                                if isinstance(value, dict) and 'note' in value:
                                    note_data = value.get('note', {})
                                    break

                        if note_data:
                            # title과 content를 분리하여 추출
                            raw_title = note_data.get('title', '')
                            raw_desc = note_data.get('desc', '')

                            # 플레이스홀더 텍스트 필터링
                            placeholder_texts = ["还没有简介", "暂无简介", "没有描述", "暂无描述", "暂无内容", ""]

                            # title 설정 (플레이스홀더가 아닌 경우만)
                            if raw_title and raw_title.strip() not in placeholder_texts:
                                result['title'] = raw_title
                            elif raw_desc and raw_desc.strip() not in placeholder_texts:
                                result['title'] = raw_desc[:100]

                            # content 설정
                            if raw_desc and raw_desc.strip() not in placeholder_texts:
                                result['content'] = raw_desc

                            # interactInfo에서 상호작용 정보 추출
                            interact_info = note_data.get('interactInfo', {})
                            if interact_info:
                                logger.info(f"interactInfo 키: {list(interact_info.keys())}")
                                result['likes'] = parse_chinese_number(
                                    interact_info.get('likedCount') or interact_info.get('liked_count') or interact_info.get('likes') or 0)
                                result['favorites'] = parse_chinese_number(
                                    interact_info.get('collectedCount') or interact_info.get('collected_count') or interact_info.get('collectCount') or interact_info.get('bookmarkedCount') or 0)
                                result['comments'] = parse_chinese_number(
                                    interact_info.get('commentCount') or interact_info.get('comment_count') or interact_info.get('comments') or 0)
                                result['shares'] = parse_chinese_number(
                                    interact_info.get('shareCount') or interact_info.get('share_count') or interact_info.get('shares') or 0)

                            # 댓글 수를 comment 섹션에서도 시도
                            if result['comments'] == 0:
                                comment_section = data.get('comment', {})
                                if comment_section:
                                    result['comments'] = parse_chinese_number(
                                        comment_section.get('commentCount') or comment_section.get('total') or comment_section.get('count') or 0)
                                    logger.info(f"comment 섹션에서 댓글 수 추출: {result['comments']}")

                            # favorites를 note_data 직접 필드에서도 시도
                            if result['favorites'] == 0:
                                result['favorites'] = parse_chinese_number(
                                    note_data.get('collectedCount') or note_data.get('collectCount') or 0)
                            if result['shares'] == 0:
                                result['shares'] = parse_chinese_number(
                                    note_data.get('shareCount') or note_data.get('share_count') or 0)
                            if result['comments'] == 0:
                                result['comments'] = parse_chinese_number(
                                    note_data.get('commentCount') or note_data.get('comment_count') or 0)

                            # user 정보에서 작성자 추출
                            user = note_data.get('user', {})
                            if user:
                                result['author'] = user.get('nickname') or user.get('name')
                                result['author_id'] = user.get('userId') or user.get('uid')

                            # imageList에서 썸네일 추출
                            image_list = note_data.get('imageList', [])
                            if image_list and len(image_list) > 0:
                                first_image = image_list[0]
                                if isinstance(first_image, dict):
                                    # urlDefault 또는 url 사용
                                    result['thumbnail'] = first_image.get('urlDefault') or first_image.get('url') or first_image.get('infoList', [{}])[0].get('url')
                                elif isinstance(first_image, str):
                                    result['thumbnail'] = first_image

                            if result.get('likes', 0) > 0 or result.get('author'):
                                logger.info(f"noteDetailMap에서 데이터 추출 성공: likes={result['likes']}, author={result['author']}, thumbnail={'있음' if result.get('thumbnail') else '없음'}")
                                return result

                    # 구버전 구조: note.note
                    note_data = data.get('note', {}).get('note', {})
                    if note_data:
                        # title과 content를 분리하여 추출
                        raw_title = note_data.get('title', '')
                        raw_desc = note_data.get('desc', '')

                        # 플레이스홀더 텍스트 필터링
                        if raw_title and raw_title.strip() not in placeholder_texts:
                            result['title'] = raw_title
                        elif raw_desc and raw_desc.strip() not in placeholder_texts:
                            result['title'] = raw_desc[:100]

                        # content 설정
                        if raw_desc and raw_desc.strip() not in placeholder_texts:
                            result['content'] = raw_desc

                        # interactInfo 우선
                        interact_info = note_data.get('interactInfo', {})
                        if interact_info:
                            result['likes'] = parse_chinese_number(
                                interact_info.get('likedCount') or interact_info.get('likes') or 0)
                            result['favorites'] = parse_chinese_number(
                                interact_info.get('collectedCount') or interact_info.get('collectCount') or interact_info.get('bookmarkedCount') or 0)
                            result['comments'] = parse_chinese_number(
                                interact_info.get('commentCount') or interact_info.get('comments') or 0)
                            result['shares'] = parse_chinese_number(
                                interact_info.get('shareCount') or interact_info.get('shares') or 0)
                        # 직접 필드 fallback
                        if result.get('likes', 0) == 0:
                            result['likes'] = parse_chinese_number(note_data.get('likedCount', 0))
                        if result.get('favorites', 0) == 0:
                            result['favorites'] = parse_chinese_number(
                                note_data.get('collectedCount') or note_data.get('collectCount') or 0)
                        if result.get('comments', 0) == 0:
                            result['comments'] = parse_chinese_number(
                                note_data.get('commentCount') or note_data.get('comment_count') or 0)
                        if result.get('shares', 0) == 0:
                            result['shares'] = parse_chinese_number(
                                note_data.get('shareCount') or note_data.get('share_count') or 0)

                        user = note_data.get('user', {})
                        if user:
                            result['author'] = user.get('nickname') or user.get('name')
                            result['author_id'] = user.get('userId') or user.get('uid')

                        # imageList에서 썸네일 추출
                        image_list = note_data.get('imageList', [])
                        if image_list and len(image_list) > 0:
                            first_image = image_list[0]
                            if isinstance(first_image, dict):
                                result['thumbnail'] = first_image.get('urlDefault') or first_image.get('url')
                            elif isinstance(first_image, str):
                                result['thumbnail'] = first_image

                        if result.get('likes', 0) > 0 or result.get('author'):
                            logger.info(f"note.note에서 데이터 추출 성공: likes={result['likes']}, author={result['author']}")
                            return result

                except json.JSONDecodeError as e:
                    logger.debug(f"JSON 파싱 실패: {e}")
                    pass

            # 정규식으로 직접 추출 시도 (JSON 파싱 실패 시 백업)
            # interactInfo 내부 데이터 패턴
            interact_patterns = {
                'likes': [
                    r'"interactInfo"\s*:\s*\{[^}]*"likedCount"\s*:\s*"?(\d+)"?',
                    r'"likedCount"\s*:\s*"?(\d+)"?',
                    r'"liked_count"\s*:\s*"?(\d+)"?',
                ],
                'favorites': [
                    r'"interactInfo"\s*:\s*\{[^}]*"collectedCount"\s*:\s*"?(\d+)"?',
                    r'"collectedCount"\s*:\s*"?(\d+)"?',
                    r'"collected_count"\s*:\s*"?(\d+)"?',
                ],
                'comments': [
                    r'"interactInfo"\s*:\s*\{[^}]*"commentCount"\s*:\s*"?(\d+)"?',
                    r'"commentCount"\s*:\s*"?(\d+)"?',
                    r'"comment_count"\s*:\s*"?(\d+)"?',
                ],
                'shares': [
                    r'"interactInfo"\s*:\s*\{[^}]*"shareCount"\s*:\s*"?(\d+)"?',
                    r'"shareCount"\s*:\s*"?(\d+)"?',
                    r'"share_count"\s*:\s*"?(\d+)"?',
                ],
            }

            # 작성자 패턴 (로그인 사용자가 아닌 게시물 작성자)
            # noteDetailMap 내 user 정보를 찾아야 함
            author_patterns = [
                # noteDetailMap 내 user.nickname
                r'"noteDetailMap"[^}]*"user"\s*:\s*\{[^}]*"nickname"\s*:\s*"([^"]+)"',
                # note.user.nickname
                r'"note"\s*:\s*\{[^}]*"user"\s*:\s*\{[^}]*"nickname"\s*:\s*"([^"]+)"',
            ]

            title_patterns = [
                r'"noteDetailMap"[^}]*"title"\s*:\s*"([^"]+)"',
                r'"note"\s*:\s*\{[^}]*"title"\s*:\s*"([^"]+)"',
                r'"desc"\s*:\s*"([^"]{1,100})"',
            ]

            for key, pattern_list in interact_patterns.items():
                for pattern in pattern_list:
                    match = re.search(pattern, html)
                    if match:
                        value = match.group(1)
                        result[key] = int(value)
                        break

            # 작성자 추출 (첫 번째 매칭되는 것 사용)
            for pattern in author_patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    result['author'] = match.group(1)
                    break

            # 추가 작성자 패턴 (다양한 JSON 구조 대응)
            if not result.get('author'):
                extra_author_patterns = [
                    # noteCard 구조
                    r'"noteCard"[^}]*"user"[^}]*"nickname"\s*:\s*"([^"]+)"',
                    # 직접 user 객체
                    r'"user"\s*:\s*\{[^}]*"nickname"\s*:\s*"([^"]+)"',
                    # basicInfo 구조
                    r'"basicInfo"[^}]*"nickname"\s*:\s*"([^"]+)"',
                    # name 필드
                    r'"user"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                ]
                for pattern in extra_author_patterns:
                    match = re.search(pattern, html, re.DOTALL)
                    if match:
                        author_name = match.group(1)
                        # 로그인 사용자 이름이 아닌지 확인 (일반적으로 짧은 이름)
                        if author_name and len(author_name) < 30:
                            result['author'] = author_name
                            break

            # 제목 추출 (플레이스홀더 필터링)
            placeholder_texts = ["还没有简介", "暂无简介", "没有描述", "暂无描述", "暂无内容"]
            for pattern in title_patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    title_text = match.group(1)
                    # 플레이스홀더가 아닌 경우만 설정
                    if title_text and title_text.strip() not in placeholder_texts:
                        result['title'] = title_text
                        break

            # 내용(desc) 추출 — 표의 내용 컬럼에 저장
            content_patterns = [
                r'"noteDetailMap"[^}]*"desc"\s*:\s*"([^"]{10,})"',
                r'"note"\s*:\s*\{[^}]*"desc"\s*:\s*"([^"]{10,})"',
                r'"desc"\s*:\s*"([^"]{10,2000})"',
            ]
            for pattern in content_patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    content_text = match.group(1)
                    if content_text and content_text.strip() not in placeholder_texts:
                        from src.utils.text_utils import decode_unicode_escapes as _decode
                        result['content'] = _decode(content_text)
                        break

            # 썸네일 정규식 추출 (실제 콘텐츠 이미지 우선)
            if not result.get('thumbnail'):
                thumbnail_patterns = [
                    # 실제 콘텐츠 이미지 (sns-webpic, sns-img 등) - 이스케이프 포함
                    r'"urlDefault"\s*:\s*"(https?:\\?u?002F\\?u?002Fsns-[^"]+)"',
                    r'"url"\s*:\s*"(https?:\\?u?002F\\?u?002Fsns-[^"]+)"',
                    # 일반 형식
                    r'"urlDefault"\s*:\s*"(https?://sns-[^"]+\.xhscdn\.com[^"]+)"',
                    r'"url"\s*:\s*"(https?://sns-[^"]+\.xhscdn\.com[^"]+)"',
                    # imageList 내부 URL
                    r'"imageList"\s*:\s*\[[^\]]*"urlDefault"\s*:\s*"([^"]+)"',
                    r'"imageList"\s*:\s*\[[^\]]*"url"\s*:\s*"([^"]+)"',
                    # cover 필드
                    r'"cover"\s*:\s*\{[^}]*"urlDefault"\s*:\s*"([^"]+)"',
                    r'"cover"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"',
                    # infoList 내부
                    r'"infoList"\s*:\s*\[[^\]]*"url"\s*:\s*"(https?://[^"]+)"',
                    # 일반 xhscdn (마지막 fallback, 정적 자산 제외)
                    r'"urlDefault"\s*:\s*"(https?://(?!fe-static)[^"]+xhscdn[^"]+)"',
                ]
                for pattern in thumbnail_patterns:
                    match = re.search(pattern, html, re.DOTALL)
                    if match:
                        thumb_url = match.group(1)
                        # Unicode 이스케이프 디코딩 (\u002F -> /)
                        thumb_url = thumb_url.encode().decode('unicode_escape')
                        # 정적 자산 URL 제외
                        if 'fe-static' not in thumb_url and ('http://' in thumb_url or 'https://' in thumb_url):
                            result['thumbnail'] = thumb_url
                            break

            if result.get('likes', 0) > 0 or result.get('favorites', 0) > 0 or result.get('author'):
                logger.info(f"정규식으로 데이터 추출: likes={result['likes']}, author={result['author']}, thumbnail={'있음' if result.get('thumbnail') else '없음'}")
                return result

            return None

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
            has_session = False
            for cookie in cookies:
                if cookie.get("name") == "web_session" and cookie.get("value"):
                    if len(cookie.get("value", "")) > 50:
                        has_session = True
                        break

            if has_session:
                # 실제 페이지에서 로그인 상태 재검증
                page_source = self.driver.page_source
                # QR 로그인 화면 또는 로그인 유도 감지
                login_indicators = ['扫码', 'qrcode', '登录', '验证', 'login']
                login_detected = sum(1 for ind in login_indicators if ind.lower() in page_source.lower())
                if login_detected >= 2:
                    print(f"[XHS] 쿠키 만료 감지 (로그인 키워드 {login_detected}개)")
                    return False
                # 실제 게시물이나 피드가 보이는지 확인
                has_content = '小红书' in page_source or 'explore' in page_source
                if has_content:
                    print("[XHS] 로그인 세션 쿠키 유효")
                    return True
                print("[XHS] 쿠키 상태 불확실 - 유효로 간주")
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

    def _extract_comments(self, data_source) -> list:
        """
        댓글 데이터 추출

        Args:
            data_source: JSON 문자열 또는 딕셔너리

        Returns:
            댓글 리스트 [{"author": str, "text": str}, ...]
        """
        comments_list = []

        try:
            # JSON 파싱
            if isinstance(data_source, str):
                try:
                    data = json.loads(data_source)
                except json.JSONDecodeError:
                    return comments_list
            else:
                data = data_source

            # comment 섹션에서 댓글 추출
            comment_section = data.get('comment', {})

            # comments 배열 찾기 (여러 경로 시도)
            comments_data = (
                comment_section.get('comments', []) or
                comment_section.get('commentList', []) or
                data.get('comments', [])
            )

            for comment in comments_data[:self.max_comments]:
                if not isinstance(comment, dict):
                    continue

                # 작성자 추출
                user_info = comment.get('user', {}) or comment.get('userInfo', {})
                author = (
                    user_info.get('nickname') or
                    user_info.get('name') or
                    user_info.get('userName') or
                    comment.get('nickname') or
                    '익명'
                )

                # 댓글 내용 추출
                content = (
                    comment.get('content') or
                    comment.get('text') or
                    comment.get('note') or
                    ''
                )

                # 공백 정리 (여러 공백을 하나로)
                if content:
                    content = ' '.join(content.split())

                if author and content:
                    comments_list.append({
                        "author": author,
                        "text": content
                    })

            logger.info(f"댓글 {len(comments_list)}개 추출")

        except Exception as e:
            logger.debug(f"댓글 추출 실패: {e}")

        # JSON에서 못 찾으면 DOM에서 추출 시도
        if not comments_list and self.driver:
            try:
                from selenium.webdriver.common.by import By
                # XHS 댓글 DOM 패턴
                comment_elements = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    'div.comment-item, div[class*="comment"], div.note-comment'
                )
                for elem in comment_elements[:self.max_comments]:
                    try:
                        author_el = elem.find_element(By.CSS_SELECTOR,
                            '.user-name, .nickname, [class*="author"], [class*="name"]')
                        text_el = elem.find_element(By.CSS_SELECTOR,
                            '.content, .text, [class*="content"]')
                        if author_el and text_el:
                            comments_list.append({
                                "author": author_el.text.strip(),
                                "text": text_el.text.strip()
                            })
                    except Exception:
                        pass
                if comments_list:
                    logger.info(f"DOM에서 댓글 {len(comments_list)}개 추출")
            except Exception as e:
                logger.debug(f"DOM 댓글 추출 실패: {e}")

        return comments_list

    def _fetch_note_via_internal_api(self, note_id: str) -> Optional[Dict[str, Any]]:
        """
        XHS 내부 API를 Selenium 브라우저에서 직접 호출하여 노트 상세 데이터 가져오기
        window._webmsxyw 함수로 서명을 생성하고 fetch로 API 호출

        Returns:
            interact_info dict 또는 None
        """
        if not self.driver:
            return None

        try:
            # 브라우저에서 직접 XHS 내부 API 호출
            api_result = self.driver.execute_script("""
                async function fetchNoteDetail(noteId) {
                    try {
                        var url = '/api/sns/web/v1/feed';
                        var data = JSON.stringify({"source_note_id": noteId});

                        // 서명 생성
                        var signs = {};
                        if (window._webmsxyw) {
                            signs = window._webmsxyw(url, data);
                        }

                        var headers = {
                            'Content-Type': 'application/json',
                            'X-S': signs['X-s'] || '',
                            'X-T': signs['X-t'] || String(Date.now())
                        };
                        if (signs['X-s-common']) {
                            headers['X-S-Common'] = signs['X-s-common'];
                        }

                        var resp = await fetch('https://edith.xiaohongshu.com' + url, {
                            method: 'POST',
                            headers: headers,
                            body: data,
                            credentials: 'include'
                        });

                        var json = await resp.json();
                        if (json && json.data && json.data.items && json.data.items.length > 0) {
                            var noteCard = json.data.items[0].note_card;
                            if (noteCard) {
                                return {
                                    title: noteCard.title || noteCard.desc || '',
                                    desc: noteCard.desc || '',
                                    interact_info: noteCard.interact_info || {},
                                    user: noteCard.user || {},
                                    image_list: noteCard.image_list || [],
                                    tag_list: noteCard.tag_list || []
                                };
                            }
                        }
                        return null;
                    } catch(e) {
                        return {error: e.toString()};
                    }
                }
                return await fetchNoteDetail(arguments[0]);
            """, note_id)

            if not api_result:
                logger.info("내부 API: 응답 없음")
                return None

            if api_result.get('error'):
                logger.warning(f"내부 API 오류: {api_result['error']}")
                return None

            logger.info(f"내부 API 성공: interact_info keys={list(api_result.get('interact_info', {}).keys())}")
            return api_result

        except Exception as e:
            logger.warning(f"내부 API 호출 실패: {e}")
            # 디버그: _webmsxyw 존재 여부 확인
            try:
                has_sign = self.driver.execute_script("return typeof window._webmsxyw")
                logger.info(f"_webmsxyw 타입: {has_sign}")
            except Exception:
                pass
            return None

    def _fetch_comments_via_api(self, note_id: str) -> list:
        """
        XHS 내부 API로 댓글 목록 가져오기
        """
        if not self.driver:
            return []

        try:
            comments = self.driver.execute_script("""
                async function fetchComments(noteId) {
                    try {
                        var url = '/api/sns/web/v2/comment/page?note_id=' + noteId + '&cursor=&top_comment_id=&image_formats=jpg,webp,avif';

                        var signs = {};
                        if (window._webmsxyw) {
                            signs = window._webmsxyw(url, undefined);
                        }

                        var headers = {
                            'X-S': signs['X-s'] || '',
                            'X-T': signs['X-t'] || String(Date.now())
                        };
                        if (signs['X-s-common']) {
                            headers['X-S-Common'] = signs['X-s-common'];
                        }

                        var resp = await fetch('https://edith.xiaohongshu.com' + url, {
                            method: 'GET',
                            headers: headers,
                            credentials: 'include'
                        });

                        var json = await resp.json();
                        var result = [];
                        if (json && json.data && json.data.comments) {
                            var comments = json.data.comments;
                            for (var i = 0; i < Math.min(comments.length, 10); i++) {
                                var c = comments[i];
                                result.push({
                                    author: (c.user_info && c.user_info.nickname) || '익명',
                                    text: c.content || '',
                                    likes: parseInt(c.like_count) || 0
                                });
                            }
                        }
                        return result;
                    } catch(e) {
                        return [];
                    }
                }
                return await fetchComments(arguments[0]);
            """, note_id)

            if comments:
                logger.info(f"내부 API 댓글 {len(comments)}개 수집")
            return comments or []

        except Exception as e:
            logger.warning(f"댓글 API 호출 실패: {e}")
            return []

    def _extract_post_data(self, url: str) -> dict:
        """
        게시물 데이터 추출 - CDP 네트워크 가로채기 + __INITIAL_STATE__ + DOM 복합 방식

        Args:
            url: 게시물 URL

        Returns:
            게시물 데이터 딕셔너리
        """
        note_id = self._extract_note_id(url)
        result = {
            "platform": "xiaohongshu",
            "url": url,
            "note_id": note_id,
            "author": None,
            "author_id": None,
            "title": None,
            "content": None,
            "likes": 0,
            "favorites": 0,
            "comments": 0,
            "shares": 0,
            "views": None,
            "thumbnail": None,
            "comments_list": [],
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            # === 게시물 페이지 직접 로드 ===
            logger.info(f"페이지 로드: {url}")
            self.driver.get(url)
            time.sleep(self.PAGE_LOAD_WAIT)

            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)
            logger.info(f"현재 URL: {self.driver.current_url}")
            logger.info(f"페이지 제목: {self.driver.title}")

            # === DOM 구조 덤프 (디버그) ===
            try:
                dom_dump = self.driver.execute_script("""
                    // 페이지의 주요 구조 덤프
                    var result = {};
                    result.url = window.location.href;
                    result.title = document.title;

                    // body의 직속 자식 요소들
                    var bodyChildren = [];
                    for (var i = 0; i < document.body.children.length && i < 10; i++) {
                        var el = document.body.children[i];
                        var cn = (typeof el.className === 'string') ? el.className : '';
                        bodyChildren.push(el.tagName + '.' + cn.substring(0, 80) + '#' + (el.id || ''));
                    }
                    result.body_children = bodyChildren;

                    // 모든 class에 'detail', 'modal', 'engage', 'interact', 'like', 'comment' 포함된 요소
                    var keywords = ['detail', 'modal', 'engage', 'interact', 'comment', 'collect'];
                    var found = {};
                    keywords.forEach(function(kw) {
                        var els = document.querySelectorAll('[class*="' + kw + '"]');
                        found[kw] = [];
                        for (var j = 0; j < els.length && j < 5; j++) {
                            var cn2 = (typeof els[j].className === 'string') ? els[j].className : '';
                            found[kw].push(els[j].tagName + '.' + cn2.substring(0, 100));
                        }
                    });
                    result.keyword_elements = found;

                    // engage/interact 영역의 자식 텍스트
                    var engageEls = document.querySelectorAll('[class*="engage"], [class*="interact"]');
                    result.engage_texts = [];
                    for (var k = 0; k < engageEls.length && k < 3; k++) {
                        var cn3 = (typeof engageEls[k].className === 'string') ? engageEls[k].className : '';
                        result.engage_texts.push({
                            class: cn3.substring(0, 80),
                            text: engageEls[k].textContent.substring(0, 200)
                        });
                    }

                    return result;
                """)
                if dom_dump:
                    logger.info(f"DOM 덤프 - URL: {dom_dump.get('url')}")
                    logger.info(f"DOM 덤프 - title: {dom_dump.get('title')}")
                    logger.info(f"DOM 덤프 - body_children: {dom_dump.get('body_children')}")
                    for kw, els in (dom_dump.get('keyword_elements') or {}).items():
                        if els:
                            logger.info(f"DOM 덤프 - [{kw}]: {els}")
                    for et in (dom_dump.get('engage_texts') or []):
                        logger.info(f"DOM 덤프 - engage text: class={et.get('class')}, text={et.get('text')}")
            except Exception as e:
                logger.warning(f"DOM 덤프 실패: {e}")

            # === 방법 1: performance log에서 XHS API 응답 캡처 ===
            api_data_found = False
            try:
                # XHR/fetch 응답 데이터를 JavaScript로 직접 캡처
                api_capture = self.driver.execute_script("""
                    // window.__INITIAL_STATE__에서 먼저 시도
                    if (window.__INITIAL_STATE__) {
                        var state = window.__INITIAL_STATE__;
                        var noteState = state.note || {};
                        var noteDetailMap = noteState.noteDetailMap || noteState.noteFetching || {};

                        // noteDetailMap에서 데이터 찾기
                        var keys = Object.keys(noteDetailMap);
                        for (var i = 0; i < keys.length; i++) {
                            var entry = noteDetailMap[keys[i]];
                            if (entry && entry.note) {
                                var note = entry.note;
                                var interact = note.interactInfo || {};
                                return {
                                    source: 'INITIAL_STATE',
                                    title: note.title || '',
                                    desc: note.desc || '',
                                    likes: parseInt(interact.likedCount) || 0,
                                    favorites: parseInt(interact.collectedCount) || 0,
                                    comments: parseInt(interact.commentCount) || 0,
                                    shares: parseInt(interact.shareCount) || 0,
                                    author: (note.user && note.user.nickname) || '',
                                    author_id: (note.user && (note.user.userId || note.user.uid)) || '',
                                    thumbnail: (note.imageList && note.imageList[0] && (note.imageList[0].urlDefault || note.imageList[0].url)) || '',
                                    all_interact_keys: Object.keys(interact),
                                    all_note_keys: Object.keys(note).slice(0, 20)
                                };
                            }
                        }

                        // 구버전 구조
                        if (noteState.note) {
                            var note2 = noteState.note;
                            var interact2 = note2.interactInfo || {};
                            return {
                                source: 'INITIAL_STATE_legacy',
                                title: note2.title || '',
                                desc: note2.desc || '',
                                likes: parseInt(interact2.likedCount) || parseInt(note2.likedCount) || 0,
                                favorites: parseInt(interact2.collectedCount) || parseInt(note2.collectedCount) || 0,
                                comments: parseInt(interact2.commentCount) || parseInt(note2.commentCount) || 0,
                                shares: parseInt(interact2.shareCount) || parseInt(note2.shareCount) || 0,
                                author: (note2.user && note2.user.nickname) || '',
                                author_id: (note2.user && note2.user.userId) || '',
                                thumbnail: (note2.imageList && note2.imageList[0] && (note2.imageList[0].urlDefault || note2.imageList[0].url)) || '',
                                all_interact_keys: Object.keys(interact2),
                                all_note_keys: Object.keys(note2).slice(0, 20)
                            };
                        }

                        // 디버깅: state 구조 출력
                        return {
                            source: 'DEBUG',
                            state_keys: Object.keys(state).slice(0, 10),
                            note_keys: Object.keys(noteState).slice(0, 10),
                            noteDetailMap_keys: keys.slice(0, 5),
                            has_note: !!noteState.note
                        };
                    }
                    return {source: 'NO_STATE'};
                """)

                if api_capture:
                    print(f"[XHS] API 캡처 결과: source={api_capture.get('source')}")

                    if api_capture.get('source') == 'DEBUG':
                        print(f"[XHS] DEBUG - state_keys: {api_capture.get('state_keys')}")
                        print(f"[XHS] DEBUG - note_keys: {api_capture.get('note_keys')}")
                        print(f"[XHS] DEBUG - noteDetailMap_keys: {api_capture.get('noteDetailMap_keys')}")

                    elif api_capture.get('source') == 'NO_STATE':
                        print("[XHS] __INITIAL_STATE__ 없음")

                    else:
                        # 데이터 추출 성공
                        logger.info(f"  interact_keys: {api_capture.get('all_interact_keys')}")
                        logger.info(f"  note_keys: {api_capture.get('all_note_keys')}")

                        if api_capture.get('title'):
                            result['title'] = api_capture['title']
                        if api_capture.get('desc') and not result.get('content'):
                            result['content'] = api_capture['desc']
                            if not result.get('title'):
                                result['title'] = api_capture['desc'][:100]
                        if api_capture.get('likes', 0) > 0:
                            result['likes'] = api_capture['likes']
                        if api_capture.get('favorites', 0) > 0:
                            result['favorites'] = api_capture['favorites']
                        if api_capture.get('comments', 0) > 0:
                            result['comments'] = api_capture['comments']
                        if api_capture.get('shares', 0) > 0:
                            result['shares'] = api_capture['shares']
                        if api_capture.get('author'):
                            result['author'] = api_capture['author']
                        if api_capture.get('author_id'):
                            result['author_id'] = api_capture['author_id']
                        if api_capture.get('thumbnail'):
                            thumb = api_capture['thumbnail']
                            if '\\u002F' in thumb:
                                thumb = thumb.encode().decode('unicode_escape')
                            result['thumbnail'] = thumb

                        api_data_found = (result['likes'] > 0 or result.get('author'))
                        logger.info(f"캡처 최종: likes={result['likes']}, comments={result['comments']}, favorites={result['favorites']}, shares={result['shares']}, author={result.get('author', '')}")

            except Exception as e:
                logger.warning(f"API 캡처 실패: {e}")

            # === 방법 2: HTML 소스에서 정규식 추출 (fallback) ===
            if not api_data_found:
                try:
                    page_source = self.driver.page_source
                    html_result = self._extract_data_from_html(page_source, url, note_id or "")
                    if html_result:
                        result.update(html_result)
                        logger.info(f"HTML 추출: likes={result['likes']}, comments={result['comments']}")
                except Exception as e:
                    logger.debug(f"HTML 추출 실패: {e}")

            # === 내부 API로 부족한 데이터 보충 ===
            if note_id and (result['comments'] == 0 or result['favorites'] == 0 or result['shares'] == 0):
                logger.info("내부 API로 댓글/저장/공유 수 보충 시도...")
                api_data = self._fetch_note_via_internal_api(note_id)
                if api_data and not api_data.get('error'):
                    interact = api_data.get('interact_info', {})
                    if interact:
                        api_likes = int(interact.get('liked_count') or 0)
                        api_comments = int(interact.get('comment_count') or 0)
                        api_favorites = int(interact.get('collected_count') or 0)
                        api_shares = int(interact.get('share_count') or 0)
                        logger.info(f"내부 API 데이터: likes={api_likes}, comments={api_comments}, favorites={api_favorites}, shares={api_shares}")
                        if api_likes > 0 and result['likes'] == 0:
                            result['likes'] = api_likes
                        if api_comments > 0:
                            result['comments'] = api_comments
                        if api_favorites > 0:
                            result['favorites'] = api_favorites
                        if api_shares > 0:
                            result['shares'] = api_shares
                    # 작성자 보충
                    user = api_data.get('user', {})
                    if user and not result.get('author'):
                        result['author'] = user.get('nickname') or user.get('name')
                    # 제목 보충
                    if not result.get('title') and api_data.get('title'):
                        result['title'] = api_data['title']

                # 댓글 수집
                if self.collect_comments and not result.get('comments_list'):
                    logger.info("내부 API로 댓글 목록 수집 시도...")
                    result['comments_list'] = self._fetch_comments_via_api(note_id)

            # DOM에서 댓글 수집 (API 실패 시 fallback)
            if self.collect_comments and not result.get('comments_list'):
                logger.info("DOM에서 댓글 수집 시도...")
                try:
                    dom_comments = self.driver.execute_script("""
                        var comments = [];
                        var seen = {};
                        // comment-item만 선택 (parent-comment은 중복 포함)
                        var items = document.querySelectorAll('.comment-item');
                        if (items.length === 0) items = document.querySelectorAll('[class*="comment-item"]');
                        for (var i = 0; i < items.length && comments.length < arguments[0]; i++) {
                            var item = items[i];
                            var authorEl = item.querySelector('.author-wrapper .name, .user-name, .nickname, [class*="name"]');
                            var contentEl = item.querySelector('.note-text, .content, [class*="content"]');
                            if (authorEl && contentEl) {
                                var key = authorEl.textContent.trim() + '|' + contentEl.textContent.trim();
                                if (!seen[key]) {
                                    seen[key] = true;
                                    comments.push({
                                        author: authorEl.textContent.trim(),
                                        text: contentEl.textContent.trim()
                                    });
                                }
                            }
                        }
                        return comments;
                    """, self.max_comments)
                    if dom_comments:
                        result['comments_list'] = dom_comments
                        logger.info(f"DOM에서 댓글 {len(dom_comments)}개 수집")
                    else:
                        logger.info("DOM 댓글 없음")
                except Exception as e:
                    logger.warning(f"DOM 댓글 수집 실패: {e}")

            # === DOM 셀렉터 방식 (추가 보충) ===
            need_dom = (result['comments'] == 0 or result['favorites'] == 0
                        or not result.get('thumbnail') or not result.get('author'))
            if need_dom:
                logger.info("DOM 셀렉터로 부족한 데이터 보충 시도...")

            # === 최신 XHS: 모달/상세 영역 내 engage-bar에서 숫자 수집 ===
            if result['comments'] == 0 or result['favorites'] == 0 or result['shares'] == 0:
                try:
                    engage_data = self.driver.execute_script("""
                        var r = {likes: '', favorites: '', comments: '', shares: ''};

                        // 모달 또는 상세 컨테이너 찾기 (피드가 아닌 현재 게시물)
                        var modal = document.querySelector(
                            '.note-detail-mask, .note-detail, #noteContainer, ' +
                            '[class*="note-detail"], [class*="detail-container"], ' +
                            '.overlay-container, .modal-container'
                        );
                        var scope = modal || document;

                        // 방법1: like-wrapper, collect-wrapper, chat-wrapper, share-wrapper 순서
                        var likeW = scope.querySelector('[class*="like-wrapper"], [class*="like-active"], [class*="like "]');
                        var collectW = scope.querySelector('[class*="collect-wrapper"], [class*="collect-active"], [class*="collect "]');
                        var chatW = scope.querySelector('[class*="chat-wrapper"], [class*="comment-wrapper"], [class*="chat "]');
                        var shareW = scope.querySelector('[class*="share-wrapper"], [class*="share "]');

                        function getCount(el) {
                            if (!el) return '';
                            var spans = el.querySelectorAll('span');
                            for (var i = 0; i < spans.length; i++) {
                                var t = spans[i].textContent.trim();
                                if (/^[\\d.]+[万wk亿]?$/i.test(t)) return t;
                            }
                            // 직접 텍스트에서
                            var m = el.textContent.match(/[\\d.]+[万wk亿]?/i);
                            return m ? m[0] : '';
                        }

                        r.likes = getCount(likeW);
                        r.favorites = getCount(collectW);
                        r.comments = getCount(chatW);
                        r.shares = getCount(shareW);

                        // 방법2: engage-bar 내 순서대로 (fallback)
                        if (!r.likes && !r.favorites) {
                            var bar = scope.querySelector('[class*="engage-bar"], [class*="interact-container"]');
                            if (bar) {
                                var items = bar.querySelectorAll('[class*="wrapper"], [class*="item"]');
                                var counts = [];
                                items.forEach(function(item) {
                                    var m = item.textContent.match(/[\\d.]+[万wk亿]?/i);
                                    if (m) counts.push(m[0]);
                                });
                                if (counts.length >= 1 && !r.likes) r.likes = counts[0];
                                if (counts.length >= 2 && !r.favorites) r.favorites = counts[1];
                                if (counts.length >= 3 && !r.comments) r.comments = counts[2];
                                if (counts.length >= 4 && !r.shares) r.shares = counts[3];
                            }
                        }

                        r.modal_found = !!modal;
                        r.scope_tag = scope.tagName + '.' + (scope.className || '').substring(0, 50);
                        return r;
                    """)
                    if engage_data:
                        logger.info(f"DOM engage 데이터: {engage_data}")
                        if engage_data.get('likes') and result['likes'] == 0:
                            result['likes'] = self._parse_count(engage_data['likes'])
                        if engage_data.get('favorites') and result['favorites'] == 0:
                            result['favorites'] = self._parse_count(engage_data['favorites'])
                        if engage_data.get('comments') and result['comments'] == 0:
                            result['comments'] = self._parse_count(engage_data['comments'])
                        if engage_data.get('shares') and result['shares'] == 0:
                            result['shares'] = self._parse_count(engage_data['shares'])
                except Exception as e:
                    logger.debug(f"DOM engage 추출 실패: {e}")

            # === 최신 XHS: 전체 페이지 텍스트에서 숫자 패턴 추출 ===
            if result['comments'] == 0 or result['favorites'] == 0:
                try:
                    dom_data = self.driver.execute_script("""
                        var r = {};
                        // XHS 2024+ 구조: data-* 속성이나 aria-label에 숫자 정보
                        var btns = document.querySelectorAll('button, [role="button"], .operation-btn, [class*="btn"]');
                        btns.forEach(function(btn) {
                            var label = btn.getAttribute('aria-label') || btn.getAttribute('data-label') || '';
                            var text = btn.textContent.trim();
                            if (/赞|like/i.test(label) || /赞|like/i.test(btn.className)) {
                                var m = text.match(/[\\d.]+[万wk亿]?/i);
                                if (m) r.likes = m[0];
                            }
                            if (/收藏|collect|star/i.test(label) || /collect|star/i.test(btn.className)) {
                                var m = text.match(/[\\d.]+[万wk亿]?/i);
                                if (m) r.favorites = m[0];
                            }
                            if (/评论|comment|chat/i.test(label) || /comment|chat/i.test(btn.className)) {
                                var m = text.match(/[\\d.]+[万wk亿]?/i);
                                if (m) r.comments = m[0];
                            }
                            if (/分享|share/i.test(label) || /share/i.test(btn.className)) {
                                var m = text.match(/[\\d.]+[万wk亿]?/i);
                                if (m) r.shares = m[0];
                            }
                        });
                        return r;
                    """)
                    if dom_data:
                        logger.info(f"DOM 버튼 데이터: {dom_data}")
                        if dom_data.get('likes') and result['likes'] == 0:
                            result['likes'] = self._parse_count(dom_data['likes'])
                        if dom_data.get('favorites') and result['favorites'] == 0:
                            result['favorites'] = self._parse_count(dom_data['favorites'])
                        if dom_data.get('comments') and result['comments'] == 0:
                            result['comments'] = self._parse_count(dom_data['comments'])
                        if dom_data.get('shares') and result['shares'] == 0:
                            result['shares'] = self._parse_count(dom_data['shares'])
                except Exception as e:
                    logger.debug(f"DOM 버튼 데이터 추출 실패: {e}")

            # === 썸네일 추출 (DOM) ===
            thumbnail_selectors = [
                # 실제 콘텐츠 이미지 우선
                "//div[contains(@class, 'swiper-slide')]//img[contains(@src, 'sns-')]",
                "//div[contains(@class, 'carousel')]//img[contains(@src, 'sns-')]",
                "//div[contains(@class, 'media')]//img[contains(@src, 'sns-')]",
                "//img[contains(@src, 'sns-webpic')]",
                "//img[contains(@src, 'sns-img')]",
                # 일반 이미지 (fallback)
                "//div[contains(@class, 'swiper-slide')]//img",
                "//div[contains(@class, 'carousel')]//img",
                "//div[contains(@class, 'media')]//img",
                "//div[contains(@class, 'note')]//img[contains(@class, 'image')]",
            ]
            for selector in thumbnail_selectors:
                try:
                    img_elem = self.driver.find_element(By.XPATH, selector)
                    src = img_elem.get_attribute('src')
                    # 정적 자산 제외, 실제 콘텐츠 이미지만
                    if src and 'fe-static' not in src and ('xhscdn' in src or 'xiaohongshu' in src):
                        result["thumbnail"] = src
                        logger.info(f"DOM에서 썸네일 추출: {src[:50]}...")
                        break
                except NoSuchElementException:
                    continue

            # === 작성자 정보 (최신 샤오홍슈 구조) ===
            author_selectors = [
                # 모달/상세 페이지 구조
                "//a[contains(@href, '/user/profile')]//span[contains(@class, 'name')]",
                "//div[contains(@class, 'author-container')]//span[contains(@class, 'name')]",
                "//div[contains(@class, 'author-wrapper')]//span[contains(@class, 'username')]",
                "//a[contains(@class, 'author')]//span[contains(@class, 'name')]",
                # 프로필 링크 내 이름
                "//a[contains(@href, '/user/profile')]//div[contains(@class, 'name')]",
                "//a[contains(@href, '/user/profile')]/span",
                # 일반 구조
                "//div[contains(@class, 'note-top')]//a[contains(@class, 'author')]",
                "//div[contains(@class, 'info')]//a[contains(@class, 'name')]",
                "//a[contains(@class, 'author')]//span",
                "//div[contains(@class, 'author')]//span[@class='username']",
                "//span[contains(@class, 'user-name')]",
                "//div[contains(@class, 'user-info')]//span",
            ]
            for selector in author_selectors:
                try:
                    author_elem = self.driver.find_element(By.XPATH, selector)
                    text = author_elem.text.strip()
                    # 로그인 사용자가 아닌 게시물 작성자인지 확인
                    if text and len(text) > 0:
                        result["author"] = text
                        break
                except NoSuchElementException:
                    continue

            # === 제목 (최신 구조) ===
            # 플레이스홀더 텍스트 리스트
            placeholder_texts = ["还没有简介", "暂无简介", "没有描述", "暂无描述", "暂无内容"]

            title_selectors = [
                "//div[@id='detail-title']",
                "//div[contains(@class, 'title')]//span",
                "//div[contains(@class, 'note-content')]//div[contains(@class, 'title')]",
                "//h1",
            ]
            for selector in title_selectors:
                try:
                    title_elem = self.driver.find_element(By.XPATH, selector)
                    text = title_elem.text.strip()
                    # 기본 페이지 제목/플레이스홀더가 아닌지 확인
                    if text and not text.startswith("小红书") and "沪ICP" not in text:
                        if text not in placeholder_texts:
                            result["title"] = text[:100]
                            break
                except NoSuchElementException:
                    continue

            # === 본문 내용 (content) ===
            content_selectors = [
                "//div[@id='detail-desc']",
                "//div[contains(@class, 'desc')]//span[@class='desc']",
                "//div[contains(@class, 'note-content')]//div[contains(@class, 'desc')]",
                "//div[contains(@class, 'note-scroller')]//span[contains(@class, 'desc')]",
                "//span[contains(@class, 'note-text')]",
            ]
            for selector in content_selectors:
                try:
                    content_elem = self.driver.find_element(By.XPATH, selector)
                    text = content_elem.text.strip()
                    # 플레이스홀더가 아닌 실제 콘텐츠인지 확인
                    if text and len(text) > 5 and not text.startswith("小红书") and "沪ICP" not in text:
                        if text not in placeholder_texts:
                            result["content"] = text[:500]
                            # title이 없으면 content 첫 부분을 title로 사용
                            if not result.get("title"):
                                result["title"] = text[:100]
                            break
                except NoSuchElementException:
                    continue

            # === 좋아요/즐겨찾기/댓글 (최신 구조: engage-bar) ===
            # 최신 샤오홍슈는 engage-bar 안에 상호작용 버튼이 있음
            engage_bar_selectors = [
                # 좋아요
                ("likes", [
                    "//div[contains(@class, 'engage-bar')]//span[contains(@class, 'like-wrapper')]//span[contains(@class, 'count')]",
                    "//div[contains(@class, 'engage-bar')]//div[contains(@class, 'like')]//span",
                    "//button[contains(@class, 'like')]//span[contains(@class, 'count')]",
                    "//span[contains(@class, 'like-count')]",
                ]),
                # 즐겨찾기
                ("favorites", [
                    "//div[contains(@class, 'engage-bar')]//span[contains(@class, 'collect-wrapper')]//span[contains(@class, 'count')]",
                    "//div[contains(@class, 'engage-bar')]//div[contains(@class, 'collect')]//span",
                    "//button[contains(@class, 'collect')]//span[contains(@class, 'count')]",
                    "//span[contains(@class, 'collect-count')]",
                ]),
                # 댓글
                ("comments", [
                    "//div[contains(@class, 'engage-bar')]//span[contains(@class, 'chat-wrapper')]//span[contains(@class, 'count')]",
                    "//div[contains(@class, 'engage-bar')]//div[contains(@class, 'chat')]//span",
                    "//span[contains(@class, 'comment-count')]",
                    "//div[contains(@class, 'comments')]//span[contains(@class, 'total')]",
                ]),
            ]

            for metric_name, selectors in engage_bar_selectors:
                for selector in selectors:
                    try:
                        elem = self.driver.find_element(By.XPATH, selector)
                        text = elem.text.strip()
                        # "评论" 텍스트 제거
                        if "评论" in text:
                            text = text.replace("评论", "").strip()
                        count = self._parse_count(text)
                        if count > 0:
                            result[metric_name] = count
                            break
                    except NoSuchElementException:
                        continue

            # === 공유 수 ===
            share_selectors = [
                "//div[contains(@class, 'engage-bar')]//span[contains(@class, 'share-wrapper')]//span[contains(@class, 'count')]",
                "//span[contains(@class, 'share-count')]",
            ]
            for selector in share_selectors:
                try:
                    share_elem = self.driver.find_element(By.XPATH, selector)
                    result["shares"] = self._parse_count(share_elem.text)
                    if result["shares"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === 조회수 ===
            view_selectors = [
                "//span[contains(@class, 'view-count')]",
                "//span[contains(@class, 'read-count')]",
                "//span[contains(text(), '浏览')]",
            ]
            for selector in view_selectors:
                try:
                    view_elem = self.driver.find_element(By.XPATH, selector)
                    result["views"] = self._parse_count(view_elem.text)
                    if result["views"] and result["views"] > 0:
                        break
                except NoSuchElementException:
                    continue

            # === DOM 방식도 실패 시 JavaScript로 재시도 ===
            if result["likes"] == 0 and result["favorites"] == 0:
                try:
                    # noteDetailMap 구조에서 직접 데이터 추출
                    page_data = self.driver.execute_script("""
                        if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note) {
                            var noteState = window.__INITIAL_STATE__.note;
                            var result = {};

                            // noteDetailMap 구조
                            if (noteState.noteDetailMap) {
                                var keys = Object.keys(noteState.noteDetailMap);
                                if (keys.length > 0) {
                                    var noteData = noteState.noteDetailMap[keys[0]];
                                    if (noteData && noteData.note) {
                                        var note = noteData.note;
                                        result.title = note.title || note.desc || '';

                                        if (note.interactInfo) {
                                            result.likes = parseInt(note.interactInfo.likedCount) || 0;
                                            result.favorites = parseInt(note.interactInfo.collectedCount) || 0;
                                            result.comments = parseInt(note.interactInfo.commentCount) || 0;
                                            result.shares = parseInt(note.interactInfo.shareCount) || 0;
                                        }

                                        if (note.user) {
                                            result.author = note.user.nickname || note.user.name || '';
                                            result.authorId = note.user.userId || note.user.uid || '';
                                        }
                                    }
                                }
                            }

                            // 구버전 구조 fallback
                            if (!result.author && noteState.note) {
                                var note = noteState.note;
                                result.title = note.title || note.desc || '';
                                result.likes = parseInt(note.likedCount) || 0;
                                result.favorites = parseInt(note.collectedCount) || 0;
                                result.comments = parseInt(note.commentCount) || 0;

                                if (note.user) {
                                    result.author = note.user.nickname || '';
                                    result.authorId = note.user.userId || '';
                                }
                            }

                            return result;
                        }
                        return null;
                    """)

                    if page_data:
                        # JavaScript에서 반환된 객체 직접 처리
                        if isinstance(page_data, dict):
                            if page_data.get('title'):
                                result['title'] = page_data['title']
                            if page_data.get('likes'):
                                result['likes'] = int(page_data['likes'])
                            if page_data.get('favorites'):
                                result['favorites'] = int(page_data['favorites'])
                            if page_data.get('comments'):
                                result['comments'] = int(page_data['comments'])
                            if page_data.get('shares'):
                                result['shares'] = int(page_data['shares'])
                            if page_data.get('author'):
                                result['author'] = page_data['author']
                            if page_data.get('authorId'):
                                result['author_id'] = page_data['authorId']
                            logger.info(f"JavaScript 객체에서 데이터 추출 성공: likes={result['likes']}, author={result['author']}")
                        elif isinstance(page_data, str):
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
            json_str: JSON 문자열 또는 딕셔너리
            result: 결과 딕셔너리 (업데이트됨)
        """
        try:
            # 이미 딕셔너리인 경우
            if isinstance(json_str, dict):
                data = json_str
            else:
                # JSON 문자열 파싱 시도
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    data = None

            if data:
                # noteDetailMap 구조 처리
                note_detail_map = data.get('note', {}).get('noteDetailMap', {})
                if note_detail_map:
                    for key, value in note_detail_map.items():
                        if isinstance(value, dict) and 'note' in value:
                            note = value.get('note', {})
                            interact_info = note.get('interactInfo', {})
                            if interact_info:
                                result['likes'] = parse_chinese_number(interact_info.get('likedCount', 0))
                                result['favorites'] = parse_chinese_number(interact_info.get('collectedCount', 0))
                                result['comments'] = parse_chinese_number(interact_info.get('commentCount', 0))
                                result['shares'] = parse_chinese_number(interact_info.get('shareCount', 0))

                            user = note.get('user', {})
                            if user:
                                result['author'] = user.get('nickname') or user.get('name')
                                result['author_id'] = user.get('userId') or user.get('uid')

                            result['title'] = note.get('title') or note.get('desc')
                            return

                # interactInfo 직접 찾기
                if 'interactInfo' in str(data):
                    interact_info = data.get('interactInfo', {})
                    if interact_info:
                        result['likes'] = parse_chinese_number(interact_info.get('likedCount', 0))
                        result['favorites'] = parse_chinese_number(interact_info.get('collectedCount', 0))
                        result['comments'] = parse_chinese_number(interact_info.get('commentCount', 0))
                        result['shares'] = parse_chinese_number(interact_info.get('shareCount', 0))
                        return

            # 문자열에서 정규식으로 추출
            if isinstance(json_str, str):
                # interactInfo 내부 데이터 우선
                patterns = [
                    (r'"interactInfo"[^}]*"likedCount"\s*:\s*"?(\d+)"?', "likes"),
                    (r'"interactInfo"[^}]*"collectedCount"\s*:\s*"?(\d+)"?', "favorites"),
                    (r'"interactInfo"[^}]*"commentCount"\s*:\s*"?(\d+)"?', "comments"),
                    (r'"interactInfo"[^}]*"shareCount"\s*:\s*"?(\d+)"?', "shares"),
                    (r'"likedCount"\s*:\s*"?(\d+)"?', "likes"),
                    (r'"collectedCount"\s*:\s*"?(\d+)"?', "favorites"),
                    (r'"commentCount"\s*:\s*"?(\d+)"?', "comments"),
                    (r'"shareCount"\s*:\s*"?(\d+)"?', "shares"),
                ]

                for pattern, key in patterns:
                    if result.get(key, 0) == 0:  # 아직 값이 없는 경우만
                        match = re.search(pattern, json_str, re.IGNORECASE)
                        if match:
                            result[key] = int(match.group(1))

                # 작성자 추출 (noteDetailMap 내 user)
                if not result.get('author'):
                    author_match = re.search(r'"user"\s*:\s*\{[^}]*"nickname"\s*:\s*"([^"]+)"', json_str)
                    if author_match:
                        result['author'] = author_match.group(1)

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

        # 인증 모드 (headless=False)일 때는 바로 Selenium으로 (QR 로그인용)
        if not self.headless and not IS_CLOUD:
            print("[샤오홍슈] 인증 모드 - 브라우저 창을 여는 중...")
            logger.info("인증 모드 활성화 - Selenium으로 직접 크롤링 (API 스킵)")
            # Selenium으로 직접 진행
        else:
            # 1. API 방식 우선 시도 (Cloud 환경에서 효과적)
            if self.use_api and self.session:
                print("[샤오홍슈] API로 크롤링 시도 중...")
                logger.info("requests API로 크롤링 시도...")
                result = self._crawl_via_api(url)
                if result and (result.get('likes', 0) > 0 or result.get('favorites', 0) > 0 or result.get('author')):
                    logger.info(f"API 크롤링 성공: likes={result.get('likes')}, favorites={result.get('favorites')}")
                    return result
                print("[샤오홍슈] API 실패, 브라우저 모드로 전환...")
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
                "error": "QR 인증이 필요합니다. 로컬 환경에서 실행하거나 쿠키를 설정해주세요.",
            }

        # 3. Selenium fallback (로컬 환경)
        if self.driver is None:
            print("[샤오홍슈] Chrome 브라우저 시작 중...")
            logger.info("Chrome WebDriver 생성 중...")
            self.driver = self._create_driver()
            print("[샤오홍슈] 브라우저 창이 열렸습니다.")

        # 로그인 확인 및 수행
        if not self.is_logged_in and auto_login:
            print("[샤오홍슈] QR 코드 로그인을 시작합니다...")
            print("[샤오홍슈] 브라우저 창에서 QR 코드를 스캔하세요. (최대 2분)")
            self.login()
            print("[샤오홍슈] 로그인 완료!")

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
                error_msg = str(e)
                error_type = "not_found" if any(kw in error_msg for kw in ["찾을 수 없", "not found", "404", "삭제"]) else None
                error_result = {
                    "platform": "xiaohongshu",
                    "url": url,
                    "error": error_msg,
                    "crawled_at": datetime.now().isoformat(),
                }
                if error_type:
                    error_result["error_type"] = error_type
                results.append(error_result)

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
