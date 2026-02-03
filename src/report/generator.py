"""
PDF 리포트 생성 모듈

HTML 템플릿 기반 PDF 리포트 생성
"""

import io
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


def fetch_thumbnail_image(url: str, max_size: tuple = (80, 80), timeout: int = 5):
    """
    URL에서 썸네일 이미지를 가져와 ReportLab Image 객체로 변환

    Args:
        url: 이미지 URL
        max_size: 최대 크기 (width, height) in mm
        timeout: 요청 타임아웃 (초)

    Returns:
        ReportLab Image 객체 또는 None
    """
    if not url:
        return None

    try:
        import requests
        from reportlab.platypus import Image
        from reportlab.lib.units import mm
        from PIL import Image as PILImage

        # 이미지 다운로드
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=timeout, stream=True)
        response.raise_for_status()

        # PIL로 이미지 로드 및 크기 조정
        img_data = io.BytesIO(response.content)
        pil_img = PILImage.open(img_data)

        # RGB로 변환 (RGBA나 P 모드 처리)
        if pil_img.mode in ('RGBA', 'P'):
            pil_img = pil_img.convert('RGB')

        # 크기 조정 (비율 유지, 고해상도 유지)
        max_w, max_h = max_size
        orig_w, orig_h = pil_img.size

        # PDF 출력 해상도 기준 (150 DPI)
        dpi = 150
        target_w = int(max_w / 25.4 * dpi)  # mm to pixels at 150 DPI
        target_h = int(max_h / 25.4 * dpi)

        # 비율 유지하면서 리사이즈
        ratio = min(target_w / orig_w, target_h / orig_h)
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)

        # 원본보다 작을 때만 리사이즈 (확대는 하지 않음)
        if new_w < orig_w or new_h < orig_h:
            pil_img = pil_img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)

        # BytesIO로 변환
        output = io.BytesIO()
        pil_img.save(output, format='JPEG', quality=90)
        output.seek(0)

        # ReportLab Image 생성 (비율 유지)
        img_w, img_h = pil_img.size
        aspect_ratio = img_w / img_h

        # max 크기 내에서 비율 유지
        if aspect_ratio > (max_w / max_h):
            # 가로가 더 넓음
            final_w = max_w * mm
            final_h = (max_w / aspect_ratio) * mm
        else:
            # 세로가 더 높음
            final_h = max_h * mm
            final_w = (max_h * aspect_ratio) * mm

        img = Image(output, width=final_w, height=final_h)
        return img

    except Exception as e:
        logger.debug(f"썸네일 로딩 실패 ({url[:50]}...): {e}")
        return None

# 플랫폼 한글명
PLATFORM_NAMES_KR = {
    "xiaohongshu": "샤오홍슈",
    "youtube": "유튜브",
    "instagram": "인스타그램",
    "facebook": "페이스북",
    "dcard": "디카드",
}

# 조회수 수집 가능 플랫폼 (참고용 - 실제 표시는 데이터 유무로 판단)
# YouTube: 항상 조회수 공개
# Instagram: 릴스/영상만 조회수 제공, 일반 이미지는 없음
# Facebook: 동영상만 조회수 제공, 이미지/텍스트는 없음
# Xiaohongshu: 일부 게시물만 조회수 제공
# Dcard: 조회수 비공개


def format_number(num: Optional[int]) -> str:
    """
    숫자를 읽기 쉬운 형태로 포맷

    Args:
        num: 포맷할 숫자

    Returns:
        포맷된 문자열
    """
    if num is None or num == 0:
        return "-"

    if not isinstance(num, (int, float)):
        return str(num)

    num = int(num)

    if num >= 100000000:
        return f"{num / 100000000:.1f}억"
    elif num >= 10000:
        return f"{num / 10000:.1f}만"
    elif num >= 1000:
        return f"{num:,}"
    else:
        return str(num)


def format_metric(value: Any) -> str:
    """
    지표 표시 (실제 데이터 유무로 판단)
    - None → 수집 불가 (해당 플랫폼/게시물에서 제공 안 함)
    - 0 → -
    - 숫자 → 포맷팅

    Args:
        value: 지표 값

    Returns:
        포맷된 문자열
    """
    if value is None:
        return "수집 불가"
    return format_number(value)


def format_views_for_platform(views: Optional[int], platform: str) -> str:
    """조회수 표시 (하위 호환용)"""
    return format_metric(views)


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


def wrap_cjk_font(text: str, cjk_font: str = 'MSYaHei') -> str:
    """
    텍스트 내 중국어/일본어 문자를 감지하여 CJK 폰트 태그로 감싸기
    (ReportLab Paragraph 인라인 폰트 변경용)
    """
    if not text:
        return text
    # XML 특수문자 이스케이프 (ReportLab Paragraph는 XML 파서 사용)
    # 순서 중요: & 먼저, 그 다음 < > (이미 변환된 &amp;의 재변환 방지)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    import re
    # 중국어 간체/번체 + 일본어 범위 (한국어 제외)
    # CJK Unified Ideographs, CJK Ext-A, CJK Compatibility
    cjk_pattern = re.compile(r'([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u2e80-\u2eff\u3000-\u303f\u31c0-\u31ef]+)')
    parts = cjk_pattern.split(text)
    if len(parts) == 1:
        return text  # CJK 문자 없음
    result = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if cjk_pattern.match(part):
            result.append(f'<font name="{cjk_font}">{part}</font>')
        else:
            result.append(part)
    return ''.join(result)


def shorten_url(url: str, max_length: int = 50) -> str:
    """
    긴 URL을 줄임

    Args:
        url: 원본 URL
        max_length: 최대 길이

    Returns:
        줄인 URL
    """
    if not url or len(url) <= max_length:
        return url or "-"

    # 프로토콜 제거
    short = url.replace("https://", "").replace("http://", "")

    if len(short) <= max_length:
        return short

    # 중간 생략
    return short[:max_length - 3] + "..."


class PDFReportGenerator:
    """PDF 리포트 생성 클래스"""

    def __init__(self):
        """PDF 생성기 초기화"""
        # 템플릿 디렉토리 설정
        self.template_dir = Path(__file__).parent / "templates"

        # Jinja2 환경 설정
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=True
        )

        # 차트 생성기
        self._chart_generator = None

    @property
    def chart_generator(self):
        """차트 생성기 (lazy loading)"""
        if self._chart_generator is None:
            from .charts import ChartGenerator
            self._chart_generator = ChartGenerator()
        return self._chart_generator

    def _aggregate_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                "total_engagement": 0,
                "avg_engagement": 0,
            }

        success_results = [r for r in results if "error" not in r]
        error_results = [r for r in results if "error" in r]

        total_likes = sum(safe_int(r.get("likes")) for r in success_results)
        total_comments = sum(safe_int(r.get("comments")) for r in success_results)
        total_shares = sum(safe_int(r.get("shares")) for r in success_results)
        total_views = sum(safe_int(r.get("views")) for r in success_results)
        total_favorites = sum(safe_int(r.get("favorites")) for r in success_results)

        total_engagement = total_likes + total_comments + total_shares + total_favorites
        success_count = len(success_results)
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
            "avg_engagement": avg_engagement,
        }

    def _group_by_platform(self, results: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        플랫폼별로 결과 그룹핑

        Args:
            results: 크롤링 결과 리스트

        Returns:
            플랫폼별 결과 딕셔너리
        """
        grouped = {}
        for result in results:
            platform = result.get("platform", "unknown")
            if platform not in grouped:
                grouped[platform] = []
            grouped[platform].append(result)
        return grouped

    def _calculate_platform_stats(
        self,
        grouped_results: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        플랫폼별 통계 계산

        Args:
            grouped_results: 플랫폼별 그룹화된 결과

        Returns:
            플랫폼별 통계 딕셔너리
        """
        stats = {}
        for platform, results in grouped_results.items():
            success_results = [r for r in results if "error" not in r]

            total_likes = sum(safe_int(r.get("likes")) for r in success_results)
            total_comments = sum(safe_int(r.get("comments")) for r in success_results)
            total_shares = sum(safe_int(r.get("shares")) for r in success_results)
            total_views = sum(safe_int(r.get("views")) for r in success_results)
            total_favorites = sum(safe_int(r.get("favorites")) for r in success_results)
            total_engagement = total_likes + total_comments + total_shares + total_favorites

            stats[platform] = {
                "count": len(results),
                "success": len(success_results),
                "errors": len(results) - len(success_results),
                "likes": total_likes,
                "comments": total_comments,
                "shares": total_shares,
                "views": total_views,
                "favorites": total_favorites,
                "engagement": total_engagement,
            }
        return stats

    def _prepare_template_data(
        self,
        campaign_name: str,
        advertiser_name: str,
        start_date: str,
        end_date: str,
        results: List[Dict[str, Any]],
        charts: Dict[str, str],
        logo_base64: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        템플릿 렌더링용 데이터 준비

        Args:
            campaign_name: 캠페인명
            advertiser_name: 광고주명
            start_date: 시작일
            end_date: 종료일
            results: 크롤링 결과 리스트
            charts: 차트 이미지 딕셔너리
            logo_base64: 로고 이미지 (base64)

        Returns:
            템플릿 데이터 딕셔너리
        """
        # 집계 데이터
        aggregated = self._aggregate_results(results)
        grouped = self._group_by_platform(results)
        platform_stats = self._calculate_platform_stats(grouped)

        # 플랫폼별 요약 데이터 준비
        platform_summary = []
        for platform, stats in platform_stats.items():
            platform_summary.append({
                "code": platform,
                "name": PLATFORM_NAMES_KR.get(platform, platform),
                "count": stats["count"],
                "views_formatted": format_number(stats["views"]),
                "likes_formatted": format_number(stats["likes"]),
                "comments_formatted": format_number(stats["comments"]),
                "shares_formatted": format_number(stats["shares"]),
                "favorites_formatted": format_number(stats["favorites"]),
                "engagement_formatted": format_number(stats["engagement"]),
            })

        # 개별 게시물 데이터 준비
        posts = []
        for result in results:
            platform = result.get("platform", "unknown")
            likes = safe_int(result.get("likes"))
            comments = safe_int(result.get("comments"))
            # comments가 0이고 comments_list가 있으면 그 길이 사용
            if comments == 0 and result.get("comments_list"):
                comments = len(result.get("comments_list"))
            shares = safe_int(result.get("shares"))
            favorites = safe_int(result.get("favorites"))
            engagement = likes + comments + shares + favorites

            # title 추출 (title > content > description > caption > url)
            title = result.get("title") or result.get("content") or result.get("description") or result.get("caption") or ""
            if title:
                title = title[:80] + "..." if len(title) > 80 else title
            else:
                title = shorten_url(result.get("url", ""), 40)

            posts.append({
                "platform": platform,
                "platform_name": PLATFORM_NAMES_KR.get(platform, platform),
                "author": result.get("author", "-"),
                "title": title,
                "url": result.get("url", ""),
                "url_short": shorten_url(result.get("url", ""), 45),
                "views_formatted": format_metric(result.get("views")),  # 데이터 유무 기반 표시
                "likes_formatted": format_metric(result.get("likes")),
                "comments_formatted": format_number(comments),
                "shares_formatted": format_metric(result.get("shares")),
                "engagement": engagement,
                "engagement_formatted": format_number(engagement),
                "error": result.get("error"),
                "thumbnail": result.get("thumbnail"),
            })

        # 상위 게시물 (인게이지먼트 기준 정렬)
        success_posts = [p for p in posts if not p.get("error")]
        top_posts = sorted(success_posts, key=lambda x: x["engagement"], reverse=True)[:4]

        return {
            "campaign_name": campaign_name or "캠페인",
            "advertiser_name": advertiser_name or "광고주",
            "start_date": start_date or "-",
            "end_date": end_date or "-",
            "generated_date": datetime.now().strftime("%Y-%m-%d %H:%M"),

            # 요약 지표
            "total_posts": aggregated["total_posts"],
            "success_count": aggregated["success_count"],
            "error_count": aggregated["error_count"],
            "total_views_formatted": format_number(aggregated["total_views"]),
            "total_likes_formatted": format_number(aggregated["total_likes"]),
            "total_comments_formatted": format_number(aggregated["total_comments"]),
            "total_shares_formatted": format_number(aggregated["total_shares"]),
            "total_favorites_formatted": format_number(aggregated["total_favorites"]),
            "total_engagement_formatted": format_number(aggregated["total_engagement"]),
            "avg_engagement_formatted": format_number(int(aggregated["avg_engagement"])),

            # 플랫폼별 요약
            "platform_summary": platform_summary,

            # 개별 게시물
            "posts": posts,

            # 상위 게시물 (Best 콘텐츠)
            "top_posts": top_posts,

            # 차트
            "charts": charts,

            # 로고
            "logo_base64": logo_base64,
        }

    def generate_html(
        self,
        campaign_name: str,
        advertiser_name: str,
        start_date: str,
        end_date: str,
        results: List[Dict[str, Any]],
        logo_base64: Optional[str] = None
    ) -> str:
        """
        HTML 리포트 생성

        Args:
            campaign_name: 캠페인명
            advertiser_name: 광고주명
            start_date: 시작일
            end_date: 종료일
            results: 크롤링 결과 리스트
            logo_base64: 로고 이미지 (base64)

        Returns:
            렌더링된 HTML 문자열
        """
        # 데이터 집계
        aggregated = self._aggregate_results(results)
        grouped = self._group_by_platform(results)
        platform_stats = self._calculate_platform_stats(grouped)

        # 차트 생성
        try:
            charts = self.chart_generator.generate_all_charts(
                results=results,
                platform_data=platform_stats,
                aggregated=aggregated
            )
        except Exception as e:
            logger.error(f"차트 생성 오류: {e}")
            charts = {}

        # 템플릿 데이터 준비
        template_data = self._prepare_template_data(
            campaign_name=campaign_name,
            advertiser_name=advertiser_name,
            start_date=start_date,
            end_date=end_date,
            results=results,
            charts=charts,
            logo_base64=logo_base64
        )

        # 템플릿 렌더링
        template = self.jinja_env.get_template("report.html")
        html = template.render(**template_data)

        return html

    def generate_pdf(
        self,
        campaign_name: str,
        advertiser_name: str,
        start_date: str,
        end_date: str,
        results: List[Dict[str, Any]],
        output_path: Optional[str] = None,
        logo_base64: Optional[str] = None
    ) -> bytes:
        """
        PDF 리포트 생성

        Args:
            campaign_name: 캠페인명
            advertiser_name: 광고주명
            start_date: 시작일
            end_date: 종료일
            results: 크롤링 결과 리스트
            output_path: 출력 파일 경로 (선택)
            logo_base64: 로고 이미지 (base64)

        Returns:
            PDF 바이트 데이터
        """
        # HTML 생성
        html = self.generate_html(
            campaign_name=campaign_name,
            advertiser_name=advertiser_name,
            start_date=start_date,
            end_date=end_date,
            results=results,
            logo_base64=logo_base64
        )

        # WeasyPrint로 PDF 변환
        try:
            from weasyprint import HTML, CSS
            from weasyprint.text.fonts import FontConfiguration

            font_config = FontConfiguration()

            # HTML을 PDF로 변환
            html_doc = HTML(string=html, base_url=str(self.template_dir))
            pdf_bytes = html_doc.write_pdf(font_config=font_config)

            # 파일로 저장 (선택)
            if output_path:
                with open(output_path, 'wb') as f:
                    f.write(pdf_bytes)
                logger.info(f"PDF 저장됨: {output_path}")

            return pdf_bytes

        except ImportError:
            logger.warning("weasyprint를 찾을 수 없습니다. reportlab으로 대체합니다.")
            return self._generate_pdf_reportlab(
                campaign_name=campaign_name,
                advertiser_name=advertiser_name,
                start_date=start_date,
                end_date=end_date,
                results=results,
                output_path=output_path
            )

    def _generate_pdf_reportlab(
        self,
        campaign_name: str,
        advertiser_name: str,
        start_date: str,
        end_date: str,
        results: List[Dict[str, Any]],
        output_path: Optional[str] = None
    ) -> bytes:
        """
        ReportLab을 사용한 PDF 생성 (문서1.jpg 스타일 - 깔끔한 대시보드 형식)

        Args:
            campaign_name: 캠페인명
            advertiser_name: 광고주명
            start_date: 시작일
            end_date: 종료일
            results: 크롤링 결과 리스트
            output_path: 출력 파일 경로 (선택)

        Returns:
            PDF 바이트 데이터
        """
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, HRFlowable, KeepTogether, ListFlowable, ListItem
        )
        from reportlab.platypus.flowables import Flowable
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.graphics.shapes import Drawing, Rect, String
        from reportlab.graphics.charts.barcharts import HorizontalBarChart
        from reportlab.graphics import renderPDF

        # 브랜드 컬러 정의 (문서1.jpg 스타일 - 보라/회색 계열)
        BRAND_PRIMARY = colors.HexColor('#6b46c1')      # 보라색 (메인)
        BRAND_SECONDARY = colors.HexColor('#4a5568')    # 다크 그레이
        BRAND_ACCENT = colors.HexColor('#805ad5')       # 밝은 보라
        BRAND_SUCCESS = colors.HexColor('#48bb78')      # 그린
        BRAND_WARNING = colors.HexColor('#ed8936')      # 오렌지
        BRAND_LIGHT = colors.HexColor('#f7fafc')        # 라이트 그레이
        BRAND_BORDER = colors.HexColor('#e2e8f0')       # 보더 컬러
        CARD_BG = colors.HexColor('#faf5ff')            # 연한 보라 배경

        # 한글 폰트 등록 (크로스플랫폼)
        font_name = 'Helvetica'
        font_name_bold = 'Helvetica-Bold'
        try:
            import sys
            font_candidates = []
            if sys.platform == 'win32':
                # Windows: Malgun Gothic (한국어) + MS YaHei (중국어)
                font_candidates = [
                    ("MalgunGothic", "C:/Windows/Fonts/malgun.ttf",
                     "MalgunGothicBold", "C:/Windows/Fonts/malgunbd.ttf"),
                ]
                # YaHei fallback 등록
                yh_path = "C:/Windows/Fonts/msyh.ttc"
                yh_bold_path = "C:/Windows/Fonts/msyhbd.ttc"
                if os.path.exists(yh_path):
                    pdfmetrics.registerFont(TTFont('MSYaHei', yh_path, subfontIndex=0))
                    if os.path.exists(yh_bold_path):
                        pdfmetrics.registerFont(TTFont('MSYaHeiBold', yh_bold_path, subfontIndex=0))
            else:
                # Linux (Streamlit Cloud): Noto Sans CJK (packages.txt로 설치)
                font_candidates = [
                    ("NotoSansCJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                     "NotoSansCJKBold", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
                    ("NotoSansCJK", "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                     "NotoSansCJKBold", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
                ]

            for reg_name, reg_path, bold_name, bold_path in font_candidates:
                if os.path.exists(reg_path):
                    subfont = 0 if reg_path.endswith('.ttc') else None
                    if subfont is not None:
                        pdfmetrics.registerFont(TTFont(reg_name, reg_path, subfontIndex=subfont))
                    else:
                        pdfmetrics.registerFont(TTFont(reg_name, reg_path))
                    font_name = reg_name
                    if os.path.exists(bold_path):
                        if subfont is not None:
                            pdfmetrics.registerFont(TTFont(bold_name, bold_path, subfontIndex=subfont))
                        else:
                            pdfmetrics.registerFont(TTFont(bold_name, bold_path))
                        font_name_bold = bold_name
                    else:
                        font_name_bold = font_name
                    break
            else:
                logger.warning("한글 폰트를 찾을 수 없습니다. PDF에서 CJK 문자가 깨질 수 있습니다.")
        except Exception as e:
            logger.warning(f"폰트 등록 오류: {e}")

        # 버퍼 생성
        buffer = io.BytesIO()

        # 집계 데이터 먼저 계산
        aggregated = self._aggregate_results(results)
        grouped = self._group_by_platform(results)
        platform_stats = self._calculate_platform_stats(grouped)

        # 페이지 헤더/푸터 함수 (문서1.jpg 스타일 - 간소화)
        page_number = [0]

        def add_page_footer(canvas, doc):
            """간소화된 페이지 푸터"""
            page_number[0] += 1
            canvas.saveState()
            page_width, page_height = A4

            # 페이지 번호만 표시 (하단 중앙)
            canvas.setFont(font_name, 8)
            canvas.setFillColor(colors.HexColor('#a0aec0'))
            canvas.drawCentredString(page_width / 2, 10*mm, f"{page_number[0]}")

            canvas.restoreState()

        # 문서 생성
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=15*mm,
            leftMargin=15*mm,
            topMargin=20*mm,
            bottomMargin=25*mm
        )

        # 스타일 설정
        styles = getSampleStyleSheet()

        # 표지 타이틀 스타일
        styles.add(ParagraphStyle(
            name='CoverTitle',
            fontName=font_name_bold,
            fontSize=32,
            leading=40,
            alignment=TA_CENTER,
            textColor=BRAND_PRIMARY,
            spaceAfter=10*mm
        ))

        # 표지 서브타이틀 스타일
        styles.add(ParagraphStyle(
            name='CoverSubtitle',
            fontName=font_name,
            fontSize=18,
            leading=24,
            alignment=TA_CENTER,
            textColor=BRAND_SECONDARY,
            spaceAfter=5*mm
        ))

        # 표지 정보 스타일
        styles.add(ParagraphStyle(
            name='CoverInfo',
            fontName=font_name,
            fontSize=11,
            leading=16,
            alignment=TA_CENTER,
            textColor=BRAND_SECONDARY
        ))

        # 섹션 헤딩 스타일 (큰 제목)
        styles.add(ParagraphStyle(
            name='SectionHeading',
            fontName=font_name_bold,
            fontSize=16,
            leading=22,
            spaceBefore=8*mm,
            spaceAfter=5*mm,
            textColor=BRAND_PRIMARY,
            borderPadding=(0, 0, 3*mm, 0)
        ))

        # 서브 헤딩 스타일
        styles.add(ParagraphStyle(
            name='SubHeading',
            fontName=font_name_bold,
            fontSize=12,
            leading=16,
            spaceBefore=5*mm,
            spaceAfter=3*mm,
            textColor=BRAND_SECONDARY
        ))

        # 본문 스타일 (기존 BodyText 덮어쓰기)
        styles['BodyText'].fontName = font_name
        styles['BodyText'].fontSize = 10
        styles['BodyText'].leading = 15
        styles['BodyText'].alignment = TA_JUSTIFY
        styles['BodyText'].textColor = BRAND_SECONDARY

        # 인사이트 박스 스타일
        styles.add(ParagraphStyle(
            name='InsightText',
            fontName=font_name,
            fontSize=11,
            leading=16,
            textColor=BRAND_PRIMARY,
            backColor=BRAND_LIGHT,
            borderPadding=10
        ))

        # 강조 숫자 스타일
        styles.add(ParagraphStyle(
            name='HighlightNumber',
            fontName=font_name_bold,
            fontSize=24,
            leading=30,
            alignment=TA_CENTER,
            textColor=BRAND_ACCENT
        ))

        # 캡션 스타일
        styles.add(ParagraphStyle(
            name='Caption',
            fontName=font_name,
            fontSize=8,
            leading=12,
            textColor=colors.HexColor('#718096'),
            alignment=TA_CENTER
        ))

        # 목차 스타일
        styles.add(ParagraphStyle(
            name='TOCEntry',
            fontName=font_name,
            fontSize=11,
            leading=18,
            textColor=BRAND_SECONDARY
        ))

        # 컨텐츠 빌드 (문서1.jpg 스타일 - 대시보드 형식)
        story = []

        # 성공한 결과만 필터
        success_results = [r for r in results if "error" not in r]

        # ============================================================
        # 1. 누적성과 섹션 (메인 페이지)
        # ============================================================
        # 헤더: 누적성과 제목 + 기준일
        header_data = [[
            Paragraph("<b>누적성과</b>", ParagraphStyle(name='HeaderTitle', fontName=font_name_bold, fontSize=14, textColor=BRAND_SECONDARY)),
            Paragraph(f"기준일: {end_date}", ParagraphStyle(name='HeaderDate', fontName=font_name, fontSize=9, textColor=colors.HexColor('#718096'), alignment=TA_RIGHT))
        ]]
        header_table = Table(header_data, colWidths=[90*mm, 90*mm])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(header_table)

        # 캠페인 정보 서브헤더
        sub_header = Paragraph(
            f"{campaign_name} | {advertiser_name} | {start_date} ~ {end_date}",
            ParagraphStyle(name='SubHeader', fontName=font_name, fontSize=9, textColor=colors.HexColor('#718096'))
        )
        story.append(sub_header)
        story.append(Spacer(1, 5*mm))

        # 핵심 인사이트 문구 생성
        total_posts = aggregated['total_posts']
        total_engagement = aggregated['total_engagement']
        total_views = aggregated['total_views']
        avg_engagement = aggregated['avg_engagement']
        platform_count = len(platform_stats)

        # 평균 계산
        avg_views = total_views / total_posts if total_posts > 0 else 0
        avg_engagement_per_post = total_engagement / total_posts if total_posts > 0 else 0

        # ============================================================
        # 누적성과 4개 카드 (계약 범위 내 데이터로 구성)
        # ============================================================
        total_likes = aggregated['total_likes']
        avg_likes = total_likes / total_posts if total_posts > 0 else 0

        metric_cards_data = [
            [
                self._create_metric_card_simple("콘텐츠수", f"{total_posts}개", BRAND_PRIMARY, font_name, font_name_bold, CARD_BG),
                self._create_metric_card_v2("총 조회수", format_number(total_views), format_number(int(avg_views)), BRAND_ACCENT, font_name, font_name_bold),
                self._create_metric_card_v2("총 인게이지먼트", format_number(total_engagement), format_number(int(avg_engagement_per_post)), BRAND_SUCCESS, font_name, font_name_bold),
                self._create_metric_card_v2("총 좋아요", format_number(total_likes), format_number(int(avg_likes)), BRAND_WARNING, font_name, font_name_bold),
            ]
        ]

        metric_cards_table = Table(metric_cards_data, colWidths=[45*mm, 45*mm, 45*mm, 45*mm])
        metric_cards_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(metric_cards_table)
        story.append(Spacer(1, 8*mm))

        # ============================================================
        # Best 콘텐츠 섹션 (문서1.jpg 스타일 - 인게이지먼트 상위 4개)
        # ============================================================
        # Best 콘텐츠 헤더
        best_header_data = [[
            Paragraph("<b>Best 콘텐츠</b>", ParagraphStyle(name='BestHeader', fontName=font_name_bold, fontSize=12, textColor=BRAND_SECONDARY)),
            Paragraph("인게이지먼트순", ParagraphStyle(name='BestSort', fontName=font_name, fontSize=9, textColor=colors.HexColor('#718096'), alignment=TA_RIGHT))
        ]]
        best_header_table = Table(best_header_data, colWidths=[90*mm, 90*mm])
        best_header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(best_header_table)
        story.append(Spacer(1, 3*mm))

        # 인게이지먼트 기준 정렬
        sorted_results = sorted(
            success_results,
            key=lambda x: safe_int(x.get("likes")) + safe_int(x.get("comments")) + safe_int(x.get("shares")) + safe_int(x.get("favorites")),
            reverse=True
        )[:4]

        best_cards = []
        for result in sorted_results:
            platform = result.get("platform", "unknown")
            platform_name = PLATFORM_NAMES_KR.get(platform, platform)
            author = (result.get("author") or "-")[:20]
            engagement = safe_int(result.get("likes")) + safe_int(result.get("comments")) + safe_int(result.get("shares")) + safe_int(result.get("favorites"))

            # 인게이지먼트 표시 (조회수 대신)
            engagement_display = format_number(engagement)

            # 썸네일 로딩 시도 (카드 크기에 맞게 38x28mm)
            thumbnail_url = result.get('thumbnail')
            thumbnail_img = fetch_thumbnail_image(thumbnail_url, max_size=(38, 28), timeout=3) if thumbnail_url else None

            # 카드 내용 구성 (깔끔한 레이아웃)
            # 플랫폼/작성자 스타일
            platform_style = ParagraphStyle(name='BestPlatform', fontName=font_name_bold,
                fontSize=9, textColor=BRAND_PRIMARY, alignment=1, spaceAfter=2)
            author_style = ParagraphStyle(name='BestAuthor', fontName=font_name,
                fontSize=8, textColor=colors.HexColor('#4A5568'), alignment=1)
            reach_style = ParagraphStyle(name='BestReach', fontName=font_name,
                fontSize=8, textColor=BRAND_ACCENT, alignment=1, spaceBefore=2)

            author_text = author if author.startswith('@') else f"@{author}"
            author_text = wrap_cjk_font(author_text)

            if thumbnail_img:
                card_content = [
                    [thumbnail_img],  # 썸네일 (정사각형)
                    [Paragraph(f"<b>{platform_name}</b>", platform_style)],
                    [Paragraph(author_text, author_style)],
                    [Paragraph(f"인게이지: {engagement_display}", reach_style)],
                ]
                row_heights = [30*mm, 5*mm, 5*mm, 5*mm]  # 썸네일 크게 + 텍스트 분리
            else:
                card_content = [
                    [Paragraph(f"<b>{platform_name}</b>", platform_style)],
                    [Paragraph(author_text, author_style)],
                    [Paragraph(f"인게이지: {engagement_display}", reach_style)],
                ]
                row_heights = [8*mm, 6*mm, 6*mm]

            best_card = Table(card_content, colWidths=[42*mm], rowHeights=row_heights)
            best_card.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                ('BOX', (0, 0), (-1, -1), 1, BRAND_BORDER),
                ('LINEABOVE', (0, 0), (-1, 0), 3, BRAND_PRIMARY),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ]))
            best_cards.append(best_card)

        # 4개 미만이면 빈 셀 채우기
        while len(best_cards) < 4:
            empty_card = Table([[""]], colWidths=[42*mm], rowHeights=[20*mm])
            best_cards.append(empty_card)

        best_row = Table([best_cards], colWidths=[45*mm, 45*mm, 45*mm, 45*mm])
        best_row.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(best_row)
        story.append(Spacer(1, 8*mm))

        # ============================================================
        # 등록 콘텐츠 섹션 (문서1.jpg 스타일)
        # ============================================================
        # 등록 콘텐츠 헤더
        content_header_data = [[
            Paragraph(f"<b>등록 콘텐츠</b> {len(results)}", ParagraphStyle(name='ContentHeader', fontName=font_name_bold, fontSize=12, textColor=BRAND_SECONDARY)),
        ]]
        content_header_table = Table(content_header_data, colWidths=[180*mm])
        content_header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(content_header_table)
        story.append(Spacer(1, 3*mm))

        # 게시물 테이블 (계약 범위 내 데이터: 조회수, 인게이지먼트, 좋아요, 댓글, 공유, 저장, URL)
        posts_data = [["No.", "콘텐츠", "조회수", "인게이지", "좋아요", "댓글", "공유", "저장", "URL"]]

        for i, result in enumerate(results[:50], 1):
            platform = result.get("platform", "unknown")
            platform_name = PLATFORM_NAMES_KR.get(platform, platform)
            author = (result.get("author") or "-")[:20]  # 20자까지 허용

            # 콘텐츠 정보 (플랫폼 + 작성자 + 제목)
            author_display = author if author.startswith('@') else f"@{author}"
            author_display = wrap_cjk_font(author_display)
            # 제목 또는 내용에서 첫 줄 추출 (20자 제한)
            title = (result.get("title") or result.get("content")
                     or result.get("description") or result.get("caption") or "")
            if title:
                title = title.split('\n')[0][:20]  # 첫 줄 20자
                title = wrap_cjk_font(title)
                content_info = f"{platform_name}\n{author_display}\n{title}"
            else:
                content_info = f"{platform_name}\n{author_display}"

            # 댓글 수 (comments가 0이면 comments_list 길이 사용)
            comments_count = safe_int(result.get("comments"))
            if comments_count == 0 and result.get("comments_list"):
                comments_count = len(result.get("comments_list"))

            # 인게이지먼트 계산
            engagement = safe_int(result.get("likes")) + comments_count + safe_int(result.get("shares")) + safe_int(result.get("favorites"))

            # 에러가 있는 경우 표시
            if result.get("error"):
                content_info = f"{platform_name}\n(오류)"

            # content_info를 Paragraph로 변환 (CJK 폰트 태그 렌더링 위해)
            content_para = Paragraph(
                content_info.replace('\n', '<br/>'),
                ParagraphStyle(name='CellContent', fontName=font_name, fontSize=7, leading=10)
            )

            # URL 링크 생성
            url = result.get("url", "")
            if url:
                url_para = Paragraph(f'<link href="{url}">링크</link>',
                    ParagraphStyle(name='URLLink', fontName=font_name, fontSize=7, textColor=colors.blue))
            else:
                url_para = "-"

            posts_data.append([
                str(i),
                content_para,
                format_metric(result.get("views")),
                format_number(engagement),
                format_metric(result.get("likes")),
                format_number(comments_count),
                format_metric(result.get("shares")),
                format_metric(result.get("favorites")),
                url_para,
            ])

        posts_table = Table(
            posts_data,
            colWidths=[7*mm, 38*mm, 18*mm, 18*mm, 18*mm, 15*mm, 15*mm, 15*mm, 12*mm]
        )
        posts_table.setStyle(TableStyle([
            # 헤더
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), font_name_bold),
            ('FONTSIZE', (0, 0), (-1, 0), 7),

            # 본문
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 1), (-1, -1), 7),

            # 정렬
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            # 그리드
            ('GRID', (0, 0), (-1, -1), 0.3, BRAND_BORDER),
            ('LINEBELOW', (0, 0), (-1, 0), 1.5, BRAND_PRIMARY),

            # 행 배경색
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),

            # 패딩
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(posts_table)

        if len(results) > 50:
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(
                f"* 총 {len(results)}개 중 50개만 표시",
                styles['Caption']
            ))

        # ============================================================
        # URL 목록 섹션
        # ============================================================
        story.append(Spacer(1, 8*mm))
        url_header = Paragraph(
            "<b>콘텐츠 URL 목록</b>",
            ParagraphStyle(name='URLHeader', fontName=font_name_bold, fontSize=10, textColor=BRAND_SECONDARY)
        )
        story.append(url_header)
        story.append(Spacer(1, 2*mm))

        # URL 목록 (최대 50개)
        url_style = ParagraphStyle(
            name='URLText',
            fontName=font_name,
            fontSize=6,
            textColor=colors.HexColor('#4A5568'),
            leading=9
        )

        url_list_items = []
        for i, result in enumerate(results[:50], 1):
            url = result.get("url", "-")
            platform = result.get("platform", "unknown")
            platform_name = PLATFORM_NAMES_KR.get(platform, platform)
            # URL 표시 (너무 길면 중간 생략)
            if len(url) > 70:
                url_display = url[:35] + "..." + url[-30:]
            else:
                url_display = url
            url_list_items.append(f"{i}. [{platform_name}] {url_display}")

        # 2열로 나누어 표시
        mid = (len(url_list_items) + 1) // 2
        col1 = "\n".join(url_list_items[:mid])
        col2 = "\n".join(url_list_items[mid:])

        url_table = Table([
            [Paragraph(col1, url_style), Paragraph(col2, url_style)]
        ], colWidths=[90*mm, 90*mm])
        url_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ]))
        story.append(url_table)

        # ============================================================
        # 게시물 본문 섹션
        # ============================================================
        posts_with_content = [r for r in results if not r.get("error") and (r.get("content") or r.get("title") or r.get("caption") or r.get("description"))]

        if posts_with_content:
            story.append(Spacer(1, 8*mm))
            content_section_header = Paragraph(
                "<b>게시물 본문</b>",
                ParagraphStyle(name='ContentSectionHeader', fontName=font_name_bold, fontSize=12, textColor=BRAND_SECONDARY)
            )
            story.append(content_section_header)
            story.append(Spacer(1, 3*mm))

            for idx, result in enumerate(posts_with_content, 1):
                platform = result.get("platform", "unknown")
                platform_name = PLATFORM_NAMES_KR.get(platform, platform)
                author = (result.get("author") or "-")[:20]
                author_display = author if author.startswith('@') else f"@{author}"
                author_display = wrap_cjk_font(author_display)

                # 게시물 헤더 (번호 + 플랫폼 + 작성자)
                post_header = Paragraph(
                    f"<b>{idx}. {platform_name}</b> {author_display}",
                    ParagraphStyle(name='PostContentHeader', fontName=font_name_bold, fontSize=9, textColor=BRAND_PRIMARY, spaceBefore=3*mm)
                )
                story.append(post_header)

                # 본문 내용 (title + content 중복 제거)
                title_text = (result.get("title") or "").strip()
                body_text = (result.get("content") or result.get("caption") or result.get("description") or "").strip()

                # 중복 제거: 동일하거나, 한쪽이 다른 쪽에 포함되면 긴 쪽만 표시
                if title_text and body_text:
                    if title_text == body_text:
                        body_text = ""
                    elif body_text.startswith(title_text[:30]):
                        # content가 title로 시작하면 content만 표시 (샤오홍슈 등)
                        title_text = ""
                    elif title_text.startswith(body_text[:30]):
                        # title이 content로 시작하면 title만 표시
                        body_text = ""

                display_text = ""
                if title_text:
                    title_text = title_text[:200]
                    title_text = wrap_cjk_font(title_text)
                    display_text += f"<b>{title_text}</b>"
                if body_text:
                    body_text = body_text[:500]
                    body_text = wrap_cjk_font(body_text)
                    if display_text:
                        display_text += f"<br/>{body_text}"
                    else:
                        display_text = body_text

                if display_text:
                    content_para = Paragraph(
                        display_text,
                        ParagraphStyle(name='PostContentBody', fontName=font_name, fontSize=8, leading=12,
                                       textColor=BRAND_SECONDARY, leftIndent=5*mm, spaceBefore=1*mm, spaceAfter=2*mm)
                    )
                    story.append(content_para)

        # ============================================================
        # 댓글 샘플 섹션 (인게이지먼트 상위 게시물의 댓글)
        # ============================================================
        # 댓글이 있는 게시물 필터링 (comments_list가 있거나 댓글 수가 0보다 큰 경우)
        posts_with_comments = [r for r in sorted_results if r.get('comments_list') or safe_int(r.get('comments', 0)) > 0]

        if posts_with_comments:
            story.append(Spacer(1, 8*mm))

            # 댓글 섹션 헤더
            comment_header = Paragraph(
                "<b>댓글 샘플</b> (Best 콘텐츠)",
                ParagraphStyle(name='CommentHeader', fontName=font_name_bold, fontSize=12, textColor=BRAND_SECONDARY)
            )
            story.append(comment_header)
            story.append(Spacer(1, 3*mm))

            # 상위 4개 게시물의 댓글 표시
            for result in posts_with_comments[:4]:
                platform = result.get("platform", "unknown")
                platform_name = PLATFORM_NAMES_KR.get(platform, platform)
                author = (result.get("author") or "-")[:15]
                comments_list = result.get("comments_list", [])
                comments_count = safe_int(result.get("comments", 0))

                # 게시물 제목
                author_display = author if author.startswith('@') else f"@{author}"
                author_display = wrap_cjk_font(author_display)
                post_title = Paragraph(
                    f"<b>{platform_name}</b> {author_display}",
                    ParagraphStyle(name='CommentPostTitle', fontName=font_name_bold, fontSize=9, textColor=BRAND_PRIMARY, spaceBefore=3*mm)
                )
                story.append(post_title)

                # comments_list가 비어있지만 댓글 수가 있는 경우
                if not comments_list:
                    no_comments_msg = Paragraph(
                        f"<font color='#a0aec0'>[댓글 내용 수집 불가 - 총 {comments_count}개]</font>",
                        ParagraphStyle(name='NoComments', fontName=font_name, fontSize=8, textColor=colors.HexColor('#a0aec0'), leftIndent=5*mm, spaceBefore=1*mm)
                    )
                    story.append(no_comments_msg)
                    continue

                # 댓글 목록 (최대 5개)
                for comment in comments_list[:5]:
                    comment_author = comment.get("author", "익명")[:10]
                    comment_text = comment.get("text", "")[:100]  # 100자 제한
                    comment_likes = comment.get("likes", 0)

                    if comment_text:
                        # 이모지만 있는 댓글 처리 (reportlab이 이모지 지원 안함)
                        import re
                        # 이모지 패턴: 대부분의 이모지 범위
                        emoji_pattern = re.compile(
                            "["
                            "\U0001F600-\U0001F64F"  # 이모티콘
                            "\U0001F300-\U0001F5FF"  # 기호 & 픽토그램
                            "\U0001F680-\U0001F6FF"  # 교통 & 지도
                            "\U0001F1E0-\U0001F1FF"  # 국기
                            "\U00002702-\U000027B0"  # 딩벳
                            "\U0001F900-\U0001F9FF"  # 보충 기호
                            "\U0001FA00-\U0001FA6F"  # 체스 기호
                            "\U00002600-\U000026FF"  # 기타 기호
                            "\U00002B50-\U00002B55"  # 별 등
                            "]+", flags=re.UNICODE
                        )
                        # 이모지 제거 후 텍스트만 남기기
                        text_only = emoji_pattern.sub('', comment_text).strip()
                        if not text_only:
                            # 이모지만 있는 댓글
                            comment_text = "[이모지 반응]"
                        else:
                            # 이모지 제거하고 텍스트만 표시
                            comment_text = text_only

                        # 특수문자 이스케이프는 wrap_cjk_font 내부에서 처리

                        comment_author_display = comment_author if comment_author.startswith('@') else f"@{comment_author}"
                        comment_author_display = wrap_cjk_font(comment_author_display)
                        comment_text = wrap_cjk_font(comment_text)
                        comment_para = Paragraph(
                            f"<font color='#718096'>{comment_author_display}</font>: {comment_text} <font color='#a0aec0'>({comment_likes} 좋아요)</font>",
                            ParagraphStyle(name='CommentText', fontName=font_name, fontSize=8, textColor=BRAND_SECONDARY, leftIndent=5*mm, spaceBefore=1*mm)
                        )
                        story.append(comment_para)

        # 푸터 (생성일)
        story.append(Spacer(1, 10*mm))
        footer_text = Paragraph(
            f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')} | {campaign_name} | {advertiser_name}",
            ParagraphStyle(name='Footer', fontName=font_name, fontSize=8, textColor=colors.HexColor('#a0aec0'), alignment=TA_CENTER)
        )
        story.append(footer_text)

        # ============================================================
        # PDF 빌드
        # ============================================================
        doc.build(story, onFirstPage=add_page_footer, onLaterPages=add_page_footer)

        # 바이트 데이터 추출
        pdf_bytes = buffer.getvalue()
        buffer.close()

        # 파일로 저장 (선택)
        if output_path:
            with open(output_path, 'wb') as f:
                f.write(pdf_bytes)
            logger.info(f"PDF 저장됨: {output_path}")

        return pdf_bytes

    def _create_metric_card(
        self,
        title: str,
        value: str,
        color,
        font_name: str,
        font_name_bold: str
    ):
        """
        지표 카드 생성 헬퍼 함수

        Args:
            title: 카드 제목
            value: 지표 값
            color: 강조 색상
            font_name: 일반 폰트명
            font_name_bold: 굵은 폰트명

        Returns:
            Table 객체
        """
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Table, TableStyle

        title_style = ParagraphStyle(
            name='CardTitle',
            fontName=font_name,
            fontSize=9,
            textColor=colors.HexColor('#718096'),
            alignment=1  # CENTER
        )

        value_style = ParagraphStyle(
            name='CardValue',
            fontName=font_name_bold,
            fontSize=18,
            textColor=color,
            alignment=1  # CENTER
        )

        card_data = [
            [Paragraph(title, title_style)],
            [Paragraph(value, value_style)],
        ]

        card = Table(card_data, colWidths=[38*mm], rowHeights=[8*mm, 12*mm])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('LINEABOVE', (0, 0), (-1, 0), 3, color),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        return card

    def _create_metric_card_simple(
        self,
        title: str,
        value: str,
        color,
        font_name: str,
        font_name_bold: str,
        bg_color=None
    ):
        """
        단순 지표 카드 생성 (문서1.jpg 스타일 - 콘텐츠수용)

        Args:
            title: 카드 제목
            value: 지표 값
            color: 강조 색상
            font_name: 일반 폰트명
            font_name_bold: 굵은 폰트명
            bg_color: 배경 색상

        Returns:
            Table 객체
        """
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Table, TableStyle

        title_style = ParagraphStyle(
            name='CardTitleSimple',
            fontName=font_name,
            fontSize=9,
            textColor=colors.HexColor('#718096'),
            alignment=1
        )

        value_style = ParagraphStyle(
            name='CardValueSimple',
            fontName=font_name_bold,
            fontSize=22,
            textColor=color,
            alignment=1
        )

        card_data = [
            [Paragraph(title, title_style)],
            [Paragraph(value, value_style)],
        ]

        card = Table(card_data, colWidths=[42*mm], rowHeights=[10*mm, 18*mm])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), bg_color or colors.white),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('LINEABOVE', (0, 0), (-1, 0), 3, color),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        return card

    def _create_metric_card_v2(
        self,
        title: str,
        total_value: str,
        avg_value: str,
        color,
        font_name: str,
        font_name_bold: str
    ):
        """
        누적/평균을 표시하는 지표 카드 생성 (고객 요청 스타일)

        Args:
            title: 카드 제목 (예: "총 인게이지먼트")
            total_value: 누적 값
            avg_value: 콘텐츠 평균 값
            color: 강조 색상
            font_name: 일반 폰트명
            font_name_bold: 굵은 폰트명

        Returns:
            Table 객체
        """
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Table, TableStyle

        title_style = ParagraphStyle(
            name='CardTitleV2',
            fontName=font_name,
            fontSize=9,
            textColor=colors.HexColor('#718096'),
            alignment=1  # CENTER
        )

        total_label_style = ParagraphStyle(
            name='TotalLabel',
            fontName=font_name,
            fontSize=7,
            textColor=colors.HexColor('#a0aec0'),
            alignment=1
        )

        total_value_style = ParagraphStyle(
            name='TotalValue',
            fontName=font_name_bold,
            fontSize=20,
            textColor=color,
            alignment=1
        )

        avg_style = ParagraphStyle(
            name='AvgStyle',
            fontName=font_name,
            fontSize=8,
            textColor=colors.HexColor('#718096'),
            alignment=1
        )

        card_data = [
            [Paragraph(title, title_style)],
            [Paragraph("누적", total_label_style)],
            [Paragraph(total_value, total_value_style)],
            [Paragraph(f"콘텐츠 평균 {avg_value}", avg_style)],
        ]

        card = Table(card_data, colWidths=[42*mm], rowHeights=[7*mm, 5*mm, 14*mm, 6*mm])
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('LINEABOVE', (0, 0), (-1, 0), 3, color),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))

        return card


def generate_pdf_report(
    campaign_name: str,
    advertiser_name: str,
    start_date: str,
    end_date: str,
    results: List[Dict[str, Any]],
    output_path: Optional[str] = None,
    logo_base64: Optional[str] = None
) -> bytes:
    """
    PDF 리포트 생성 (편의 함수)

    Args:
        campaign_name: 캠페인명
        advertiser_name: 광고주명
        start_date: 시작일 (YYYY-MM-DD 형식)
        end_date: 종료일 (YYYY-MM-DD 형식)
        results: 크롤링 결과 리스트
        output_path: 출력 파일 경로 (선택, 지정 시 파일로도 저장)
        logo_base64: 로고 이미지 (base64 인코딩, 선택)

    Returns:
        PDF 바이트 데이터 (Streamlit 다운로드용)

    Example:
        >>> results = [
        ...     {"platform": "youtube", "url": "...", "likes": 1000, "views": 50000},
        ...     {"platform": "instagram", "url": "...", "likes": 500, "comments": 50},
        ... ]
        >>> pdf_bytes = generate_pdf_report(
        ...     campaign_name="2024 신제품 런칭",
        ...     advertiser_name="ABC 브랜드",
        ...     start_date="2024-01-01",
        ...     end_date="2024-01-31",
        ...     results=results
        ... )
        >>> # Streamlit 다운로드
        >>> st.download_button("PDF 다운로드", pdf_bytes, "report.pdf", "application/pdf")
    """
    generator = PDFReportGenerator()
    return generator.generate_pdf(
        campaign_name=campaign_name,
        advertiser_name=advertiser_name,
        start_date=start_date,
        end_date=end_date,
        results=results,
        output_path=output_path,
        logo_base64=logo_base64
    )
