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


def decode_unicode_escapes(text: str) -> str:
    """유니코드 이스케이프 시퀀스를 디코딩 (\\uXXXX -> 실제 문자)

    이모지 등 surrogate pair도 올바르게 처리합니다.
    UTF-8이 Latin-1로 잘못 해석된 경우도 수정합니다.
    """
    if not text:
        return text
    try:
        # 1단계: UTF-8이 Latin-1로 잘못 해석된 경우 수정 시도
        # 예: "ç¬¬ä¸æ¬¡è¦ç¶²å" -> "第一次覺網友" (중국어/대만어)
        try:
            fixed = text.encode('latin-1').decode('utf-8')
            if fixed != text:
                # Latin-1 수정 성공 - 기본 이스케이프만 처리하고 반환
                fixed = fixed.replace('\\n', '\n').replace('\\r', '\r')
                fixed = fixed.replace('\\t', '\t').replace('\\"', '"')
                fixed = fixed.replace('\\/', '/')
                return fixed
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass  # Latin-1 수정 불가, 다음 단계로

        # 2단계: \uXXXX 패턴이 있는 경우 re.sub으로 안전하게 변환
        # (unicode_escape 코덱 대신 re.sub 사용 - 중국어+이스케이프 혼합 텍스트에서도 안전)
        if '\\u' in text:
            def replace_unicode(match):
                return chr(int(match.group(1), 16))
            decoded = re.sub(r'\\u([0-9a-fA-F]{4})', replace_unicode, text)
            try:
                decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
            except Exception:
                pass
        else:
            decoded = text

        # 기타 이스케이프 시퀀스 처리
        decoded = decoded.replace('\\n', '\n').replace('\\r', '\r')
        decoded = decoded.replace('\\t', '\t').replace('\\"', '"')
        decoded = decoded.replace('\\/', '/')
        return decoded
    except Exception:
        return text

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

# nodriver for Cloudflare bypass (2025 recommended)
try:
    import nodriver
    import asyncio
    HAS_NODRIVER = True
except ImportError:
    HAS_NODRIVER = False

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
        collect_comments: bool = True,  # 댓글 내용 수집 여부
        max_comments: int = 10,  # 수집할 최대 댓글 수
    ):
        """
        크롤러 초기화

        Args:
            headless: 헤드리스 모드 (Selenium 사용 시)
            timeout: 기본 타임아웃 (초)
            cookie_file: 쿠키 저장 파일 경로
            use_api: API 방식 우선 사용 여부
            collect_comments: 댓글 내용 수집 여부
            max_comments: 수집할 최대 댓글 수
        """
        self.headless = headless
        self.timeout = timeout
        self.cookie_file = Path(cookie_file) if cookie_file else self.COOKIE_FILE
        self.use_api = use_api
        self.collect_comments = collect_comments
        self.max_comments = max_comments

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

            # 저장된 쿠키 로드하여 cloudscraper에 적용 (cf_clearance 등)
            self._load_cookies_to_scraper()

            logger.info(f"cloudscraper 초기화 완료 (version={CLOUDSCRAPER_VERSION})")
        except Exception as e:
            logger.warning(f"cloudscraper 초기화 실패: {e}")
            self.scraper = None

    def _fetch_comments(self, post_id: str) -> List[Dict[str, Any]]:
        """
        Dcard API를 통해 댓글 수집

        Args:
            post_id: 게시물 ID

        Returns:
            댓글 리스트 (author, text, likes 포함)
        """
        if not self.scraper or not self.collect_comments:
            return []

        comments_list = []
        # 댓글 API 엔드포인트 (v2 우선, _api fallback)
        endpoints = [
            f"{self.API_URL}/{post_id}/comments?limit={self.max_comments}",
            f"{self.BASE_URL}/_api/posts/{post_id}/comments?limit={self.max_comments}",
        ]

        for ep_url in endpoints:
            try:
                logger.info(f"Dcard 댓글 API 호출: {ep_url}")
                response = self.scraper.get(ep_url, timeout=self.timeout)
                response.encoding = 'utf-8'  # UTF-8 강제

                if response.status_code == 200:
                    comments_data = response.json()
                    for comment in comments_data[:self.max_comments]:
                        raw_content = comment.get("content", "")
                        comment_item = {
                            "author": comment.get("school") or "익명",
                            "text": decode_unicode_escapes(raw_content) if raw_content else "",
                            "likes": comment.get("likeCount", 0) or 0,
                        }
                        if comment_item["text"]:  # 빈 댓글 제외
                            comments_list.append(comment_item)
                    if comments_list:
                        logger.info(f"Dcard 댓글 {len(comments_list)}개 수집됨 ({ep_url})")
                        break  # 성공하면 루프 종료
                    else:
                        logger.info(f"Dcard 댓글 API 응답 200이지만 댓글 없음 ({ep_url})")
                else:
                    logger.warning(f"Dcard 댓글 API 실패: {response.status_code} ({ep_url})")

            except Exception as e:
                logger.warning(f"Dcard 댓글 수집 실패: {e} ({ep_url})")

        return comments_list

    def _load_cookies_to_scraper(self) -> bool:
        """저장된 쿠키를 cloudscraper 세션에 로드"""
        if not self.scraper or not self.cookie_file.exists():
            return False

        try:
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)

            # 중요 쿠키들을 cloudscraper 세션에 추가
            important_cookies = ['cf_clearance', '__cf_bm', '_cfuvid', 'NID', 'dcsrd']
            loaded_count = 0

            for cookie in cookies:
                name = cookie.get('name', '')
                value = cookie.get('value', '')
                domain = cookie.get('domain', '')

                # 중요 쿠키만 로드 (Cloudflare 관련)
                if name in important_cookies or name.startswith('cf_') or name.startswith('__cf'):
                    # requests 쿠키 형식으로 변환
                    self.scraper.cookies.set(
                        name=name,
                        value=value,
                        domain=domain.lstrip('.') if domain.startswith('.') else domain,
                        path=cookie.get('path', '/')
                    )
                    loaded_count += 1

            if loaded_count > 0:
                logger.info(f"cloudscraper에 {loaded_count}개 쿠키 로드 완료")
                return True
            return False

        except Exception as e:
            logger.debug(f"cloudscraper 쿠키 로드 실패: {e}")
            return False

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
                response.encoding = 'utf-8'  # UTF-8 강제 (Latin-1 기본값 방지)

                if response.status_code == 200:
                    data = response.json()
                    # content 우선, 없으면 excerpt 사용
                    raw_content = data.get("content", "") or data.get("excerpt", "")
                    raw_title = data.get("title", "")

                    # 썸네일 추출 (media 배열에서 첫 번째 이미지)
                    thumbnail = None
                    media_list = data.get("media", []) or data.get("mediaMeta", [])
                    if media_list and len(media_list) > 0:
                        first_media = media_list[0]
                        thumbnail = first_media.get("url") or first_media.get("thumbnail")

                    # 댓글 내용 수집
                    comments_list = self._fetch_comments(post_id)

                    result = {
                        "platform": "dcard",
                        "url": f"{self.BASE_URL}/f/{data.get('forumAlias', 'all')}/p/{post_id}",
                        "post_id": str(post_id),
                        "author": data.get("school") or "Anonymous",
                        "title": decode_unicode_escapes(raw_title),
                        "content": decode_unicode_escapes(raw_content),  # 게시물 본문 (유니코드 디코딩)
                        "likes": data.get("likeCount", 0) or 0,
                        "comments": data.get("commentCount", 0) or 0,
                        "shares": data.get("shareCount"),
                        "views": data.get("viewCount"),
                        "forum": data.get("forumAlias", ""),
                        "created_at": data.get("createdAt", ""),
                        "thumbnail": thumbnail,
                        "comments_list": comments_list,  # 댓글 내용 리스트
                        "crawled_at": datetime.now().isoformat(),
                    }
                    logger.info(f"API 크롤링 성공: likes={result['likes']}, comments={result['comments']}, 댓글내용={len(comments_list)}개")
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

    def _crawl_via_nodriver(self, url: str, max_retries: int = 2) -> Optional[Dict[str, Any]]:
        """
        nodriver를 사용한 Cloudflare 우회 크롤링 (2025 권장 방식)

        Args:
            url: 게시물 URL
            max_retries: 최대 재시도 횟수

        Returns:
            게시물 데이터 또는 None (실패 시)
        """
        if not HAS_NODRIVER:
            logger.warning("nodriver가 설치되지 않음")
            return None

        async def _async_crawl():
            browser = None
            try:
                print("[Dcard] nodriver로 크롤링 시도 중...")
                print("[Dcard] 브라우저 창에서 Cloudflare 인증을 완료해주세요...")
                logger.info("nodriver 브라우저 시작...")

                browser = await nodriver.start(headless=self.headless)

                # 메인 페이지 먼저 방문 (Cloudflare 통과)
                page = await browser.get('https://www.dcard.tw/')

                # === Cloudflare 수동 인증 대기 (최대 60초) ===
                print("")
                print("=" * 60)
                print("[Dcard] Cloudflare 보안 인증 대기 중...")
                print("")
                print("  브라우저 창에서 '사람입니다' 체크박스를 클릭하거나")
                print("  보안 인증을 완료해주세요.")
                print("")
                print("  대기 시간: 60초")
                print("=" * 60)
                print("")

                cf_wait_start = time.time()
                cf_max_wait = 60  # 60초 대기
                cf_passed = False

                while time.time() - cf_wait_start < cf_max_wait:
                    await asyncio.sleep(2)

                    try:
                        content = await page.get_content()
                        page_title = ""
                        try:
                            title_elem = await page.query_selector('title')
                            if title_elem:
                                page_title = await page.evaluate('document.title')
                        except Exception:
                            pass

                        # Cloudflare 통과 확인
                        if "just a moment" not in content.lower() and "cloudflare" not in page_title.lower():
                            if "dcard" in content.lower() or "__NEXT_DATA__" in content:
                                print("[Dcard] Cloudflare 인증 성공!")
                                cf_passed = True
                                break

                        # 진행 상황 출력 (10초마다)
                        elapsed = int(time.time() - cf_wait_start)
                        if elapsed % 10 == 0 and elapsed > 0:
                            remaining = cf_max_wait - elapsed
                            print(f"[Dcard] Cloudflare 인증 대기 중... (남은 시간: {remaining}초)")

                    except Exception as e:
                        logger.debug(f"인증 확인 중 오류 (무시): {e}")

                if not cf_passed:
                    print("[Dcard] Cloudflare 인증 시간 초과")
                    logger.warning("nodriver: Cloudflare 인증 시간 초과")
                    return None

                # === Cloudflare 인증 후 쿠키 저장 ===
                print("[Dcard] Cloudflare 인증 완료, 쿠키 저장 중...")
                try:
                    # 브라우저에서 쿠키 추출
                    cookies_js = """
                    (() => {
                        return document.cookie;
                    })()
                    """
                    doc_cookies = await page.evaluate(cookies_js)

                    # 모든 쿠키 가져오기 (CDP 사용)
                    all_cookies = await browser.cookies.get_all()
                    if all_cookies:
                        # 쿠키를 JSON 형식으로 변환하여 저장
                        cookies_to_save = []
                        for cookie in all_cookies:
                            cookie_dict = {
                                "name": cookie.name,
                                "value": cookie.value,
                                "domain": cookie.domain,
                                "path": cookie.path,
                                "secure": cookie.secure,
                                "httpOnly": cookie.http_only if hasattr(cookie, 'http_only') else False,
                            }
                            if hasattr(cookie, 'expires') and cookie.expires:
                                cookie_dict["expiry"] = int(cookie.expires)
                            if hasattr(cookie, 'same_site') and cookie.same_site:
                                # CookieSameSite enum을 문자열로 변환
                                same_site_val = cookie.same_site
                                if hasattr(same_site_val, 'value'):
                                    cookie_dict["sameSite"] = same_site_val.value
                                elif hasattr(same_site_val, 'name'):
                                    cookie_dict["sameSite"] = same_site_val.name
                                else:
                                    cookie_dict["sameSite"] = str(same_site_val)
                            cookies_to_save.append(cookie_dict)

                        # 파일에 저장
                        with open(self.cookie_file, "w", encoding="utf-8") as f:
                            json.dump(cookies_to_save, f, ensure_ascii=False, indent=2)

                        logger.info(f"nodriver: {len(cookies_to_save)}개 쿠키 저장 완료")
                        print(f"[Dcard] {len(cookies_to_save)}개 쿠키 저장 완료")

                        # cloudscraper에 쿠키 적용 (댓글 API 호출용)
                        if self.scraper:
                            self._load_cookies_to_scraper()
                except Exception as ce:
                    logger.warning(f"nodriver 쿠키 저장 실패: {ce}")
                    print(f"[Dcard] 쿠키 저장 실패: {ce}")

                # 인증 후 추가 대기
                await asyncio.sleep(3)

                # 게시글 페이지로 이동
                page = await browser.get(url)
                logger.info(f"페이지 로드 중: {url}")

                # 렌더링 대기 (더 오래)
                await asyncio.sleep(8)

                # === 댓글 로딩을 위한 스크롤 및 버튼 클릭 (강화) ===
                # 1. 먼저 더 많은 스크롤로 댓글 섹션 로드
                for i in range(10):  # 10번 스크롤 (기존 5번)
                    await page.scroll_down(300)
                    await asyncio.sleep(0.8)  # 대기 시간 증가

                # 2. "더 보기" 버튼 클릭 시도 (댓글 펼치기)
                try:
                    js_click_more = """
                    (() => {
                        // 댓글 더 보기 버튼들
                        const selectors = [
                            'button[class*="more"]',
                            '[class*="LoadMore"]',
                            '[class*="load-more"]',
                            'button:contains("更多")',
                            'button:contains("展開")',
                            '[data-testid*="comment"]',
                            'a[class*="comment"]'
                        ];
                        let clicked = 0;
                        for (const sel of selectors) {
                            try {
                                const btns = document.querySelectorAll(sel);
                                btns.forEach(btn => {
                                    if (btn.innerText && (btn.innerText.includes('更多') || btn.innerText.includes('留言') || btn.innerText.includes('展開'))) {
                                        btn.click();
                                        clicked++;
                                    }
                                });
                            } catch(e) {}
                        }
                        return clicked;
                    })()
                    """
                    await page.evaluate(js_click_more)
                    await asyncio.sleep(2)
                except Exception:
                    pass

                # 3. 댓글 영역으로 스크롤
                try:
                    js_scroll_comments = """
                    (() => {
                        const commentSection = document.querySelector('[class*="Comment"], [class*="comment"], [id*="comment"]');
                        if (commentSection) {
                            commentSection.scrollIntoView({behavior: 'smooth'});
                            return true;
                        }
                        return false;
                    })()
                    """
                    await page.evaluate(js_scroll_comments)
                    await asyncio.sleep(2)
                except Exception:
                    pass

                # 4. 추가 스크롤로 더 많은 댓글 로드
                for i in range(5):
                    await page.scroll_down(200)
                    await asyncio.sleep(0.5)

                # 추가 대기 후 컨텐츠 로드
                await asyncio.sleep(3)

                # 페이지 소스 가져오기
                content = await page.get_content()

                # 데이터 추출
                import re
                like_match = re.search(r'"likeCount"\s*:\s*(\d+)', content)
                comment_match = re.search(r'"commentCount"\s*:\s*(\d+)', content)
                title_match = re.search(r'"title"\s*:\s*"([^"]+)"', content)
                # content 필드 우선 검색 (더 긴 내용), 없으면 excerpt
                body_match = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)(?=",)', content)
                if not body_match:
                    body_match = re.search(r'"excerpt"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
                nickname_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', content)
                author_match = re.search(r'"school"\s*:\s*"([^"]+)"', content)
                forum_match = re.search(r'"forumAlias"\s*:\s*"([^"]*)"', content)
                created_match = re.search(r'"createdAt"\s*:\s*"([^"]+)"', content)

                likes = int(like_match.group(1)) if like_match else 0
                comments = int(comment_match.group(1)) if comment_match else 0

                if likes > 0 or comments > 0:
                    # 유니코드 이스케이프 디코딩 적용
                    raw_title = title_match.group(1) if title_match else None
                    raw_body = body_match.group(1) if body_match else None

                    # 썸네일 추출 시도 (og:image 또는 Dcard CDN 이미지)
                    thumbnail = None
                    thumb_patterns = [
                        r'<meta\s+property="og:image"\s+content="([^"]+)"',
                        r'<meta\s+content="([^"]+)"\s+property="og:image"',
                        r'"url"\s*:\s*"(https://[^"]*dcard[^"]*\.jpg)"',
                        r'"url"\s*:\s*"(https://[^"]*dcard[^"]*\.png)"',
                    ]
                    for pattern in thumb_patterns:
                        thumb_match = re.search(pattern, content)
                        if thumb_match:
                            thumbnail = thumb_match.group(1)
                            break

                    # 댓글 내용 추출 시도 (nodriver) - 다중 방법 시도
                    comments_list = []
                    post_id = self._extract_post_id(url)

                    # 방법 0: __NEXT_DATA__에서 초기 댓글 추출 (가장 빠름)
                    try:
                        print("[Dcard] __NEXT_DATA__에서 댓글 검색 중...")
                        next_data_match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>([^<]+)</script>', content)
                        if next_data_match:
                            import json as json_module
                            next_data = json_module.loads(next_data_match.group(1))

                            # pageProps에서 comments 찾기
                            def extract_comments_from_data(obj, found_comments, depth=0):
                                if depth > 15 or len(found_comments) >= 10:
                                    return
                                if isinstance(obj, dict):
                                    # comments 키가 있으면 추출
                                    if 'comments' in obj and isinstance(obj['comments'], list):
                                        for c in obj['comments'][:10]:
                                            if isinstance(c, dict) and c.get('content'):
                                                found_comments.append({
                                                    'author': c.get('school') or 'Anonymous',
                                                    'text': decode_unicode_escapes(c['content'])[:200],
                                                    'likes': c.get('likeCount', 0) or 0,
                                                })
                                    for v in obj.values():
                                        extract_comments_from_data(v, found_comments, depth + 1)
                                elif isinstance(obj, list):
                                    for item in obj[:30]:
                                        extract_comments_from_data(item, found_comments, depth + 1)

                            temp_comments = []
                            extract_comments_from_data(next_data, temp_comments)
                            if temp_comments:
                                comments_list = temp_comments[:10]
                                print(f"[Dcard] __NEXT_DATA__에서 댓글 {len(comments_list)}개 발견!")
                                logger.info(f"__NEXT_DATA__에서 댓글 {len(comments_list)}개 수집")
                    except Exception as nd_err:
                        logger.debug(f"__NEXT_DATA__ 댓글 추출 실패: {nd_err}")

                    # 방법 1: 브라우저 내 fetch로 댓글 API 호출 (인증된 세션 사용)
                    if not comments_list and post_id:
                        try:
                            print("[Dcard] 브라우저 fetch로 댓글 API 호출 시도...")
                            # 구버전 _api와 신버전 service/api/v2 둘 다 시도
                            js_fetch_comments = f"""
                            (async () => {{
                                const endpoints = [
                                    'https://www.dcard.tw/service/api/v2/posts/{post_id}/comments?limit=30',
                                    'https://www.dcard.tw/_api/posts/{post_id}/comments?limit=30'
                                ];

                                for (const url of endpoints) {{
                                    try {{
                                        const response = await fetch(url, {{
                                            method: 'GET',
                                            credentials: 'include',
                                            headers: {{
                                                'Accept': 'application/json',
                                                'Referer': window.location.href
                                            }}
                                        }});

                                        if (response.ok) {{
                                            const data = await response.json();
                                            if (Array.isArray(data) && data.length > 0) {{
                                                return JSON.stringify({{success: true, data: data, endpoint: url}});
                                            }}
                                        }}
                                    }} catch (e) {{
                                        // 계속 다음 endpoint 시도
                                    }}
                                }}
                                return JSON.stringify({{success: false, error: 'All endpoints failed'}});
                            }})()
                            """

                            api_result = await page.evaluate(js_fetch_comments)
                            print(f"[Dcard] fetch 결과: {str(api_result)[:150]}...")

                            if api_result and api_result != 'null':
                                import json as json_module
                                result_data = json_module.loads(api_result)
                                if result_data.get('success') and result_data.get('data'):
                                    for comment in result_data['data'][:10]:
                                        if isinstance(comment, dict):
                                            raw_content = comment.get('content', '')
                                            if raw_content:
                                                comments_list.append({
                                                    'author': comment.get('school') or 'Anonymous',
                                                    'text': decode_unicode_escapes(raw_content)[:200],
                                                    'likes': comment.get('likeCount', 0) or 0,
                                                })
                                    if comments_list:
                                        logger.info(f"브라우저 fetch로 댓글 {len(comments_list)}개 수집 (endpoint: {result_data.get('endpoint')})")
                                        print(f"[Dcard] 브라우저 fetch로 댓글 {len(comments_list)}개 수집 성공!")
                        except Exception as fetch_err:
                            logger.debug(f"브라우저 fetch 실패: {fetch_err}")
                            print(f"[Dcard] 브라우저 fetch 실패: {fetch_err}")

                    # 방법 2: DOM에서 직접 추출 (API 실패 시)
                    if not comments_list:
                        print("[Dcard] DOM에서 댓글 추출 시도...")
                        # 댓글 영역 로드를 위해 추가 스크롤
                        for _ in range(5):
                            await page.scroll_down(400)
                            await asyncio.sleep(1)

                        # 약간의 대기 후 content 다시 가져오기
                        await asyncio.sleep(2)
                        content = await page.get_content()

                        js_get_comments = """
                        (() => {
                            const comments = [];
                            const seen = new Set();

                            // Dcard 댓글 컨테이너 - 실제 구조 기반
                            const selectors = [
                                'article[class*="Comment"] div[class*="Content"]',
                                '[class*="CommentContent"]',
                                '[class*="comment_content"]',
                                'div[class^="sc-"] > p'
                            ];

                            for (const sel of selectors) {
                                try {
                                    const elements = document.querySelectorAll(sel);
                                    elements.forEach(el => {
                                        if (comments.length >= 10) return;
                                        const text = (el.innerText || '').trim();
                                        if (text && text.length >= 10 && text.length < 500 && !seen.has(text)) {
                                            if (!text.includes('留言') && !text.includes('讚') && !text.includes('回覆')) {
                                                seen.add(text);
                                                comments.push({author: 'Anonymous', text: text.substring(0, 200)});
                                            }
                                        }
                                    });
                                } catch(e) {}
                                if (comments.length >= 10) break;
                            }
                            return JSON.stringify(comments);
                        })()
                        """
                        comments_json = await page.evaluate(js_get_comments)
                        if comments_json:
                            import json as json_module
                            parsed_comments = json_module.loads(comments_json)
                            for c in parsed_comments:
                                if c.get('text'):
                                    comments_list.append({
                                        'author': c.get('author', 'Anonymous'),
                                        'text': decode_unicode_escapes(c['text'])
                                    })
                            if comments_list:
                                logger.info(f"nodriver DOM에서 댓글 {len(comments_list)}개 수집")

                    # 방법 3: 페이지 소스 JSON에서 댓글 추출 (fallback)
                    if not comments_list:
                        # Dcard API 응답 내 댓글 패턴
                        # "comments":[{"id":...,"content":"댓글내용",...}]
                        import json as json_module

                        # 직접 패턴 매칭 (fallback)
                        comments_pattern = r'"comments"\s*:\s*\[(.*?)\]'
                        comments_match = re.search(comments_pattern, content, re.DOTALL)
                        if comments_match:
                            comment_contents = re.findall(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', comments_match.group(1))
                            for i, c_text in enumerate(comment_contents[:10]):
                                if c_text and len(c_text) > 5:
                                    comments_list.append({
                                        'author': 'Anonymous',
                                        'text': decode_unicode_escapes(c_text)[:200]
                                    })
                        if comments_list:
                            logger.info(f"JSON 패턴에서 댓글 {len(comments_list)}개 수집")

                    if comments_list:
                        logger.info(f"nodriver 댓글 {len(comments_list)}개 수집됨")
                        print(f"[Dcard] 총 {len(comments_list)}개 댓글 수집 완료")

                    result = {
                        "platform": "dcard",
                        "url": url,
                        "post_id": self._extract_post_id(url),
                        "author": (nickname_match.group(1) if nickname_match else None) or (author_match.group(1) if author_match else None) or "Anonymous",
                        "title": decode_unicode_escapes(raw_title) if raw_title else None,
                        "content": decode_unicode_escapes(raw_body) if raw_body else None,  # 게시물 본문 (유니코드 디코딩)
                        "likes": likes,
                        "comments": comments,
                        "shares": None,
                        "views": None,
                        "forum": forum_match.group(1) if forum_match else None,
                        "created_at": created_match.group(1) if created_match else None,
                        "thumbnail": thumbnail,
                        "comments_list": comments_list,
                        "crawled_at": datetime.now().isoformat(),
                    }
                    logger.info(f"nodriver 크롤링 성공: likes={likes}, comments={comments}")
                    print(f"[Dcard] nodriver 성공: likes={likes}, comments={comments}")
                    return result
                else:
                    logger.warning("nodriver: 데이터 추출 실패")
                    # 앱 다운로드 페이지인지 확인
                    if '下載' in content or 'download' in content.lower():
                        logger.warning("nodriver: 앱 다운로드 페이지 감지")
                    return None

            except Exception as e:
                logger.error(f"nodriver 크롤링 오류: {e}")
                return None
            finally:
                if browser:
                    try:
                        await browser.stop()
                    except Exception:
                        pass

        # 비동기 함수 실행 (재시도 포함)
        for attempt in range(max_retries):
            try:
                # 새 이벤트 루프 생성하여 실행 (연속 호출 충돌 방지)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(_async_crawl())
                    if result:
                        return result
                    elif attempt < max_retries - 1:
                        logger.info(f"nodriver 재시도 {attempt + 2}/{max_retries}...")
                        print(f"[Dcard] nodriver 재시도 {attempt + 2}/{max_retries}...")
                        time.sleep(2)
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"nodriver asyncio 오류 (시도 {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)

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

            # Chrome 브라우저 버전 자동 감지
            chrome_version = None
            try:
                import subprocess
                result = subprocess.run(
                    ['reg', 'query', r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon', '/v', 'version'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'version' in line.lower():
                            ver = line.strip().split()[-1]
                            chrome_version = int(ver.split('.')[0])
                            logger.info(f"Chrome 버전 감지: {chrome_version}")
            except Exception:
                pass

            # undetected-chromedriver 특수 옵션
            driver = uc.Chrome(
                options=options,
                use_subprocess=True,  # 서브프로세스 사용 (탐지 회피)
                version_main=chrome_version,  # 실제 Chrome 버전 매칭
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
        print("")
        print("=" * 60)
        print("[Dcard] Cloudflare 보안 인증이 필요합니다!")
        print("")
        print("  브라우저 창에서 '사람입니다' 체크박스를 클릭하거나")
        print("  보안 인증을 완료해주세요.")
        print("")
        print(f"  대기 시간: {self.CLOUDFLARE_WAIT}초")
        print("=" * 60)
        print("")

        logger.info("Cloudflare 인증 대기 시작")

        start_time = time.time()
        last_print_time = start_time

        while time.time() - start_time < self.CLOUDFLARE_WAIT:
            try:
                page_source = self.driver.page_source
                page_title = self.driver.title.lower()

                # Cloudflare 체크 완료 확인
                if "just a moment" not in page_source.lower() and "cloudflare" not in page_title:
                    # 실제 Dcard 페이지인지 확인
                    if "dcard" in page_title or "__NEXT_DATA__" in page_source or '"likeCount"' in page_source:
                        print("[Dcard] Cloudflare 인증 성공!")
                        logger.info("Cloudflare 인증 성공!")
                        self._save_cookies()
                        return True

                # 진행 상황 출력 (5초마다)
                current_time = time.time()
                if current_time - last_print_time >= 5:
                    remaining = int(self.CLOUDFLARE_WAIT - (current_time - start_time))
                    print(f"[Dcard] Cloudflare 인증 대기 중... (남은 시간: {remaining}초)")
                    last_print_time = current_time

                time.sleep(1)

            except Exception as e:
                logger.debug(f"인증 확인 중 오류 (무시): {e}")
                time.sleep(1)

        print("[Dcard] Cloudflare 인증 시간 초과 (실패)")
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

        logger.warning(f"게시물 ID를 추출할 수 없습니다: {url}")
        return None

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
            "content": None,  # 게시물 본문 내용
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

                if post and post.get("likeCount") is not None:
                    # content 우선, 없으면 excerpt (유니코드 디코딩 적용)
                    raw_title = post.get("title", "")
                    raw_content = post.get("content", "") or post.get("excerpt", "")
                    result["title"] = decode_unicode_escapes(raw_title)
                    result["content"] = decode_unicode_escapes(raw_content)
                    result["likes"] = post.get("likeCount", 0) or 0
                    result["comments"] = post.get("commentCount", 0) or 0
                    result["forum"] = post.get("forumAlias", "")
                    result["created_at"] = post.get("createdAt", "")

                    # 작성자 정보 (school/nickname이 null일 수 있으므로 or 사용)
                    if "member" in post and post["member"]:
                        result["author"] = post["member"].get("nickname") or post.get("school") or "Anonymous"
                    elif post.get("school"):
                        result["author"] = post["school"]
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
            # content 필드 우선 검색, 없으면 excerpt
            body_match = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)(?=",)', page_source)
            if not body_match:
                body_match = re.search(r'"excerpt"\s*:\s*"((?:[^"\\]|\\.)*)"', page_source)
            share_match = re.search(r'"shareCount"\s*:\s*(\d+)', page_source)
            created_match = re.search(r'"createdAt"\s*:\s*"([^"]+)"', page_source)
            # 작성자 추출 (nickname > school > Anonymous)
            nickname_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', page_source)
            school_match = re.search(r'"school"\s*:\s*"([^"]+)"', page_source)

            if like_match:
                result["likes"] = int(like_match.group(1))
            if comment_match:
                result["comments"] = int(comment_match.group(1))
            if title_match:
                result["title"] = decode_unicode_escapes(title_match.group(1))
            if body_match:
                result["content"] = decode_unicode_escapes(body_match.group(1))
            if share_match:
                result["shares"] = int(share_match.group(1))
            if created_match:
                result["created_at"] = created_match.group(1)
            # 작성자 설정
            if not result["author"]:
                if nickname_match:
                    result["author"] = nickname_match.group(1)
                elif school_match:
                    result["author"] = school_match.group(1)

            # 데이터가 추출되었으면 반환
            if result["likes"] > 0 or result["comments"] > 0:
                logger.info(f"JSON 패턴에서 데이터 추출 성공: likes={result['likes']}, comments={result['comments']}")
                # 포럼 (URL에서)
                forum_match = re.search(r'/f/([^/]+)/p/', url)
                if forum_match:
                    result["forum"] = forum_match.group(1)
                if not result["author"]:
                    result["author"] = "Anonymous"
                return result

            # === DOM에서 직접 추출 (CSR 대응) ===
            logger.info("DOM에서 직접 데이터 추출 시도 (스크롤 후)")

            # 스크롤하여 engagement 영역 로드 (더 많이 스크롤)
            self.driver.execute_script("window.scrollTo(0, 300);")
            time.sleep(2)
            self.driver.execute_script("window.scrollTo(0, 600);")
            time.sleep(2)

            # 제목 추출
            try:
                title_elem = self.driver.find_element(By.XPATH, "//h1")
                result["title"] = title_elem.text.strip()
            except NoSuchElementException:
                pass

            # 포럼 (URL에서)
            forum_match = re.search(r'/f/([^/]+)/p/', url)
            if forum_match:
                result["forum"] = forum_match.group(1)

            # 페이지 소스 다시 확인 (스크롤 후 동적 로드된 데이터)
            page_source = self.driver.page_source
            like_match = re.search(r'"likeCount"\s*:\s*(\d+)', page_source)
            comment_match = re.search(r'"commentCount"\s*:\s*(\d+)', page_source)

            if like_match:
                result["likes"] = int(like_match.group(1))
            if comment_match:
                result["comments"] = int(comment_match.group(1))

            # 데이터가 있으면 반환
            if result["likes"] > 0 or result["comments"] > 0:
                logger.info(f"스크롤 후 JSON 패턴에서 데이터 추출 성공: likes={result['likes']}, comments={result['comments']}")
                if not result["author"]:
                    result["author"] = "Anonymous"
                return result

            # JavaScript로 reaction 영역에서 숫자 추출 (다양한 방법 시도)
            engagement_data = self.driver.execute_script('''
                var results = {likes: 0, comments: 0, debug: []};

                // 방법 1: button 요소 안의 숫자 찾기 (Dcard reaction 버튼)
                var buttons = document.querySelectorAll('button');
                var likeFound = false;
                var commentFound = false;

                buttons.forEach(function(btn, idx) {
                    var text = btn.innerText || btn.textContent || '';
                    var nums = text.match(/\\d+/);
                    if (nums) {
                        var num = parseInt(nums[0]);
                        // 좋아요 버튼 (하트 아이콘 또는 첫 번째 숫자)
                        if (!likeFound && (btn.innerHTML.includes('heart') || btn.innerHTML.includes('like') || btn.innerHTML.includes('svg'))) {
                            results.likes = num;
                            likeFound = true;
                            results.debug.push('button heart: ' + num);
                        }
                        // 댓글 버튼
                        else if (!commentFound && (btn.innerHTML.includes('comment') || btn.innerHTML.includes('message') || btn.innerHTML.includes('chat'))) {
                            results.comments = num;
                            commentFound = true;
                            results.debug.push('button comment: ' + num);
                        }
                    }
                });

                // 방법 2: aria-label 속성으로 찾기
                if (results.likes === 0) {
                    var likeBtn = document.querySelector('[aria-label*="like"], [aria-label*="heart"], [aria-label*="愛心"], [aria-label*="喜歡"]');
                    if (likeBtn) {
                        var text = likeBtn.innerText || likeBtn.textContent || '';
                        var nums = text.match(/\\d+/);
                        if (nums) {
                            results.likes = parseInt(nums[0]);
                            results.debug.push('aria-label like: ' + nums[0]);
                        }
                    }
                }

                if (results.comments === 0) {
                    var commentBtn = document.querySelector('[aria-label*="comment"], [aria-label*="留言"], [aria-label*="回應"]');
                    if (commentBtn) {
                        var text = commentBtn.innerText || commentBtn.textContent || '';
                        var nums = text.match(/\\d+/);
                        if (nums) {
                            results.comments = parseInt(nums[0]);
                            results.debug.push('aria-label comment: ' + nums[0]);
                        }
                    }
                }

                // 방법 3: SVG 근처의 숫자 (더 넓은 범위 검색)
                if (results.likes === 0) {
                    var svgs = document.querySelectorAll('svg');
                    var foundNumbers = [];

                    svgs.forEach(function(svg, idx) {
                        // SVG의 부모 요소들에서 숫자 찾기
                        var parent = svg.parentElement;
                        for (var i = 0; i < 5 && parent; i++) {
                            var text = parent.innerText || '';
                            var nums = text.match(/^\\s*(\\d+)\\s*$/);
                            if (nums) {
                                foundNumbers.push(parseInt(nums[1]));
                            }
                            parent = parent.parentElement;
                        }

                        // 형제 요소에서도 찾기
                        var sibling = svg.nextElementSibling;
                        if (sibling) {
                            var text = sibling.innerText || sibling.textContent || '';
                            var nums = text.match(/\\d+/);
                            if (nums) {
                                foundNumbers.push(parseInt(nums[0]));
                            }
                        }
                    });

                    if (foundNumbers.length >= 2) {
                        results.likes = foundNumbers[0];
                        results.comments = foundNumbers[1];
                        results.debug.push('svg numbers: ' + foundNumbers.join(', '));
                    } else if (foundNumbers.length === 1) {
                        results.likes = foundNumbers[0];
                        results.debug.push('svg single: ' + foundNumbers[0]);
                    }
                }

                // 방법 4: 특정 클래스 패턴으로 찾기
                if (results.likes === 0) {
                    // Dcard의 reaction 영역 클래스 패턴
                    var reactionContainers = document.querySelectorAll('[class*="reaction"], [class*="Reaction"], [class*="engagement"], [class*="Engagement"], [class*="action"], [class*="Action"]');
                    reactionContainers.forEach(function(container) {
                        var text = container.innerText || '';
                        var nums = text.match(/\\d+/g);
                        if (nums && nums.length >= 1) {
                            if (results.likes === 0) results.likes = parseInt(nums[0]);
                            if (nums.length >= 2 && results.comments === 0) results.comments = parseInt(nums[1]);
                            results.debug.push('class pattern: ' + nums.join(', '));
                        }
                    });
                }

                // 방법 5: article 하단의 숫자들 (마지막 시도)
                if (results.likes === 0) {
                    var article = document.querySelector('article');
                    if (article) {
                        var allSpans = article.querySelectorAll('span');
                        var nums = [];
                        allSpans.forEach(function(span) {
                            var text = span.innerText || '';
                            if (/^\\d+$/.test(text.trim())) {
                                nums.push(parseInt(text.trim()));
                            }
                        });
                        if (nums.length >= 2) {
                            // 일반적으로 좋아요가 더 크고, 댓글이 더 작음
                            nums.sort(function(a, b) { return b - a; });
                            results.likes = nums[0];
                            results.comments = nums[1];
                            results.debug.push('article spans: ' + nums.join(', '));
                        }
                    }
                }

                return results;
            ''')

            if engagement_data:
                # 디버그 정보 로깅
                if engagement_data.get("debug"):
                    logger.info(f"JS 추출 디버그: {engagement_data.get('debug')}")

                if engagement_data.get("likes", 0) > 0:
                    result["likes"] = engagement_data["likes"]
                if engagement_data.get("comments", 0) > 0:
                    result["comments"] = engagement_data["comments"]

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
        # URL 유효성 검사 - ValueError 대신 에러 dict 반환 (verify-bot 프로토콜)
        if not url:
            logger.error("URL이 비어있습니다")
            return {
                "platform": "dcard",
                "url": "",
                "post_id": None,
                "author": None,
                "title": None,
                "content": None,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "URL이 비어있습니다.",
                "error_type": "validation_error"
            }

        # 게시물 ID 추출
        post_id = None
        if url.isdigit():
            post_id = url
            url = f"{self.BASE_URL}/f/all/p/{url}"
        elif "dcard.tw" not in url:
            logger.error(f"유효하지 않은 Dcard URL: {url}")
            return {
                "platform": "dcard",
                "url": url,
                "post_id": None,
                "author": None,
                "title": None,
                "content": None,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": None,
                "crawled_at": datetime.now().isoformat(),
                "error": "유효하지 않은 Dcard URL입니다. dcard.tw 도메인이 포함된 URL을 입력해주세요.",
                "error_type": "validation_error"
            }
        else:
            post_id = self._extract_post_id(url)
            # post_id 추출 실패 시 에러 반환
            if not post_id:
                return {
                    "platform": "dcard",
                    "url": url,
                    "post_id": None,
                    "author": None,
                    "title": None,
                    "content": None,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "views": None,
                    "crawled_at": datetime.now().isoformat(),
                    "error": "URL에서 게시물 ID를 추출할 수 없습니다. 올바른 Dcard 게시물 URL인지 확인해주세요.",
                    "error_type": "validation_error"
                }

        # 1. API 방식 우선 시도 (cloudscraper25 v2/v3 지원)
        if self.use_api and self.scraper and post_id:
            print(f"[Dcard] API로 크롤링 시도 중... (cloudscraper {CLOUDSCRAPER_VERSION})")
            logger.info(f"API 방식으로 크롤링 시도 (version={CLOUDSCRAPER_VERSION})...")
            result = self._crawl_via_api(post_id, max_retries=3)
            if result:
                return result
            print("[Dcard] API 방식 실패, nodriver로 전환 중...")
            logger.info("API 방식 실패, nodriver fallback 시도...")

        # 2. nodriver 방식 시도 (Cloudflare 자동 우회, 2025 권장)
        if HAS_NODRIVER and not IS_CLOUD:
            result = self._crawl_via_nodriver(url)
            if result:
                return result
            print("[Dcard] nodriver 실패, Selenium으로 전환 중...")
            logger.info("nodriver 실패, Selenium fallback...")

        # 3. Cloud 환경에서는 API만 지원 (Selenium은 Cloudflare 우회 불가)
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

        # 4. Selenium fallback (로컬 환경)
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
                    print("[Dcard] [경고] Cloudflare 보안에 의해 차단되었습니다.")
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

            # 작성자 안전장치: None이면 "Anonymous"로 보정
            if not result.get("author"):
                result["author"] = "Anonymous"

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
                "author": "Anonymous",
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
        delay: float = 8.0,  # Cloudflare 차단 방지를 위해 8초로 증가
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

            except DcardPostNotFoundError as e:
                logger.warning(f"게시물 없음 ({url}): {e}")
                if continue_on_error:
                    results.append({
                        "platform": "dcard",
                        "url": url,
                        "error": "게시물이 삭제되었거나 접근할 수 없습니다",
                        "error_type": "not_found",
                        "crawled_at": datetime.now().isoformat(),
                    })
                else:
                    raise

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

    def cleanup(self) -> None:
        """리소스 정리 (close의 별칭)"""
        self.close()

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
