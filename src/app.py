"""
인플루언서 캠페인 성과 리포트 - Streamlit 웹 앱

멀티 플랫폼 크롤링 및 결과 리포트 생성
v2.0 - 플랫폼 쿠키 인증 지원
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import time

import streamlit as st
import pandas as pd

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


def crawl_with_cookies(platform: str, url: str) -> dict:
    """
    쿠키를 포함하여 크롤링 수행

    Args:
        platform: 플랫폼 이름
        url: 크롤링할 URL

    Returns:
        크롤링 결과
    """
    cookies = get_platform_cookies(platform)
    crawler = get_crawler_for_platform(platform)

    if not crawler:
        return {
            "platform": platform,
            "url": url,
            "error": f"지원하지 않는 플랫폼: {platform}",
            "crawled_at": datetime.now().isoformat(),
        }

    try:
        # YouTube는 쿠키 불필요
        if platform == "youtube":
            return crawler(url)

        # 다른 플랫폼은 쿠키와 함께 크롤링
        # 각 크롤러 클래스에서 쿠키 처리
        if platform == "instagram":
            from src.crawlers.instagram_crawler import InstagramCrawler
            with InstagramCrawler(headless=True, use_api=True) as crawler_instance:
                # 쿠키가 있으면 세션에 적용
                if cookies and crawler_instance.session:
                    crawler_instance.session.cookies.update(cookies)
                return crawler_instance.crawl_post(url)

        elif platform == "facebook":
            from src.crawlers.facebook_crawler import FacebookCrawler
            with FacebookCrawler(headless=True, use_api=True) as crawler_instance:
                if cookies and crawler_instance.session:
                    crawler_instance.session.cookies.update(cookies)
                return crawler_instance.crawl_post(url)

        elif platform == "xiaohongshu":
            from src.crawlers.xhs_crawler import XHSCrawler
            with XHSCrawler(headless=True, use_api=True) as crawler_instance:
                if cookies and crawler_instance.session:
                    crawler_instance.session.cookies.update(cookies)
                return crawler_instance.crawl_post(url)

        elif platform == "dcard":
            from src.crawlers.dcard_crawler import DcardCrawler
            with DcardCrawler(headless=True, use_api=True) as crawler_instance:
                if cookies and hasattr(crawler_instance, 'scraper'):
                    crawler_instance.scraper.cookies.update(cookies)
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


def run_crawling():
    """크롤링 실행"""
    urls = st.session_state.get("urls", [])
    valid_urls = [u for u in urls if u.get("valid")]

    if not valid_urls:
        st.error("크롤링할 유효한 URL이 없습니다.")
        st.session_state.crawling_status = "error"
        return

    st.markdown("### 크롤링 진행 중")

    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    result_container = st.container()

    total = len(valid_urls)

    for i, url_info in enumerate(valid_urls):
        url = url_info.get("url")
        platform = url_info.get("platform")

        status_text.markdown(f"**진행 중:** {i + 1}/{total} - {platform} - {url[:50]}...")
        progress_bar.progress((i + 1) / total)

        try:
            # 쿠키를 포함하여 크롤링
            result = crawl_with_cookies(platform, url)
            results.append(result)

            # 결과 표시
            if result.get("error"):
                with result_container:
                    st.markdown(f"**{i + 1}. {platform}** - ⚠️ {result.get('error', '')[:30]}")
            else:
                with result_container:
                    st.markdown(f"**{i + 1}. {platform}** - ✅ 성공")

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
                st.markdown(f"**{i + 1}. {platform}** - 오류: {str(e)[:50]}")

    # 결과 저장
    st.session_state.crawl_results = results
    st.session_state.crawling_status = "completed"

    progress_bar.progress(1.0)
    status_text.markdown("**크롤링 완료!**")

    st.success(f"크롤링이 완료되었습니다. {len(results)}개 URL 처리됨.")
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
        try:
            from src.report import generate_pdf_report

            # PDF 생성
            campaign_info = st.session_state.campaign_info
            pdf_bytes = generate_pdf_report(
                campaign_name=campaign_info.get("name", "캠페인"),
                advertiser_name=campaign_info.get("advertiser", "광고주"),
                start_date=str(campaign_info.get("start_date", "")),
                end_date=str(campaign_info.get("end_date", "")),
                results=results
            )

            pdf_filename = f"{campaign_info.get('name', 'campaign')}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

            st.download_button(
                label="PDF 리포트 다운로드",
                data=pdf_bytes,
                file_name=pdf_filename,
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )
        except ImportError as e:
            if st.button("PDF 리포트 생성", use_container_width=True, type="primary"):
                st.error(f"PDF 생성 모듈을 불러올 수 없습니다: {e}")
                st.info("weasyprint 또는 reportlab 설치가 필요합니다.")
        except Exception as e:
            if st.button("PDF 리포트 생성", use_container_width=True, type="primary"):
                st.error(f"PDF 생성 중 오류가 발생했습니다: {e}")


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
