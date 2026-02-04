"""
데이터 처리 및 집계 모듈

크롤링 결과를 처리하고 캠페인 지표를 계산
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

logger = logging.getLogger(__name__)

# 플랫폼별 지원 지표
PLATFORM_METRICS = {
    "xiaohongshu": ["likes", "favorites", "comments", "shares", "views"],
    "youtube": ["views", "likes", "comments", "subscribers"],
    "instagram": ["likes", "comments", "views"],
    "facebook": ["likes", "comments", "shares", "views"],
    "dcard": ["likes", "comments"],
}

# 조회수 수집 가능 플랫폼 (True = 수집 가능, False = 수집 불가)
PLATFORM_VIEW_SUPPORT = {
    "youtube": True,       # YouTube는 조회수 공개
    "instagram": False,    # Instagram 일반 게시물은 조회수 비공개 (릴스만 제공)
    "facebook": False,     # Facebook 공개 페이지도 조회수 비공개
    "xiaohongshu": False,  # 샤오홍슈 조회수 비공개
    "dcard": False,        # Dcard 조회수 비공개
}

# 지표 표시명 (한국어)
METRIC_DISPLAY_NAMES = {
    "likes": "좋아요",
    "favorites": "즐겨찾기",
    "comments": "댓글",
    "shares": "공유",
    "views": "조회수",
    "subscribers": "구독자",
}


def format_number(num: Optional[int]) -> str:
    """
    숫자를 읽기 쉬운 형태로 포맷

    Args:
        num: 포맷할 숫자

    Returns:
        포맷된 문자열 (예: 1,234 / 1.2만 / 1.5M)
    """
    if num is None or num == 0:
        return "-"

    if not isinstance(num, (int, float)):
        return str(num)

    num = int(num)

    if num >= 100000000:  # 1억 이상
        return f"{num / 100000000:.1f}억"
    elif num >= 10000:  # 1만 이상
        return f"{num / 10000:.1f}만"
    elif num >= 1000:
        return f"{num:,}"
    else:
        return str(num)


def safe_int(value: Any, default: int = 0) -> int:
    """
    안전하게 정수로 변환

    Args:
        value: 변환할 값
        default: 변환 실패 시 기본값

    Returns:
        정수 값
    """
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def aggregate_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    크롤링 결과 집계

    Args:
        results: 크롤링 결과 리스트

    Returns:
        집계된 결과
    """
    if not results:
        return {
            "total_posts": 0,
            "success_count": 0,
            "error_count": 0,
            "total_likes": 0,
            "total_comments": 0,
            "total_shares": 0,
            "total_views": 0,
            "total_favorites": 0,
            "avg_likes": 0,
            "avg_comments": 0,
            "avg_engagement": 0,
        }

    def _is_valid(r):
        """에러 없고, 최소 1개 데이터 지표가 있는지 확인"""
        if r.get("error"):
            return False
        return bool(
            r.get("author") or (r.get("likes") or 0) > 0
            or (r.get("comments") or 0) > 0 or (r.get("views") or 0) > 0
            or (r.get("favorites") or 0) > 0 or (r.get("shares") or 0) > 0
        )
    success_results = [r for r in results if _is_valid(r)]
    error_results = [r for r in results if not _is_valid(r)]

    total_likes = sum(safe_int(r.get("likes")) for r in success_results)
    total_comments = sum(safe_int(r.get("comments")) for r in success_results)
    total_shares = sum(safe_int(r.get("shares")) for r in success_results)
    total_views = sum(safe_int(r.get("views")) for r in success_results)
    total_favorites = sum(safe_int(r.get("favorites")) for r in success_results)

    success_count = len(success_results)
    avg_likes = total_likes / success_count if success_count > 0 else 0
    avg_comments = total_comments / success_count if success_count > 0 else 0

    # 총 인게이지먼트 (좋아요 + 댓글 + 공유 + 즐겨찾기)
    total_engagement = total_likes + total_comments + total_shares + total_favorites
    avg_engagement = total_engagement / success_count if success_count > 0 else 0

    return {
        "total_posts": len(results),
        "success_count": success_count,
        "error_count": len(error_results),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "total_views": total_views,
        "total_favorites": total_favorites,
        "total_engagement": total_engagement,
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "avg_engagement": avg_engagement,
    }


def group_by_platform(results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    플랫폼별로 결과 그룹핑

    Args:
        results: 크롤링 결과 리스트

    Returns:
        {"youtube": [...], "instagram": [...], ...}
    """
    grouped = {}
    for result in results:
        platform = result.get("platform", "unknown")
        if platform not in grouped:
            grouped[platform] = []
        grouped[platform].append(result)
    return grouped


def calculate_campaign_metrics(
    results: List[Dict[str, Any]],
    campaign_name: str = "",
    advertiser: str = "",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    캠페인 전체 지표 계산

    Args:
        results: 크롤링 결과 리스트
        campaign_name: 캠페인명
        advertiser: 광고주명
        start_date: 캠페인 시작일
        end_date: 캠페인 종료일

    Returns:
        캠페인 지표 딕셔너리
    """
    aggregated = aggregate_results(results)
    grouped = group_by_platform(results)

    # 플랫폼별 통계
    platform_stats = {}
    for platform, platform_results in grouped.items():
        platform_agg = aggregate_results(platform_results)
        platform_stats[platform] = {
            "count": len(platform_results),
            "success": platform_agg["success_count"],
            "errors": platform_agg["error_count"],
            "likes": platform_agg["total_likes"],
            "comments": platform_agg["total_comments"],
            "shares": platform_agg["total_shares"],
            "views": platform_agg["total_views"],
            "favorites": platform_agg["total_favorites"],
            "engagement": platform_agg["total_engagement"],
        }

    return {
        "campaign_name": campaign_name,
        "advertiser": advertiser,
        "period": {
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        "summary": aggregated,
        "by_platform": platform_stats,
        "generated_at": datetime.now().isoformat(),
    }


def export_to_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    크롤링 결과를 pandas DataFrame으로 변환

    Args:
        results: 크롤링 결과 리스트

    Returns:
        pandas DataFrame
    """
    if not results:
        return pd.DataFrame()

    # 컬럼 정의
    columns = [
        "platform",
        "url",
        "author",
        "title",
        "content",
        "likes",
        "comments",
        "shares",
        "views",
        "favorites",
        "crawled_at",
        "error",
    ]

    # 데이터 정리
    rows = []
    for result in results:
        row = {col: result.get(col, None) for col in columns}
        # title이 없으면 content 또는 caption 사용
        if not row["title"]:
            row["title"] = result.get("content") or result.get("caption") or result.get("description") or ""
        # content가 없으면 caption 또는 description 사용
        if not row["content"]:
            row["content"] = result.get("caption") or result.get("description") or ""
        rows.append(row)

    df = pd.DataFrame(rows, columns=columns)

    # 타입 변환 (int64 사용 - 50억+ 조회수 지원)
    # views는 None 보존 (수집 불가 구분용 - v1.5.8)
    numeric_cols = ["likes", "comments", "shares", "favorites"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype('int64')
    # views: None → NaN 유지 (데이터 유무로 "수집 불가" 판단)
    df["views"] = pd.to_numeric(df["views"], errors="coerce")

    return df


def generate_summary_table(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    요약 테이블 생성 (플랫폼별)

    Args:
        results: 크롤링 결과 리스트

    Returns:
        요약 DataFrame
    """
    grouped = group_by_platform(results)

    rows = []
    for platform, platform_results in grouped.items():
        success_results = [r for r in platform_results if "error" not in r]
        error_count = len(platform_results) - len(success_results)

        total_likes = sum(safe_int(r.get("likes")) for r in success_results)
        total_comments = sum(safe_int(r.get("comments")) for r in success_results)
        total_shares = sum(safe_int(r.get("shares")) for r in success_results)
        total_views = sum(safe_int(r.get("views")) for r in success_results)
        total_favorites = sum(safe_int(r.get("favorites")) for r in success_results)

        # 조회수 표시 (실제 데이터 유무로 판단 - v1.5.8)
        has_views_data = any(r.get("views") is not None for r in success_results)
        if has_views_data:
            views_display = total_views if total_views > 0 else "-"
        else:
            views_display = "수집 불가"

        rows.append({
            "플랫폼": get_platform_display_name(platform),
            "게시물 수": len(platform_results),
            "성공": len(success_results),
            "실패": error_count,
            "총 좋아요": total_likes,
            "총 댓글": total_comments,
            "총 공유": total_shares,
            "총 조회수": views_display,
            "총 즐겨찾기": total_favorites if total_favorites > 0 else "-",
        })

    if rows:
        df = pd.DataFrame(rows)
        return df
    else:
        return pd.DataFrame()


def get_platform_display_name(platform: str) -> str:
    """
    플랫폼 표시명 반환

    Args:
        platform: 플랫폼 코드

    Returns:
        표시명
    """
    display_names = {
        "xiaohongshu": "샤오홍슈",
        "youtube": "유튜브",
        "instagram": "인스타그램",
        "facebook": "페이스북",
        "dcard": "디카드",
    }
    return display_names.get(platform, platform)


def prepare_for_pdf(
    results: List[Dict[str, Any]],
    campaign_info: Dict[str, Any]
) -> Dict[str, Any]:
    """
    PDF 리포트 생성을 위한 데이터 준비

    Args:
        results: 크롤링 결과 리스트
        campaign_info: 캠페인 정보

    Returns:
        PDF 생성용 데이터
    """
    metrics = calculate_campaign_metrics(
        results,
        campaign_name=campaign_info.get("name", ""),
        advertiser=campaign_info.get("advertiser", ""),
        start_date=campaign_info.get("start_date"),
        end_date=campaign_info.get("end_date"),
    )

    df = export_to_dataframe(results)
    summary_df = generate_summary_table(results)

    return {
        "campaign_info": campaign_info,
        "metrics": metrics,
        "results_df": df,
        "summary_df": summary_df,
        "grouped_results": group_by_platform(results),
    }
