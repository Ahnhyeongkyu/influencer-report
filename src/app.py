"""
인플루언서 캠페인 성과 리포트 - Streamlit 웹 앱

멀티 플랫폼 크롤링 및 결과 리포트 생성
v2.0 - 플랫폼 쿠키 인증 지원
"""

import sys
import os
import logging
import platform as sys_platform
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import time

import streamlit as st
import pandas as pd

# 환경 감지 (Cloud vs Local)
IS_CLOUD = sys_platform.system() == "Linux" and os.path.exists("/etc/debian_version")
IS_LOCAL = not IS_CLOUD

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 프로젝트 모듈 임포트
from src.auth import is_authenticated, show_login_form, show_user_info, init_session_state
from src.platform_auth import (
    init_platform_auth_state,
    render_platform_auth_section,
    get_platform_cookies,
    is_platform_authenticated,
)
from src.utils.url_parser import (
    parse_urls,
    parse_csv_urls,
    count_by_platform,
    get_platform_display_name,
    PLATFORM_DISPLAY_NAMES,
)
from src.utils.data_processor import (
    aggregate_results,
    group_by_platform,
    calculate_campaign_metrics,
    export_to_dataframe,
    generate_summary_table,
    format_number,
)

# 크롤러 임포트
from src.crawlers import (
    crawl_xhs_post,
    crawl_xhs_posts,
    crawl_youtube_video,
    crawl_youtube_videos,
    crawl_instagram_post,
    crawl_instagram_posts,
    crawl_facebook_post,
    crawl_facebook_posts,
    crawl_dcard_post,
    crawl_dcard_posts,
)

# 페이지 설정
st.set_page_config(
    page_title="인플루언서 캠페인 성과 리포트",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 커스텀 CSS
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 0.5rem;
        text-align: center;
    }
    .metric-value {
        font-size: 1.5rem;
        font-weight: bold;
        color: #1f77b4;
    }
    .metric-label {
        font-size: 0.9rem;
        color: #666;
    }
    .platform-badge {
        display: inline-block;
        padding: 0.25rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.8rem;
        margin-right: 0.5rem;
    }
    .status-success {
        color: #28a745;
    }
    .status-error {
        color: #dc3545;
    }
    .status-pending {
        color: #ffc107;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def init_app_state():
    """앱 상태 초기화"""
    init_session_state()
    init_platform_auth_state()

    if "campaign_info" not in st.session_state:
        st.session_state.campaign_info = {
            "name": "",
            "advertiser": "",
            "start_date": datetime.now().date(),
            "end_date": (datetime.now() + timedelta(days=30)).date(),
        }

    if "urls" not in st.session_state:
        st.session_state.urls = []

    if "crawl_results" not in st.session_state:
        st.session_state.crawl_results = []

    if "crawling_status" not in st.session_state:
        st.session_state.crawling_status = "idle"  # idle, running, completed, error


def render_sidebar():
    """사이드바 렌더링 - 캠페인 정보 입력"""
    with st.sidebar:
        st.markdown("### 캠페인 정보")
        st.markdown("---")

        # 캠페인명
        st.session_state.campaign_info["name"] = st.text_input(
            "캠페인명",
            value=st.session_state.campaign_info.get("name", ""),
            placeholder="예: 2024 신제품 런칭 캠페인",
        )

        # 광고주명
        st.session_state.campaign_info["advertiser"] = st.text_input(
            "광고주명",
            value=st.session_state.campaign_info.get("advertiser", ""),
            placeholder="예: ABC 브랜드",
        )

        # 캠페인 기간
        st.markdown("**캠페인 기간**")
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.campaign_info["start_date"] = st.date_input(
                "시작일",
                value=st.session_state.campaign_info.get("start_date", datetime.now().date()),
            )
        with col2:
            st.session_state.campaign_info["end_date"] = st.date_input(
                "종료일",
                value=st.session_state.campaign_info.get("end_date", (datetime.now() + timedelta(days=30)).date()),
            )

        # 지원 플랫폼 안내
        st.markdown("---")
        st.markdown("### 지원 플랫폼")
        for platform, display_name in PLATFORM_DISPLAY_NAMES.items():
            # 인증 상태 표시
            if platform == "youtube":
                status = "✅"  # YouTube는 항상 OK
            elif is_platform_authenticated(platform):
                status = "✅"
            else:
                status = "⚠️"
            st.markdown(f"- {status} {display_name}")

        # 플랫폼 인증 설정
        st.markdown("---")
        render_platform_auth_section()

        # 인증 모드 설정 (로컬 환경만)
        if IS_LOCAL:
            st.markdown("---")
            st.markdown("### 크롤링 설정")
            st.session_state.auth_mode = st.checkbox(
                "인증 모드",
                value=st.session_state.get("auth_mode", False),
                help="활성화하면 브라우저 창이 열려 QR 코드 스캔이나 Cloudflare 인증을 직접 할 수 있습니다. (샤오홍슈, Dcard 등)"
            )
            if st.session_state.auth_mode:
                st.info(
                    "인증 모드가 활성화되었습니다.\n\n"
                    "크롤링 시작 시 브라우저 창이 열립니다.\n"
                    "- 샤오홍슈: QR 코드 스캔\n"
                    "- Dcard: Cloudflare 인증 완료\n\n"
                    "인증 후 자동으로 진행됩니다."
                )
        else:
            # Cloud 환경에서는 auth_mode 비활성화
            st.session_state.auth_mode = False

        # 로그아웃 버튼
        show_user_info()


def render_url_input():
    """URL 입력 섹션 렌더링"""
    st.markdown("### URL 입력")

    # 탭으로 입력 방식 선택
    tab1, tab2 = st.tabs(["직접 입력", "CSV 업로드"])

    with tab1:
        st.markdown("게시물 URL을 한 줄에 하나씩 입력하세요.")
        url_text = st.text_area(
            "URL 목록",
            height=200,
            placeholder="""https://www.youtube.com/watch?v=xxxxx
https://www.instagram.com/p/xxxxx
https://www.xiaohongshu.com/explore/xxxxx
https://www.facebook.com/xxxxx/posts/xxxxx
https://www.dcard.tw/f/xxx/p/xxxxx""",
            key="url_text_input",
        )

        if st.button("URL 분석", key="analyze_urls"):
            if url_text.strip():
                parsed = parse_urls(url_text)
                st.session_state.urls = parsed
                st.success(f"{len(parsed)}개 URL이 분석되었습니다.")
                st.rerun()
            else:
                st.warning("URL을 입력하세요.")

    with tab2:
        st.markdown("URL이 포함된 CSV 파일을 업로드하세요.")
        uploaded_file = st.file_uploader(
            "CSV 파일 선택",
            type=["csv"],
            key="csv_upload",
        )

        if uploaded_file is not None:
            try:
                content = uploaded_file.read().decode("utf-8")
                parsed = parse_csv_urls(content)
                st.session_state.urls = parsed
                st.success(f"{len(parsed)}개 URL이 CSV에서 추출되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"CSV 파싱 오류: {e}")


def render_url_preview():
    """URL 분석 결과 미리보기"""
    urls = st.session_state.get("urls", [])

    if not urls:
        return

    st.markdown("### URL 분석 결과")

    # 플랫폼별 카운트
    platform_counts = count_by_platform(urls)
    valid_count = sum(1 for u in urls if u.get("valid"))
    invalid_count = len(urls) - valid_count

    # 요약 표시
    cols = st.columns([1, 1, 1, 2])
    with cols[0]:
        st.metric("전체 URL", len(urls))
    with cols[1]:
        st.metric("유효", valid_count)
    with cols[2]:
        st.metric("오류", invalid_count)
    with cols[3]:
        platform_text = " / ".join([
            f"{get_platform_display_name(p)}: {c}"
            for p, c in platform_counts.items()
        ])
        st.markdown(f"**플랫폼별:** {platform_text}")

    # URL 상세 목록
    with st.expander("URL 상세 목록", expanded=True):
        for i, url_info in enumerate(urls):
            col1, col2, col3 = st.columns([4, 1, 1])
            with col1:
                st.text(url_info.get("url", ""))
            with col2:
                if url_info.get("valid"):
                    st.markdown(f'<span class="status-success">{get_platform_display_name(url_info.get("platform", ""))}</span>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<span class="status-error">오류</span>', unsafe_allow_html=True)
            with col3:
                if url_info.get("error"):
                    st.markdown(f'<span class="status-error">{url_info.get("error")}</span>', unsafe_allow_html=True)

    # 크롤링 시작 버튼
    st.markdown("---")
    if valid_count > 0:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button(
                f"크롤링 시작 ({valid_count}개 URL)",
                key="start_crawling",
                use_container_width=True,
                type="primary",
            ):
                st.session_state.crawling_status = "running"
                st.rerun()


def get_crawler_for_platform(platform: str):
    """플랫폼에 맞는 크롤러 함수 반환"""
    crawlers = {
        "xiaohongshu": crawl_xhs_post,
        "youtube": crawl_youtube_video,
        "instagram": crawl_instagram_post,
        "facebook": crawl_facebook_post,
        "dcard": crawl_dcard_post,
    }
    return crawlers.get(platform)


def apply_cookies_to_session(session, cookies: dict, domain: str) -> None:
    """
    requests 세션에 쿠키를 도메인과 함께 적용

    단순 dict.update()는 도메인 정보가 없어서 서버가 인증을 거부할 수 있음.
    명시적으로 도메인을 설정해야 Instagram 등 플랫폼에서 인증이 동작함.

    Args:
        session: requests.Session 객체
        cookies: 쿠키 딕셔너리 {"name": "value"}
        domain: 쿠키 도메인 (예: ".instagram.com")
    """
    if not session or not cookies:
        return

    for name, value in cookies.items():
        if value:  # 값이 있는 쿠키만 설정
            session.cookies.set(
                name=name,
                value=value,
                domain=domain,
                path="/"
            )
    logger.info(f"쿠키 적용 완료: {domain} - {list(cookies.keys())}")


# 플랫폼별 도메인 매핑
PLATFORM_DOMAINS = {
    "instagram": ".instagram.com",
    "facebook": ".facebook.com",
    "xiaohongshu": ".xiaohongshu.com",
    "dcard": ".dcard.tw",
}


def crawl_with_cookies(platform: str, url: str, auth_mode: bool = False) -> dict:
    """
    쿠키를 포함하여 크롤링 수행

    Args:
        platform: 플랫폼 이름
        url: 크롤링할 URL
        auth_mode: 인증 모드 (True면 브라우저 창 표시하여 QR/Cloudflare 인증 가능)

    Returns:
        크롤링 결과
    """
    cookies = get_platform_cookies(platform)
    crawler = get_crawler_for_platform(platform)
    domain = PLATFORM_DOMAINS.get(platform, "")

    if not crawler:
        return {
            "platform": platform,
            "url": url,
            "error": f"지원하지 않는 플랫폼: {platform}",
            "crawled_at": datetime.now().isoformat(),
        }

    # 헤드리스 모드 결정:
    # - Cloud 환경: 항상 headless (브라우저 창 표시 불가)
    # - Local + 쿠키 있음: headless (인증 불필요)
    # - Local + 쿠키 없음 + auth_mode: 비headless (인증 필요)
    # - Local + 쿠키 없음 + not auth_mode: headless (API 시도)
    has_cookies = bool(cookies)

    if IS_CLOUD:
        use_headless = True  # Cloud는 항상 headless
    elif auth_mode:
        use_headless = False  # 인증 모드면 브라우저 표시 (쿠키 유무 무관)
        logger.info(f"인증 모드 활성화 - 브라우저 창이 열립니다 ({platform})")
    elif has_cookies:
        use_headless = True  # 쿠키 있으면 headless로 충분
    else:
        use_headless = True  # 기본은 headless

    try:
        # YouTube는 쿠키 불필요
        if platform == "youtube":
            return crawler(url)

        # 다른 플랫폼은 쿠키와 함께 크롤링
        if platform == "instagram":
            from src.crawlers.instagram_crawler import InstagramCrawler
            with InstagramCrawler(headless=use_headless, use_api=True) as crawler_instance:
                if cookies and crawler_instance.session:
                    apply_cookies_to_session(crawler_instance.session, cookies, domain)
                return crawler_instance.crawl_post(url)

        elif platform == "facebook":
            from src.crawlers.facebook_crawler import FacebookCrawler
            with FacebookCrawler(headless=use_headless, use_api=True) as crawler_instance:
                if cookies and crawler_instance.session:
                    apply_cookies_to_session(crawler_instance.session, cookies, domain)
                return crawler_instance.crawl_post(url)

        elif platform == "xiaohongshu":
            from src.crawlers.xhs_crawler import XHSCrawler
            with XHSCrawler(headless=use_headless, use_api=True) as crawler_instance:
                if cookies and crawler_instance.session:
                    apply_cookies_to_session(crawler_instance.session, cookies, domain)
                return crawler_instance.crawl_post(url)

        elif platform == "dcard":
            from src.crawlers.dcard_crawler import DcardCrawler
            with DcardCrawler(headless=use_headless, use_api=True) as crawler_instance:
                if cookies and hasattr(crawler_instance, 'scraper'):
                    apply_cookies_to_session(crawler_instance.scraper, cookies, domain)
                return crawler_instance.crawl_post(url)

        # 기본 동작
        return crawler(url)

    except Exception as e:
        logger.error(f"크롤링 오류 ({platform}, {url}): {e}")
        return {
            "platform": platform,
            "url": url,
            "error": str(e),
            "crawled_at": datetime.now().isoformat(),
        }


def is_crawl_result_valid(result: dict) -> bool:
    """
    크롤링 결과가 실제로 유효한 데이터를 포함하는지 확인

    "성공 N / 실패 0" 문제 해결:
    - 에러가 없어도 실제 데이터가 수집되지 않았으면 실패로 처리

    Args:
        result: 크롤링 결과 딕셔너리

    Returns:
        유효한 데이터가 있으면 True
    """
    # 에러가 있으면 무조건 실패
    if result.get("error"):
        return False

    # 플랫폼별 유효성 검사
    platform = result.get("platform", "")

    # 필수 데이터 확인: 작성자 또는 상호작용 수치 중 하나는 있어야 함
    has_author = bool(result.get("author"))
    has_likes = result.get("likes", 0) > 0
    has_comments = result.get("comments", 0) > 0
    has_views = result.get("views", 0) > 0 if result.get("views") is not None else False
    has_favorites = result.get("favorites", 0) > 0
    has_shares = result.get("shares", 0) > 0

    # YouTube: 조회수가 핵심 지표
    if platform == "youtube":
        return has_views or has_likes or has_comments

    # 샤오홍슈: 좋아요나 즐겨찾기가 핵심
    if platform == "xiaohongshu":
        return has_author or has_likes or has_favorites or has_comments

    # 인스타그램: 좋아요가 핵심
    if platform == "instagram":
        return has_author or has_likes or has_comments or has_views

    # 페이스북: 좋아요나 공유가 핵심
    if platform == "facebook":
        return has_author or has_likes or has_shares or has_comments

    # Dcard: 좋아요 또는 댓글
    if platform == "dcard":
        return has_author or has_likes or has_comments

    # 기타 플랫폼: 최소 하나의 지표가 있어야 함
    return has_author or has_likes or has_comments or has_views


def get_crawl_failure_reason(result: dict) -> str:
    """
    크롤링 실패 이유를 사용자 친화적인 메시지로 반환

    Args:
        result: 크롤링 결과 딕셔너리

    Returns:
        실패 이유 메시지
    """
    error = result.get("error", "")
    platform = result.get("platform", "")

    # 명시적 에러가 있는 경우
    if error:
        if "timeout" in error.lower() or "시간" in error:
            return "페이지 로드 시간 초과"
        if "cookie" in error.lower() or "쿠키" in error or "로그인" in error:
            return "쿠키 만료 또는 로그인 필요"
        if "qr" in error.lower():
            return "QR 인증 필요"
        if "rate" in error.lower() or "limit" in error.lower():
            return "요청 제한 (잠시 후 다시 시도)"
        if "cloud" in error.lower():
            return "서버 환경 제한 (로컬 실행 권장)"
        return error[:50]

    # 에러는 없지만 데이터가 비어있는 경우
    has_any_data = (
        result.get("author") or
        result.get("likes", 0) > 0 or
        result.get("comments", 0) > 0 or
        result.get("views", 0) if result.get("views") is not None else False
    )

    if not has_any_data:
        if platform == "xiaohongshu":
            return "데이터 수집 실패 (QR 인증 또는 쿠키 필요)"
        if platform == "instagram":
            return "데이터 수집 실패 (쿠키 만료 또는 비공개 계정)"
        if platform == "facebook":
            return "데이터 수집 실패 (쿠키 만료 또는 비공개 게시물)"
        return "데이터 수집 실패 (인증 필요)"

    return ""


def get_platform_crawl_info(platform: str) -> dict:
    """
    플랫폼별 크롤링 정보 반환

    Args:
        platform: 플랫폼 이름

    Returns:
        예상 시간, 인증 필요 여부 등 정보
    """
    platform_info = {
        "xiaohongshu": {
            "display_name": "샤오홍슈",
            "estimated_time": "15-30초",
            "requires_auth": True,
            "auth_type": "QR",
            "auth_message": "QR 코드 인증이 필요할 수 있습니다. 브라우저 창을 확인해주세요.",
            "tips": "브라우저 창에서 QR 코드가 나타나면 샤오홍슈 앱으로 스캔하세요.",
        },
        "instagram": {
            "display_name": "인스타그램",
            "estimated_time": "5-10초",
            "requires_auth": True,
            "auth_type": "Cookie",
            "auth_message": "로그인 쿠키가 필요합니다. 사이드바에서 인증을 설정하세요.",
            "tips": "쿠키 인증이 만료되면 다시 설정해주세요.",
        },
        "facebook": {
            "display_name": "페이스북",
            "estimated_time": "5-10초",
            "requires_auth": True,
            "auth_type": "Cookie",
            "auth_message": "로그인 쿠키가 필요합니다.",
            "tips": "공개 게시물만 수집 가능합니다.",
        },
        "youtube": {
            "display_name": "유튜브",
            "estimated_time": "3-5초",
            "requires_auth": False,
            "auth_type": None,
            "auth_message": None,
            "tips": "공개 동영상은 인증 없이 수집됩니다.",
        },
        "dcard": {
            "display_name": "Dcard",
            "estimated_time": "3-5초",
            "requires_auth": False,
            "auth_type": None,
            "auth_message": None,
            "tips": "대만 커뮤니티 플랫폼입니다.",
        },
    }
    return platform_info.get(platform, {
        "display_name": platform,
        "estimated_time": "5-10초",
        "requires_auth": False,
        "auth_type": None,
        "auth_message": None,
        "tips": "",
    })


def run_crawling():
    """크롤링 실행"""
    urls = st.session_state.get("urls", [])
    valid_urls = [u for u in urls if u.get("valid")]

    if not valid_urls:
        st.error("크롤링할 유효한 URL이 없습니다.")
        st.session_state.crawling_status = "error"
        return

    st.markdown("### 크롤링 진행 중")

    # 플랫폼별 URL 카운트 분석
    platform_counts = {}
    for u in valid_urls:
        p = u.get("platform", "unknown")
        platform_counts[p] = platform_counts.get(p, 0) + 1

    # 샤오홍슈가 포함된 경우 QR 인증 안내 표시
    if "xiaohongshu" in platform_counts:
        xhs_count = platform_counts["xiaohongshu"]
        st.warning(
            f"**샤오홍슈 QR 인증 안내** ({xhs_count}개 URL)\n\n"
            "샤오홍슈 크롤링 시 QR 코드 인증이 필요할 수 있습니다.\n"
            "- 브라우저 창이 열리면 QR 코드를 확인하세요\n"
            "- 샤오홍슈 앱으로 QR 코드를 스캔하여 인증하세요\n"
            "- 인증 완료 후 자동으로 크롤링이 진행됩니다"
        )

    # 플랫폼별 예상 시간 안내
    with st.expander("플랫폼별 예상 소요 시간", expanded=False):
        for platform, count in platform_counts.items():
            info = get_platform_crawl_info(platform)
            auth_badge = ""
            if info.get("requires_auth"):
                auth_type = info.get("auth_type", "")
                if auth_type == "QR":
                    auth_badge = " [QR 인증 필요]"
                elif auth_type == "Cookie":
                    auth_badge = " [쿠키 인증]"
            st.markdown(
                f"- **{info['display_name']}**: {count}개 URL, "
                f"URL당 약 {info['estimated_time']}{auth_badge}"
            )
            if info.get("tips"):
                st.caption(f"  Tip: {info['tips']}")

    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    platform_status = st.empty()  # 플랫폼별 상태 표시용
    result_container = st.container()

    total = len(valid_urls)
    current_platform = None

    for i, url_info in enumerate(valid_urls):
        url = url_info.get("url")
        platform = url_info.get("platform")
        platform_info = get_platform_crawl_info(platform)

        # 플랫폼이 바뀌면 안내 메시지 업데이트
        if platform != current_platform:
            current_platform = platform

            # 샤오홍슈 시작 시 특별 안내
            if platform == "xiaohongshu":
                platform_status.info(
                    f"**{platform_info['display_name']} 크롤링 시작**\n\n"
                    "QR 코드 인증이 필요하면 브라우저 창을 확인하세요.\n"
                    "인증 대기 중일 수 있습니다..."
                )
            elif platform_info.get("requires_auth"):
                platform_status.info(
                    f"**{platform_info['display_name']} 크롤링 중**\n\n"
                    f"{platform_info.get('auth_message', '')}"
                )
            else:
                platform_status.info(
                    f"**{platform_info['display_name']} 크롤링 중**\n\n"
                    f"예상 소요 시간: URL당 {platform_info['estimated_time']}"
                )

        status_text.markdown(
            f"**진행 중:** {i + 1}/{total} - "
            f"{platform_info['display_name']} - {url[:50]}..."
        )
        progress_bar.progress((i + 1) / total)

        try:
            # 인증 모드 가져오기
            auth_mode = st.session_state.get("auth_mode", False)

            # 인증이 필요한 플랫폼 안내 표시
            if platform == "xiaohongshu" and auth_mode:
                platform_status.warning(
                    f"**{platform_info['display_name']} - QR 인증 대기 중...**\n\n"
                    "브라우저 창에 QR 코드가 나타나면 샤오홍슈 앱으로 스캔하세요.\n"
                    "인증 후 자동으로 진행됩니다."
                )
            elif platform == "dcard" and auth_mode:
                platform_status.warning(
                    f"**{platform_info['display_name']} - Cloudflare 인증 대기 중...**\n\n"
                    "브라우저 창에 Cloudflare 인증이 나타나면 완료해주세요.\n"
                    "인증 후 자동으로 진행됩니다."
                )

            # 쿠키를 포함하여 크롤링 (auth_mode 전달)
            result = crawl_with_cookies(platform, url, auth_mode=auth_mode)
            results.append(result)

            # 결과 표시 - 실제 데이터 수집 여부 확인
            is_valid = is_crawl_result_valid(result)
            failure_reason = get_crawl_failure_reason(result) if not is_valid else ""

            if not is_valid:
                # 에러가 없어도 데이터가 비어있으면 실패 처리
                if not result.get("error"):
                    result["error"] = failure_reason or "데이터 수집 실패"

                with result_container:
                    st.markdown(
                        f"**{i + 1}. {platform_info['display_name']}** - "
                        f"실패: {failure_reason or result.get('error', '')[:50]}"
                    )
                # 실패 시 플랫폼별 안내
                if platform == "xiaohongshu":
                    error_msg = f"**{platform_info['display_name']} - 데이터 수집 실패**\n\n"
                    if IS_LOCAL and not auth_mode:
                        error_msg += "**해결 방법:**\n"
                        error_msg += "1. 사이드바에서 '인증 모드'를 활성화하세요\n"
                        error_msg += "2. 브라우저 창에서 QR 코드를 스캔하세요\n"
                        error_msg += "또는 쿠키를 직접 입력해주세요."
                    else:
                        error_msg += "QR 인증이 필요하거나 쿠키가 만료되었을 수 있습니다.\n"
                        error_msg += "사이드바에서 쿠키를 다시 설정해주세요."
                    platform_status.error(error_msg)
                elif platform == "dcard":
                    error_msg = f"**{platform_info['display_name']} - 데이터 수집 실패**\n\n"
                    if IS_LOCAL and not auth_mode:
                        error_msg += "**해결 방법:**\n"
                        error_msg += "1. 사이드바에서 '인증 모드'를 활성화하세요\n"
                        error_msg += "2. 브라우저 창에서 Cloudflare 인증을 완료하세요\n"
                    else:
                        error_msg += "Cloudflare 인증이 필요합니다.\n"
                        error_msg += "로컬 환경에서 인증 모드를 사용해주세요."
                    platform_status.error(error_msg)
                elif platform in ["instagram", "facebook"]:
                    platform_status.warning(
                        f"**{platform_info['display_name']} - 데이터 수집 실패**\n\n"
                        "쿠키가 만료되었을 수 있습니다.\n"
                        "사이드바에서 쿠키를 다시 설정해주세요."
                    )
            else:
                with result_container:
                    # 수집된 데이터 요약 표시
                    metrics = []
                    if result.get("likes", 0) > 0:
                        metrics.append(f"좋아요 {result['likes']}")
                    if result.get("comments", 0) > 0:
                        metrics.append(f"댓글 {result['comments']}")
                    if result.get("views") and result.get("views", 0) > 0:
                        metrics.append(f"조회수 {result['views']}")
                    metrics_str = ", ".join(metrics) if metrics else "데이터 수집됨"
                    st.markdown(
                        f"**{i + 1}. {platform_info['display_name']}** - 성공 ({metrics_str})"
                    )
                # 성공 시 플랫폼 상태 업데이트
                if platform == "xiaohongshu":
                    platform_status.success(
                        f"**{platform_info['display_name']} - 크롤링 성공!**\n\n"
                        "다음 URL로 진행합니다."
                    )

            # 플랫폼별 딜레이
            if platform in ["xiaohongshu", "instagram", "facebook"]:
                time.sleep(3)
            else:
                time.sleep(1)

        except Exception as e:
            logger.error(f"크롤링 오류 ({url}): {e}")
            results.append({
                "platform": platform,
                "url": url,
                "error": str(e),
                "crawled_at": datetime.now().isoformat(),
            })

            with result_container:
                st.markdown(
                    f"**{i + 1}. {platform_info['display_name']}** - "
                    f"오류: {str(e)[:50]}"
                )

    # 결과 저장
    st.session_state.crawl_results = results
    st.session_state.crawling_status = "completed"

    progress_bar.progress(1.0)
    status_text.markdown("**크롤링 완료!**")
    platform_status.empty()  # 플랫폼 상태 메시지 제거

    # 최종 결과 요약 - 실제 데이터 수집 여부 기준
    success_count = sum(1 for r in results if is_crawl_result_valid(r))
    error_count = len(results) - success_count

    # 실패한 URL들의 플랫폼별 분석
    failed_by_platform = {}
    for r in results:
        if not is_crawl_result_valid(r):
            p = r.get("platform", "unknown")
            if p not in failed_by_platform:
                failed_by_platform[p] = []
            failed_by_platform[p].append(get_crawl_failure_reason(r))

    if error_count == 0:
        st.success(f"크롤링이 완료되었습니다. {len(results)}개 URL 모두 성공!")
    else:
        st.warning(
            f"크롤링이 완료되었습니다. "
            f"성공: {success_count}개, 실패: {error_count}개"
        )
        # 실패 원인 상세 안내
        if failed_by_platform:
            with st.expander("실패 원인 상세", expanded=True):
                for platform, reasons in failed_by_platform.items():
                    info = get_platform_crawl_info(platform)
                    unique_reasons = list(set(reasons))
                    st.markdown(f"**{info['display_name']}** ({len(reasons)}건 실패)")
                    for reason in unique_reasons:
                        st.markdown(f"  - {reason}")
                st.info(
                    "**해결 방법:**\n"
                    "1. 사이드바에서 해당 플랫폼의 쿠키를 다시 설정하세요.\n"
                    "2. 샤오홍슈는 QR 인증이 필요할 수 있습니다.\n"
                    "3. 잠시 후 다시 시도해주세요."
                )

    st.rerun()


def render_results():
    """크롤링 결과 표시"""
    results = st.session_state.get("crawl_results", [])

    if not results:
        return

    st.markdown("### 크롤링 결과")

    # 결과 집계
    aggregated = aggregate_results(results)
    grouped = group_by_platform(results)

    # 요약 지표 카드
    st.markdown("#### 캠페인 총 지표")
    cols = st.columns(5)

    metrics = [
        ("총 게시물", aggregated["total_posts"]),
        ("성공", aggregated["success_count"]),
        ("총 좋아요", format_number(aggregated["total_likes"])),
        ("총 댓글", format_number(aggregated["total_comments"])),
        ("총 조회수", format_number(aggregated["total_views"])),
    ]

    for i, (label, value) in enumerate(metrics):
        with cols[i]:
            st.metric(label, value)

    # 추가 지표
    cols2 = st.columns(4)
    with cols2[0]:
        st.metric("총 공유", format_number(aggregated["total_shares"]))
    with cols2[1]:
        st.metric("총 즐겨찾기", format_number(aggregated.get("total_favorites", 0)))
    with cols2[2]:
        st.metric("평균 좋아요", format_number(int(aggregated["avg_likes"])))
    with cols2[3]:
        st.metric("평균 인게이지먼트", format_number(int(aggregated.get("avg_engagement", 0))))

    # 플랫폼별 요약 테이블
    st.markdown("---")
    st.markdown("#### 플랫폼별 요약")

    summary_df = generate_summary_table(results)
    if not summary_df.empty:
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # 상세 결과 테이블
    st.markdown("---")
    st.markdown("#### 상세 결과")

    df = export_to_dataframe(results)

    # 컬럼명 한글화
    column_rename = {
        "platform": "플랫폼",
        "url": "URL",
        "author": "작성자",
        "title": "제목/내용",
        "likes": "좋아요",
        "comments": "댓글",
        "shares": "공유",
        "views": "조회수",
        "favorites": "즐겨찾기",
        "crawled_at": "수집시간",
        "error": "오류",
    }
    df_display = df.rename(columns=column_rename)

    st.dataframe(df_display, use_container_width=True, hide_index=True)

    # 다운로드 버튼
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        # CSV 다운로드
        csv = df.to_csv(index=False, encoding="utf-8-sig")
        campaign_name = st.session_state.campaign_info.get("name", "campaign")
        filename = f"{campaign_name}_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        st.download_button(
            label="CSV 다운로드",
            data=csv,
            file_name=filename,
            mime="text/csv",
            use_container_width=True,
        )

    with col2:
        # Excel 다운로드
        try:
            from io import BytesIO
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='결과')
                summary_df.to_excel(writer, index=False, sheet_name='요약')
            excel_data = output.getvalue()

            st.download_button(
                label="Excel 다운로드",
                data=excel_data,
                file_name=filename.replace(".csv", ".xlsx"),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except ImportError:
            st.info("Excel 다운로드는 openpyxl 설치 필요")

    with col3:
        # PDF 생성 버튼
        # 유효한 결과만 필터링 (에러 없는 것)
        valid_results = [r for r in results if is_crawl_result_valid(r)]

        if not valid_results:
            st.button(
                "PDF 리포트 생성",
                use_container_width=True,
                type="primary",
                disabled=True,
                help="유효한 크롤링 결과가 없습니다. 먼저 데이터를 성공적으로 수집해주세요."
            )
            st.caption("유효한 데이터가 없어 PDF를 생성할 수 없습니다.")
        else:
            try:
                from src.report import generate_pdf_report

                # PDF 생성 시도
                campaign_info = st.session_state.campaign_info
                try:
                    pdf_bytes = generate_pdf_report(
                        campaign_name=campaign_info.get("name", "캠페인") or "캠페인",
                        advertiser_name=campaign_info.get("advertiser", "광고주") or "광고주",
                        start_date=str(campaign_info.get("start_date", "")) or datetime.now().strftime("%Y-%m-%d"),
                        end_date=str(campaign_info.get("end_date", "")) or datetime.now().strftime("%Y-%m-%d"),
                        results=valid_results  # 유효한 결과만 사용
                    )

                    pdf_filename = f"{campaign_info.get('name', 'campaign') or 'campaign'}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

                    st.download_button(
                        label=f"PDF 리포트 다운로드 ({len(valid_results)}건)",
                        data=pdf_bytes,
                        file_name=pdf_filename,
                        mime="application/pdf",
                        use_container_width=True,
                        type="primary",
                    )
                except Exception as gen_error:
                    logger.error(f"PDF 생성 오류: {gen_error}")
                    if st.button("PDF 리포트 생성", use_container_width=True, type="primary"):
                        st.error(f"PDF 생성 중 오류: {str(gen_error)[:100]}")
                        st.info(
                            "**해결 방법:**\n"
                            "1. reportlab이 설치되어 있는지 확인하세요.\n"
                            "2. 한글 폰트(맑은 고딕)가 시스템에 있는지 확인하세요.\n"
                            "3. 크롤링 데이터가 올바른지 확인하세요."
                        )

            except ImportError as e:
                if st.button("PDF 리포트 생성", use_container_width=True, type="primary"):
                    st.error(f"PDF 생성 모듈을 불러올 수 없습니다: {e}")
                    st.info(
                        "**설치 방법:**\n"
                        "```\npip install reportlab\n```\n"
                        "또는\n"
                        "```\npip install weasyprint\n```"
                    )


def render_main_content():
    """메인 콘텐츠 렌더링"""
    # 헤더
    st.markdown('<p class="main-header">인플루언서 캠페인 성과 리포트</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">멀티 플랫폼 게시물 성과 데이터를 자동으로 수집하고 리포트를 생성합니다.</p>', unsafe_allow_html=True)

    # 상태에 따른 콘텐츠 표시
    crawling_status = st.session_state.get("crawling_status", "idle")

    if crawling_status == "running":
        run_crawling()

    elif crawling_status == "completed":
        render_results()

        # 새로 시작 버튼
        st.markdown("---")
        if st.button("새 캠페인 시작", use_container_width=False):
            st.session_state.urls = []
            st.session_state.crawl_results = []
            st.session_state.crawling_status = "idle"
            st.rerun()

    else:
        # URL 입력 및 미리보기
        render_url_input()

        if st.session_state.get("urls"):
            st.markdown("---")
            render_url_preview()


def main():
    """메인 앱 실행"""
    init_app_state()

    # 인증 확인
    if not is_authenticated():
        # 로그인 페이지
        st.markdown("### 인플루언서 캠페인 성과 리포트")
        st.markdown("---")
        show_login_form()
        return

    # 사이드바
    render_sidebar()

    # 메인 콘텐츠
    render_main_content()


if __name__ == "__main__":
    main()
