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
        ReportLab을 사용한 PDF 생성 (대체 방식)

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
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, Image
        )
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # 한글 폰트 등록
        try:
            # Windows Malgun Gothic 폰트 경로
            font_path = "C:/Windows/Fonts/malgun.ttf"
            if os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont('MalgunGothic', font_path))
                font_name = 'MalgunGothic'
            else:
                font_name = 'Helvetica'
                logger.warning("한글 폰트를 찾을 수 없습니다.")
        except Exception as e:
            font_name = 'Helvetica'
            logger.warning(f"폰트 등록 오류: {e}")

        # 버퍼 생성
        buffer = io.BytesIO()

        # 문서 생성
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=15*mm,
            leftMargin=15*mm,
            topMargin=15*mm,
            bottomMargin=20*mm
        )

        # 스타일 설정
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name='KoreanTitle',
            fontName=font_name,
            fontSize=24,
            leading=30,
            alignment=1,  # Center
            spaceAfter=20
        ))
        styles.add(ParagraphStyle(
            name='KoreanHeading',
            fontName=font_name,
            fontSize=14,
            leading=18,
            spaceBefore=20,
            spaceAfter=10,
            textColor=colors.HexColor('#1a365d')
        ))
        styles.add(ParagraphStyle(
            name='KoreanBody',
            fontName=font_name,
            fontSize=10,
            leading=14
        ))

        # 컨텐츠 빌드
        story = []

        # 집계
        aggregated = self._aggregate_results(results)
        grouped = self._group_by_platform(results)
        platform_stats = self._calculate_platform_stats(grouped)

        # 표지
        story.append(Spacer(1, 80*mm))
        story.append(Paragraph(f"캠페인 성과 리포트", styles['KoreanTitle']))
        story.append(Spacer(1, 10*mm))
        story.append(Paragraph(f"{campaign_name}", styles['KoreanHeading']))
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph(f"광고주: {advertiser_name}", styles['KoreanBody']))
        story.append(Paragraph(f"기간: {start_date} ~ {end_date}", styles['KoreanBody']))
        story.append(Paragraph(f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles['KoreanBody']))
        story.append(PageBreak())

        # 요약 섹션
        story.append(Paragraph("1. 캠페인 성과 요약", styles['KoreanHeading']))

        summary_data = [
            ["지표", "값"],
            ["총 게시물", str(aggregated["total_posts"])],
            ["성공", str(aggregated["success_count"])],
            ["총 조회수", format_number(aggregated["total_views"])],
            ["총 좋아요", format_number(aggregated["total_likes"])],
            ["총 댓글", format_number(aggregated["total_comments"])],
            ["총 공유", format_number(aggregated["total_shares"])],
            ["총 인게이지먼트", format_number(aggregated["total_engagement"])],
        ]

        summary_table = Table(summary_data, colWidths=[80*mm, 60*mm])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 10*mm))

        # 플랫폼별 성과
        story.append(Paragraph("2. 플랫폼별 성과", styles['KoreanHeading']))

        platform_data = [["플랫폼", "게시물", "조회수", "좋아요", "댓글", "공유"]]
        for platform, stats in platform_stats.items():
            platform_data.append([
                PLATFORM_NAMES_KR.get(platform, platform),
                str(stats["count"]),
                format_number(stats["views"]),
                format_number(stats["likes"]),
                format_number(stats["comments"]),
                format_number(stats["shares"]),
            ])

        platform_table = Table(platform_data, colWidths=[35*mm, 25*mm, 30*mm, 30*mm, 25*mm, 25*mm])
        platform_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(platform_table)
        story.append(PageBreak())

        # 개별 게시물 상세
        story.append(Paragraph("3. 개별 게시물 상세", styles['KoreanHeading']))

        posts_data = [["번호", "플랫폼", "작성자", "좋아요", "댓글"]]
        for i, result in enumerate(results[:50], 1):  # 최대 50개만 표시
            platform = result.get("platform", "unknown")
            author = (result.get("author") or "-")[:15]
            posts_data.append([
                str(i),
                PLATFORM_NAMES_KR.get(platform, platform),
                author,
                format_number(safe_int(result.get("likes"))),
                format_number(safe_int(result.get("comments"))),
            ])

        posts_table = Table(posts_data, colWidths=[15*mm, 30*mm, 50*mm, 35*mm, 35*mm])
        posts_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('PADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(posts_table)

        if len(results) > 50:
            story.append(Spacer(1, 5*mm))
            story.append(Paragraph(
                f"* 총 {len(results)}개 중 50개만 표시됩니다.",
                styles['KoreanBody']
            ))

        # PDF 빌드
        doc.build(story)

        # 바이트 데이터 추출
        pdf_bytes = buffer.getvalue()
        buffer.close()

        # 파일로 저장 (선택)
        if output_path:
            with open(output_path, 'wb') as f:
                f.write(pdf_bytes)
            logger.info(f"PDF 저장됨: {output_path}")

        return pdf_bytes


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
