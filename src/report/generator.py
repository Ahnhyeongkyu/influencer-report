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

# 플랫폼 한글명
PLATFORM_NAMES_KR = {
    "xiaohongshu": "샤오홍슈",
    "youtube": "유튜브",
    "instagram": "인스타그램",
    "facebook": "페이스북",
    "dcard": "디카드",
}


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
            posts.append({
                "platform": platform,
                "platform_name": PLATFORM_NAMES_KR.get(platform, platform),
                "author": result.get("author", "-"),
                "url": result.get("url", ""),
                "url_short": shorten_url(result.get("url", ""), 45),
                "views_formatted": format_number(safe_int(result.get("views"))),
                "likes_formatted": format_number(safe_int(result.get("likes"))),
                "comments_formatted": format_number(safe_int(result.get("comments"))),
                "shares_formatted": format_number(safe_int(result.get("shares"))),
                "error": result.get("error"),
            })

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
        ReportLab을 사용한 PDF 생성 (전문 보고서 형식)

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

        # 브랜드 컬러 정의
        BRAND_PRIMARY = colors.HexColor('#1a365d')      # 네이비 블루
        BRAND_SECONDARY = colors.HexColor('#2d3748')    # 다크 그레이
        BRAND_ACCENT = colors.HexColor('#3182ce')       # 밝은 블루
        BRAND_SUCCESS = colors.HexColor('#38a169')      # 그린
        BRAND_WARNING = colors.HexColor('#d69e2e')      # 옐로우
        BRAND_LIGHT = colors.HexColor('#f7fafc')        # 라이트 그레이
        BRAND_BORDER = colors.HexColor('#e2e8f0')       # 보더 컬러

        # 한글 폰트 등록
        font_name = 'Helvetica'
        font_name_bold = 'Helvetica-Bold'
        try:
            font_path = "C:/Windows/Fonts/malgun.ttf"
            font_path_bold = "C:/Windows/Fonts/malgunbd.ttf"
            if os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont('MalgunGothic', font_path))
                font_name = 'MalgunGothic'
                if os.path.exists(font_path_bold):
                    pdfmetrics.registerFont(TTFont('MalgunGothicBold', font_path_bold))
                    font_name_bold = 'MalgunGothicBold'
                else:
                    font_name_bold = font_name
            else:
                logger.warning("한글 폰트를 찾을 수 없습니다.")
        except Exception as e:
            logger.warning(f"폰트 등록 오류: {e}")

        # 버퍼 생성
        buffer = io.BytesIO()

        # 집계 데이터 먼저 계산
        aggregated = self._aggregate_results(results)
        grouped = self._group_by_platform(results)
        platform_stats = self._calculate_platform_stats(grouped)

        # 페이지 헤더/푸터 함수
        page_number = [0]  # 페이지 번호 추적용

        def add_page_header_footer(canvas, doc):
            """페이지 헤더와 푸터 추가"""
            page_number[0] += 1
            canvas.saveState()

            # 첫 페이지(표지)는 헤더/푸터 없음
            if page_number[0] == 1:
                canvas.restoreState()
                return

            page_width, page_height = A4

            # 헤더 - 상단 라인과 캠페인명
            canvas.setStrokeColor(BRAND_PRIMARY)
            canvas.setLineWidth(2)
            canvas.line(15*mm, page_height - 12*mm, page_width - 15*mm, page_height - 12*mm)

            canvas.setFont(font_name, 8)
            canvas.setFillColor(BRAND_SECONDARY)
            canvas.drawString(15*mm, page_height - 10*mm, f"{campaign_name} - 캠페인 성과 리포트")

            # 푸터 - 하단 라인, 페이지 번호, 생성일
            canvas.setStrokeColor(BRAND_BORDER)
            canvas.setLineWidth(0.5)
            canvas.line(15*mm, 15*mm, page_width - 15*mm, 15*mm)

            canvas.setFont(font_name, 8)
            canvas.setFillColor(BRAND_SECONDARY)
            # 왼쪽: 생성일
            canvas.drawString(15*mm, 10*mm, f"생성일: {datetime.now().strftime('%Y-%m-%d')}")
            # 가운데: 페이지 번호
            page_text = f"- {page_number[0] - 1} -"  # 표지 제외한 페이지 번호
            canvas.drawCentredString(page_width / 2, 10*mm, page_text)
            # 오른쪽: 기밀 표시
            canvas.drawRightString(page_width - 15*mm, 10*mm, "Confidential")

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

        # 컨텐츠 빌드
        story = []

        # ============================================================
        # 1. 표지 페이지
        # ============================================================
        story.append(Spacer(1, 30*mm))

        # 로고 영역 (플레이스홀더 박스)
        logo_table = Table(
            [[Paragraph("LOGO", styles['CoverInfo'])]],
            colWidths=[50*mm],
            rowHeights=[20*mm]
        )
        logo_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, BRAND_BORDER),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), BRAND_LIGHT),
        ]))
        logo_wrapper = Table([[logo_table]], colWidths=[180*mm])
        logo_wrapper.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        story.append(logo_wrapper)

        story.append(Spacer(1, 20*mm))

        # 상단 장식선
        story.append(HRFlowable(
            width="80%",
            thickness=3,
            color=BRAND_PRIMARY,
            spaceBefore=5*mm,
            spaceAfter=10*mm,
            hAlign='CENTER'
        ))

        # 리포트 타이틀
        story.append(Paragraph("캠페인 성과 리포트", styles['CoverTitle']))
        story.append(Paragraph("Campaign Performance Report", styles['CoverInfo']))

        story.append(Spacer(1, 10*mm))

        # 캠페인명
        story.append(Paragraph(f'"{campaign_name}"', styles['CoverSubtitle']))

        # 하단 장식선
        story.append(HRFlowable(
            width="80%",
            thickness=3,
            color=BRAND_PRIMARY,
            spaceBefore=10*mm,
            spaceAfter=15*mm,
            hAlign='CENTER'
        ))

        # 표지 정보 테이블
        cover_info_data = [
            ["광고주", advertiser_name],
            ["분석 기간", f"{start_date} ~ {end_date}"],
            ["리포트 생성일", datetime.now().strftime('%Y년 %m월 %d일')],
            ["총 분석 게시물", f"{aggregated['total_posts']}개"],
        ]
        cover_info_table = Table(cover_info_data, colWidths=[45*mm, 80*mm])
        cover_info_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (0, -1), BRAND_SECONDARY),
            ('TEXTCOLOR', (1, 0), (1, -1), BRAND_PRIMARY),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('RIGHTPADDING', (0, 0), (0, -1), 10),
            ('LEFTPADDING', (1, 0), (1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
        ]))
        cover_wrapper = Table([[cover_info_table]], colWidths=[180*mm])
        cover_wrapper.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        story.append(cover_wrapper)

        story.append(Spacer(1, 30*mm))

        # 기밀 표시
        story.append(Paragraph(
            "본 리포트는 광고주를 위해 작성된 기밀 문서입니다.",
            styles['Caption']
        ))

        story.append(PageBreak())

        # ============================================================
        # 2. 목차 페이지
        # ============================================================
        story.append(Paragraph("목 차", styles['SectionHeading']))
        story.append(HRFlowable(
            width="100%",
            thickness=1,
            color=BRAND_BORDER,
            spaceAfter=5*mm
        ))

        toc_items = [
            ("1. Executive Summary", "핵심 성과 요약"),
            ("2. 캠페인 성과 개요", "주요 지표 및 통계"),
            ("3. 플랫폼별 성과 분석", "채널별 상세 분석"),
            ("4. 개별 게시물 상세", "게시물 리스트"),
            ("5. 결론 및 제언", "인사이트 요약"),
        ]

        for section, desc in toc_items:
            toc_entry = Table(
                [[Paragraph(section, styles['TOCEntry']),
                  Paragraph(desc, styles['Caption'])]],
                colWidths=[80*mm, 80*mm]
            )
            toc_entry.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(toc_entry)
            story.append(HRFlowable(
                width="100%",
                thickness=0.3,
                color=BRAND_BORDER,
                spaceAfter=2*mm
            ))

        story.append(PageBreak())

        # ============================================================
        # 3. Executive Summary
        # ============================================================
        story.append(Paragraph("1. Executive Summary", styles['SectionHeading']))
        story.append(HRFlowable(
            width="100%",
            thickness=2,
            color=BRAND_PRIMARY,
            spaceAfter=8*mm
        ))

        # 핵심 인사이트 문구 생성
        total_posts = aggregated['total_posts']
        total_engagement = aggregated['total_engagement']
        total_views = aggregated['total_views']
        avg_engagement = aggregated['avg_engagement']
        platform_count = len(platform_stats)

        # 최고 성과 플랫폼 찾기
        best_platform = None
        best_engagement = 0
        for platform, stats in platform_stats.items():
            if stats['engagement'] > best_engagement:
                best_engagement = stats['engagement']
                best_platform = platform

        best_platform_name = PLATFORM_NAMES_KR.get(best_platform, best_platform) if best_platform else "-"

        # 인사이트 박스
        insight_text = f"""
        <b>핵심 인사이트</b><br/><br/>
        본 캠페인은 총 <b>{total_posts}개</b>의 게시물을 통해 <b>{format_number(total_engagement)}건</b>의
        인게이지먼트를 달성하였습니다. {platform_count}개 플랫폼에서 진행되었으며,
        게시물당 평균 <b>{format_number(int(avg_engagement))}건</b>의 반응을 이끌어냈습니다.
        {f'특히 <b>{best_platform_name}</b> 채널에서 가장 높은 성과를 기록하였습니다.' if best_platform else ''}
        """

        insight_table = Table(
            [[Paragraph(insight_text, styles['BodyText'])]],
            colWidths=[165*mm]
        )
        insight_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ebf8ff')),
            ('BOX', (0, 0), (-1, -1), 2, BRAND_ACCENT),
            ('LEFTPADDING', (0, 0), (-1, -1), 15),
            ('RIGHTPADDING', (0, 0), (-1, -1), 15),
            ('TOPPADDING', (0, 0), (-1, -1), 15),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
        ]))
        story.append(insight_table)
        story.append(Spacer(1, 8*mm))

        # 핵심 지표 카드 (4개)
        metric_cards_data = [
            [
                self._create_metric_card("총 게시물", f"{total_posts}개", BRAND_PRIMARY, font_name, font_name_bold),
                self._create_metric_card("총 조회수", format_number(total_views), BRAND_ACCENT, font_name, font_name_bold),
                self._create_metric_card("총 인게이지먼트", format_number(total_engagement), BRAND_SUCCESS, font_name, font_name_bold),
                self._create_metric_card("평균 반응", format_number(int(avg_engagement)), BRAND_WARNING, font_name, font_name_bold),
            ]
        ]

        metric_cards_table = Table(metric_cards_data, colWidths=[42*mm, 42*mm, 42*mm, 42*mm])
        metric_cards_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(metric_cards_table)

        story.append(PageBreak())

        # ============================================================
        # 4. 캠페인 성과 개요
        # ============================================================
        story.append(Paragraph("2. 캠페인 성과 개요", styles['SectionHeading']))
        story.append(HRFlowable(
            width="100%",
            thickness=2,
            color=BRAND_PRIMARY,
            spaceAfter=8*mm
        ))

        story.append(Paragraph("2.1 주요 성과 지표", styles['SubHeading']))

        # 성과 요약 테이블 (개선된 디자인)
        summary_data = [
            ["구분", "지표명", "값", "비고"],
            ["수집", "총 게시물 수", str(aggregated["total_posts"]), f"성공 {aggregated['success_count']}건"],
            ["도달", "총 조회수", format_number(aggregated["total_views"]), "전체 플랫폼 합계"],
            ["반응", "좋아요", format_number(aggregated["total_likes"]), ""],
            ["반응", "댓글", format_number(aggregated["total_comments"]), ""],
            ["반응", "공유", format_number(aggregated["total_shares"]), ""],
            ["반응", "저장/즐겨찾기", format_number(aggregated["total_favorites"]), ""],
            ["종합", "총 인게이지먼트", format_number(aggregated["total_engagement"]), "좋아요+댓글+공유+저장"],
            ["효율", "게시물당 평균 반응", format_number(int(aggregated["avg_engagement"])), ""],
        ]

        summary_table = Table(summary_data, colWidths=[25*mm, 50*mm, 40*mm, 50*mm])
        summary_table.setStyle(TableStyle([
            # 헤더 스타일
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), font_name_bold),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),

            # 본문 스타일
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),
            ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
            ('ALIGN', (3, 1), (3, -1), 'LEFT'),

            # 구분 컬럼 배경색
            ('BACKGROUND', (0, 1), (0, 2), colors.HexColor('#e6f2ff')),  # 수집/도달 - 파랑
            ('BACKGROUND', (0, 3), (0, 6), colors.HexColor('#e6ffe6')),  # 반응 - 초록
            ('BACKGROUND', (0, 7), (0, 7), colors.HexColor('#fff2e6')),  # 종합 - 주황
            ('BACKGROUND', (0, 8), (0, 8), colors.HexColor('#f0e6ff')),  # 효율 - 보라

            # 그리드
            ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ('LINEBELOW', (0, 0), (-1, 0), 2, BRAND_PRIMARY),

            # 행 배경색
            ('ROWBACKGROUNDS', (1, 1), (-1, -1), [colors.white, BRAND_LIGHT]),

            # 패딩
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),

            # 종합 행 강조
            ('BACKGROUND', (1, 7), (-1, 7), colors.HexColor('#fff8e6')),
            ('FONTNAME', (1, 7), (-1, 7), font_name_bold),
        ]))
        story.append(summary_table)

        # 수집 결과 요약
        if aggregated['error_count'] > 0:
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(
                f"* {aggregated['error_count']}건의 게시물은 데이터 수집에 실패하였습니다.",
                styles['Caption']
            ))

        story.append(PageBreak())

        # ============================================================
        # 5. 플랫폼별 성과 분석
        # ============================================================
        story.append(Paragraph("3. 플랫폼별 성과 분석", styles['SectionHeading']))
        story.append(HRFlowable(
            width="100%",
            thickness=2,
            color=BRAND_PRIMARY,
            spaceAfter=8*mm
        ))

        story.append(Paragraph("3.1 플랫폼별 상세 성과", styles['SubHeading']))

        # 플랫폼 컬러 맵
        platform_colors = {
            'youtube': colors.HexColor('#FF0000'),
            'instagram': colors.HexColor('#E4405F'),
            'facebook': colors.HexColor('#1877F2'),
            'xiaohongshu': colors.HexColor('#FF2442'),
            'dcard': colors.HexColor('#006aa6'),
        }

        # 플랫폼별 카드 형식
        for platform, stats in platform_stats.items():
            platform_name = PLATFORM_NAMES_KR.get(platform, platform)
            platform_color = platform_colors.get(platform, BRAND_ACCENT)

            # 플랫폼 헤더
            platform_header = Table(
                [[Paragraph(f"<b>{platform_name}</b>", styles['BodyText'])]],
                colWidths=[165*mm]
            )
            platform_header.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), platform_color),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(platform_header)

            # 플랫폼 상세 데이터
            platform_detail_data = [
                ["게시물 수", "조회수", "좋아요", "댓글", "공유", "저장", "총 인게이지먼트"],
                [
                    str(stats["count"]),
                    format_number(stats["views"]),
                    format_number(stats["likes"]),
                    format_number(stats["comments"]),
                    format_number(stats["shares"]),
                    format_number(stats["favorites"]),
                    format_number(stats["engagement"]),
                ],
            ]

            platform_detail_table = Table(
                platform_detail_data,
                colWidths=[23*mm, 24*mm, 24*mm, 24*mm, 24*mm, 24*mm, 30*mm]
            )
            platform_detail_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('FONTSIZE', (0, 1), (-1, 1), 10),
                ('FONTNAME', (0, 1), (-1, 1), font_name_bold),
                ('TEXTCOLOR', (0, 0), (-1, 0), BRAND_SECONDARY),
                ('TEXTCOLOR', (0, 1), (-1, 1), BRAND_PRIMARY),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('BACKGROUND', (0, 0), (-1, -1), BRAND_LIGHT),
                ('BOX', (0, 0), (-1, -1), 1, BRAND_BORDER),
                ('LINEBELOW', (0, 0), (-1, 0), 0.5, BRAND_BORDER),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                # 총 인게이지먼트 강조
                ('BACKGROUND', (-1, 0), (-1, -1), colors.HexColor('#ebf8ff')),
            ]))
            story.append(platform_detail_table)
            story.append(Spacer(1, 5*mm))

        # 플랫폼 비교 요약
        story.append(Paragraph("3.2 플랫폼 성과 비교", styles['SubHeading']))

        platform_compare_data = [["플랫폼", "게시물", "조회수", "좋아요", "댓글", "공유", "인게이지먼트"]]

        # 가장 높은 인게이지먼트 플랫폼 찾기
        max_engagement_platform = max(platform_stats.items(), key=lambda x: x[1]['engagement'])[0] if platform_stats else None

        for platform, stats in platform_stats.items():
            platform_compare_data.append([
                PLATFORM_NAMES_KR.get(platform, platform),
                str(stats["count"]),
                format_number(stats["views"]),
                format_number(stats["likes"]),
                format_number(stats["comments"]),
                format_number(stats["shares"]),
                format_number(stats["engagement"]),
            ])

        platform_compare_table = Table(
            platform_compare_data,
            colWidths=[30*mm, 20*mm, 25*mm, 25*mm, 25*mm, 22*mm, 28*mm]
        )

        # 테이블 스타일
        table_style_commands = [
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), font_name_bold),
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]

        # 최고 성과 플랫폼 행 강조
        if max_engagement_platform:
            for i, (platform, _) in enumerate(platform_stats.items(), 1):
                if platform == max_engagement_platform:
                    table_style_commands.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#e6ffe6')))
                    table_style_commands.append(('FONTNAME', (0, i), (-1, i), font_name_bold))

        platform_compare_table.setStyle(TableStyle(table_style_commands))
        story.append(platform_compare_table)

        if max_engagement_platform:
            story.append(Spacer(1, 2*mm))
            story.append(Paragraph(
                f"* {PLATFORM_NAMES_KR.get(max_engagement_platform, max_engagement_platform)} 플랫폼이 가장 높은 인게이지먼트를 기록하였습니다.",
                styles['Caption']
            ))

        # 성공한 결과가 충분히 있을 때만 PageBreak
        success_results = [r for r in results if not r.get("error")]
        if len(success_results) >= 3:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 10*mm))

        # ============================================================
        # 6. 개별 게시물 상세
        # ============================================================
        story.append(Paragraph("4. 개별 게시물 상세", styles['SectionHeading']))
        story.append(HRFlowable(
            width="100%",
            thickness=2,
            color=BRAND_PRIMARY,
            spaceAfter=5*mm
        ))

        # 성공한 결과가 없으면 메시지 표시
        if len(success_results) == 0:
            story.append(Paragraph(
                "⚠️ 수집에 성공한 게시물이 없습니다.",
                styles['BodyText']
            ))
            story.append(Spacer(1, 5*mm))
            story.append(Paragraph(
                "가능한 원인:\n"
                "• 플랫폼 로그인 쿠키가 만료됨\n"
                "• 네트워크 차단 (VPN 필요)\n"
                "• 게시물이 비공개로 전환됨",
                styles['Caption']
            ))
        else:
            story.append(Paragraph(
                f"총 {len(results)}개 게시물 중 {len(success_results)}개 성공 (상위 50개 표시)",
                styles['Caption']
            ))
        story.append(Spacer(1, 3*mm))

        # 게시물 테이블 (개선된 디자인)
        posts_data = [["No.", "플랫폼", "작성자", "조회수", "좋아요", "댓글", "공유"]]

        for i, result in enumerate(results[:50], 1):
            platform = result.get("platform", "unknown")
            author = (result.get("author") or "-")[:12]

            # 에러가 있는 경우 표시
            if result.get("error"):
                author = f"{author} (오류)"

            posts_data.append([
                str(i),
                PLATFORM_NAMES_KR.get(platform, platform),
                author,
                format_number(safe_int(result.get("views"))),
                format_number(safe_int(result.get("likes"))),
                format_number(safe_int(result.get("comments"))),
                format_number(safe_int(result.get("shares"))),
            ])

        posts_table = Table(
            posts_data,
            colWidths=[12*mm, 25*mm, 45*mm, 25*mm, 22*mm, 20*mm, 20*mm]
        )
        posts_table.setStyle(TableStyle([
            # 헤더
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), font_name_bold),
            ('FONTSIZE', (0, 0), (-1, 0), 8),

            # 본문
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 1), (-1, -1), 8),

            # 정렬
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),

            # 그리드
            ('GRID', (0, 0), (-1, -1), 0.3, BRAND_BORDER),
            ('LINEBELOW', (0, 0), (-1, 0), 1.5, BRAND_PRIMARY),

            # 행 배경색
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),

            # 패딩
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(posts_table)

        if len(results) > 50:
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(
                f"* 지면 관계상 총 {len(results)}개 중 50개만 표시됩니다. 전체 데이터는 별도 파일로 제공됩니다.",
                styles['Caption']
            ))

        # 게시물이 많을 때만 PageBreak
        if len(results) > 10:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 10*mm))

        # ============================================================
        # 7. 결론 및 제언
        # ============================================================
        story.append(Paragraph("5. 결론 및 제언", styles['SectionHeading']))
        story.append(HRFlowable(
            width="100%",
            thickness=2,
            color=BRAND_PRIMARY,
            spaceAfter=8*mm
        ))

        story.append(Paragraph("5.1 캠페인 성과 요약", styles['SubHeading']))

        # 결론 텍스트 생성
        conclusion_text = f"""
        본 캠페인은 {start_date}부터 {end_date}까지 진행되었으며,
        총 <b>{total_posts}개</b>의 게시물을 통해 <b>{format_number(total_views)}</b>의 조회수와
        <b>{format_number(total_engagement)}</b>의 총 인게이지먼트를 달성하였습니다.
        """

        if best_platform:
            conclusion_text += f"""
            <br/><br/>
            채널별로는 <b>{best_platform_name}</b>이(가) 가장 높은 성과를 기록하였으며,
            해당 플랫폼의 특성을 고려한 콘텐츠 전략이 효과적이었던 것으로 분석됩니다.
            """

        story.append(Paragraph(conclusion_text, styles['BodyText']))
        story.append(Spacer(1, 8*mm))

        story.append(Paragraph("5.2 주요 인사이트", styles['SubHeading']))

        # 인사이트 리스트
        insights = []
        if total_views > 0:
            views_per_post = total_views / total_posts if total_posts > 0 else 0
            insights.append(f"게시물당 평균 조회수: {format_number(int(views_per_post))}")

        if total_engagement > 0:
            engagement_rate = (total_engagement / total_views * 100) if total_views > 0 else 0
            if engagement_rate > 0:
                insights.append(f"평균 인게이지먼트율: {engagement_rate:.2f}%")

        if best_platform:
            best_stats = platform_stats.get(best_platform, {})
            best_posts = best_stats.get('count', 0)
            insights.append(f"최고 성과 플랫폼: {best_platform_name} ({best_posts}개 게시물)")

        if platform_count > 1:
            insights.append(f"{platform_count}개 플랫폼에서 동시 캠페인 진행")

        for insight in insights:
            insight_item = Table(
                [[Paragraph(f"  {insight}", styles['BodyText'])]],
                colWidths=[165*mm]
            )
            insight_item.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (-1, -1), 15),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(insight_item)

        story.append(Spacer(1, 10*mm))

        # 마무리 박스
        closing_text = """
        <b>감사합니다</b><br/><br/>
        본 리포트에 대한 문의사항이 있으시면 언제든 연락 주시기 바랍니다.<br/>
        더 나은 캠페인 성과를 위해 최선을 다하겠습니다.
        """
        closing_table = Table(
            [[Paragraph(closing_text, styles['BodyText'])]],
            colWidths=[165*mm]
        )
        closing_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BRAND_LIGHT),
            ('BOX', (0, 0), (-1, -1), 1, BRAND_BORDER),
            ('LEFTPADDING', (0, 0), (-1, -1), 15),
            ('RIGHTPADDING', (0, 0), (-1, -1), 15),
            ('TOPPADDING', (0, 0), (-1, -1), 15),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ]))
        story.append(closing_table)

        # ============================================================
        # PDF 빌드
        # ============================================================
        doc.build(story, onFirstPage=add_page_header_footer, onLaterPages=add_page_header_footer)

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
