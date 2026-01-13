"""
차트 생성 모듈

matplotlib을 사용한 캠페인 성과 시각화 차트 생성
"""

import io
import base64
import logging
from typing import Dict, Any, List, Optional

import matplotlib
matplotlib.use('Agg')  # Non-GUI backend
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

logger = logging.getLogger(__name__)

# 플랫폼별 색상 정의
PLATFORM_COLORS = {
    "xiaohongshu": "#FF2442",  # 샤오홍슈 빨간색
    "youtube": "#FF0000",      # 유튜브 빨간색
    "instagram": "#E4405F",    # 인스타그램 분홍색
    "facebook": "#1877F2",     # 페이스북 파란색
    "dcard": "#006AA6",        # 디카드 파란색
}

# 플랫폼 한글명
PLATFORM_NAMES_KR = {
    "xiaohongshu": "샤오홍슈",
    "youtube": "유튜브",
    "instagram": "인스타그램",
    "facebook": "페이스북",
    "dcard": "디카드",
}


def setup_korean_font():
    """
    한글 폰트 설정

    Windows 기본 폰트를 사용하여 한글 표시
    """
    import warnings
    # matplotlib 폰트 관련 경고 모두 무시
    warnings.filterwarnings('ignore', category=UserWarning)

    # Windows 기본 한글 폰트 목록
    korean_fonts = [
        'Malgun Gothic',      # Windows 기본
        'NanumGothic',        # 나눔고딕
        'NanumBarunGothic',   # 나눔바른고딕
        'Gulim',              # 굴림
        'Dotum',              # 돋움
        'Batang',             # 바탕
    ]

    # 시스템에서 사용 가능한 한글 폰트 찾기
    available_fonts = [f.name for f in fm.fontManager.ttflist]

    for font in korean_fonts:
        if font in available_fonts:
            # 모든 관련 설정에 한글 폰트 적용
            plt.rcParams['font.family'] = font
            plt.rcParams['font.sans-serif'] = [font, 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False  # 마이너스 기호 깨짐 방지
            logger.info(f"한글 폰트 설정: {font}")
            return font

    # 폰트를 찾지 못한 경우
    logger.warning("한글 폰트를 찾지 못했습니다. 기본 폰트 사용.")
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['axes.unicode_minus'] = False
    return None


class ChartGenerator:
    """차트 생성 클래스"""

    def __init__(self):
        """차트 생성기 초기화"""
        self.korean_font = setup_korean_font()

        # 스타일 설정
        plt.style.use('seaborn-v0_8-whitegrid')

        # 차트 색상 팔레트 (전문적인 파란색/회색 계열)
        self.color_palette = [
            '#2E86AB',  # 진한 파란색
            '#A23B72',  # 보라색
            '#F18F01',  # 주황색
            '#C73E1D',  # 빨간색
            '#3B1F2B',  # 어두운 색
            '#95C623',  # 연두색
        ]

    def _fig_to_base64(self, fig: plt.Figure, dpi: int = 150) -> str:
        """
        matplotlib Figure를 base64 문자열로 변환

        Args:
            fig: matplotlib Figure 객체
            dpi: 이미지 해상도

        Returns:
            base64로 인코딩된 PNG 이미지 문자열
        """
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        buf.close()
        plt.close(fig)
        return img_base64

    def create_platform_engagement_bar(
        self,
        platform_data: Dict[str, Dict[str, Any]],
        width: int = 10,
        height: int = 6
    ) -> str:
        """
        플랫폼별 인게이지먼트 비교 막대 차트 생성

        Args:
            platform_data: 플랫폼별 데이터 딕셔너리
            width: 차트 너비 (인치)
            height: 차트 높이 (인치)

        Returns:
            base64로 인코딩된 PNG 이미지
        """
        if not platform_data:
            return ""

        fig, ax = plt.subplots(figsize=(width, height))

        platforms = list(platform_data.keys())
        platform_names = [PLATFORM_NAMES_KR.get(p, p) for p in platforms]

        # 지표별 데이터 준비
        metrics = ['likes', 'comments', 'shares', 'views']
        metric_names = ['좋아요', '댓글', '공유', '조회수']

        x = np.arange(len(platforms))
        width_bar = 0.2

        for i, (metric, name) in enumerate(zip(metrics, metric_names)):
            values = [platform_data.get(p, {}).get(metric, 0) for p in platforms]
            # 0이 아닌 값만 표시
            if sum(values) > 0:
                bars = ax.bar(x + i * width_bar, values, width_bar,
                             label=name, color=self.color_palette[i % len(self.color_palette)])

                # 값 레이블 추가
                for bar, val in zip(bars, values):
                    if val > 0:
                        ax.annotate(self._format_number(val),
                                   xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                                   ha='center', va='bottom', fontsize=8,
                                   rotation=45)

        ax.set_xlabel('플랫폼', fontsize=11)
        ax.set_ylabel('수량', fontsize=11)
        ax.set_title('플랫폼별 인게이지먼트 비교', fontsize=14, fontweight='bold', pad=15)
        ax.set_xticks(x + width_bar * 1.5)
        ax.set_xticklabels(platform_names, fontsize=10)
        ax.legend(loc='upper right', fontsize=9)

        # Y축 포맷팅
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: self._format_number(int(x))))

        # 그리드 스타일
        ax.grid(axis='y', linestyle='--', alpha=0.7)
        ax.set_axisbelow(True)

        plt.tight_layout()
        return self._fig_to_base64(fig)

    def create_engagement_pie(
        self,
        total_likes: int,
        total_comments: int,
        total_shares: int,
        total_favorites: int = 0,
        width: int = 8,
        height: int = 6
    ) -> str:
        """
        인게이지먼트 분포 파이 차트 생성

        Args:
            total_likes: 총 좋아요 수
            total_comments: 총 댓글 수
            total_shares: 총 공유 수
            total_favorites: 총 즐겨찾기 수
            width: 차트 너비 (인치)
            height: 차트 높이 (인치)

        Returns:
            base64로 인코딩된 PNG 이미지
        """
        # 0이 아닌 값만 필터링
        data = {
            '좋아요': total_likes,
            '댓글': total_comments,
            '공유': total_shares,
        }
        if total_favorites > 0:
            data['즐겨찾기'] = total_favorites

        # 값이 모두 0인 경우
        if sum(data.values()) == 0:
            return ""

        # 0인 항목 제거
        data = {k: v for k, v in data.items() if v > 0}

        fig, ax = plt.subplots(figsize=(width, height))

        labels = list(data.keys())
        values = list(data.values())
        colors = self.color_palette[:len(labels)]

        # 가장 큰 값을 약간 분리
        explode = [0.05 if v == max(values) else 0 for v in values]

        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct=lambda pct: f'{pct:.1f}%\n({int(pct/100*sum(values)):,})',
            explode=explode,
            shadow=False,
            startangle=90,
            textprops={'fontsize': 10}
        )

        # autopct 텍스트 스타일
        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_fontweight('bold')

        ax.set_title('인게이지먼트 분포', fontsize=14, fontweight='bold', pad=15)

        # 범례
        ax.legend(wedges, labels, title="유형", loc="center left",
                 bbox_to_anchor=(1, 0, 0.5, 1), fontsize=10)

        plt.tight_layout()
        return self._fig_to_base64(fig)

    def create_platform_posts_pie(
        self,
        platform_counts: Dict[str, int],
        width: int = 8,
        height: int = 6
    ) -> str:
        """
        플랫폼별 게시물 수 파이 차트 생성

        Args:
            platform_counts: 플랫폼별 게시물 수
            width: 차트 너비 (인치)
            height: 차트 높이 (인치)

        Returns:
            base64로 인코딩된 PNG 이미지
        """
        if not platform_counts or sum(platform_counts.values()) == 0:
            return ""

        fig, ax = plt.subplots(figsize=(width, height))

        # 플랫폼명 한글화 및 색상 매칭
        labels = []
        values = []
        colors = []

        for platform, count in platform_counts.items():
            if count > 0:
                labels.append(PLATFORM_NAMES_KR.get(platform, platform))
                values.append(count)
                colors.append(PLATFORM_COLORS.get(platform, '#888888'))

        # 가장 큰 값을 약간 분리
        explode = [0.05 if v == max(values) else 0 for v in values]

        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct=lambda pct: f'{pct:.1f}%\n({int(pct/100*sum(values))}개)',
            explode=explode,
            shadow=False,
            startangle=90,
            textprops={'fontsize': 10}
        )

        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_fontweight('bold')
            autotext.set_color('white')

        ax.set_title('플랫폼별 게시물 분포', fontsize=14, fontweight='bold', pad=15)

        plt.tight_layout()
        return self._fig_to_base64(fig)

    def create_views_bar(
        self,
        platform_data: Dict[str, Dict[str, Any]],
        width: int = 10,
        height: int = 5
    ) -> str:
        """
        플랫폼별 조회수 비교 수평 막대 차트 생성

        Args:
            platform_data: 플랫폼별 데이터 딕셔너리
            width: 차트 너비 (인치)
            height: 차트 높이 (인치)

        Returns:
            base64로 인코딩된 PNG 이미지
        """
        if not platform_data:
            return ""

        # 조회수가 있는 플랫폼만 필터링
        filtered_data = {
            p: d for p, d in platform_data.items()
            if d.get('views', 0) > 0
        }

        if not filtered_data:
            return ""

        fig, ax = plt.subplots(figsize=(width, height))

        platforms = list(filtered_data.keys())
        platform_names = [PLATFORM_NAMES_KR.get(p, p) for p in platforms]
        views = [filtered_data[p].get('views', 0) for p in platforms]
        colors = [PLATFORM_COLORS.get(p, '#888888') for p in platforms]

        # 수평 막대 차트
        bars = ax.barh(platform_names, views, color=colors, height=0.6)

        # 값 레이블 추가
        for bar, val in zip(bars, views):
            ax.annotate(self._format_number(val),
                       xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                       ha='left', va='center', fontsize=10, fontweight='bold',
                       xytext=(5, 0), textcoords='offset points')

        ax.set_xlabel('조회수', fontsize=11)
        ax.set_title('플랫폼별 총 조회수', fontsize=14, fontweight='bold', pad=15)

        # X축 포맷팅
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: self._format_number(int(x))))

        # 그리드
        ax.grid(axis='x', linestyle='--', alpha=0.7)
        ax.set_axisbelow(True)

        # 왼쪽 여백 확보
        ax.margins(x=0.15)

        plt.tight_layout()
        return self._fig_to_base64(fig)

    def create_top_posts_bar(
        self,
        results: List[Dict[str, Any]],
        metric: str = 'likes',
        top_n: int = 10,
        width: int = 10,
        height: int = 6
    ) -> str:
        """
        상위 게시물 막대 차트 생성

        Args:
            results: 크롤링 결과 리스트
            metric: 정렬 기준 지표 (likes, comments, views 등)
            top_n: 상위 몇 개 표시
            width: 차트 너비 (인치)
            height: 차트 높이 (인치)

        Returns:
            base64로 인코딩된 PNG 이미지
        """
        if not results:
            return ""

        # 에러 없는 결과만 필터링하고 정렬
        valid_results = [r for r in results if 'error' not in r]
        sorted_results = sorted(
            valid_results,
            key=lambda x: x.get(metric, 0) or 0,
            reverse=True
        )[:top_n]

        if not sorted_results:
            return ""

        fig, ax = plt.subplots(figsize=(width, height))

        # 레이블 생성 (플랫폼 + 작성자/제목)
        labels = []
        values = []
        colors = []

        for r in sorted_results:
            platform = r.get('platform', 'unknown')
            author = r.get('author', '')[:15] or '알 수 없음'
            label = f"{PLATFORM_NAMES_KR.get(platform, platform)} - {author}"
            labels.append(label)
            values.append(r.get(metric, 0) or 0)
            colors.append(PLATFORM_COLORS.get(platform, '#888888'))

        # 수평 막대 차트
        y_pos = np.arange(len(labels))
        bars = ax.barh(y_pos, values, color=colors, height=0.7)

        # 값 레이블 추가
        for bar, val in zip(bars, values):
            ax.annotate(self._format_number(val),
                       xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                       ha='left', va='center', fontsize=9,
                       xytext=(5, 0), textcoords='offset points')

        metric_names = {
            'likes': '좋아요',
            'comments': '댓글',
            'views': '조회수',
            'shares': '공유'
        }
        metric_name = metric_names.get(metric, metric)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=9)
        ax.set_xlabel(metric_name, fontsize=11)
        ax.set_title(f'상위 {len(sorted_results)}개 게시물 ({metric_name} 기준)',
                    fontsize=14, fontweight='bold', pad=15)

        # X축 포맷팅
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: self._format_number(int(x))))

        # 그리드
        ax.grid(axis='x', linestyle='--', alpha=0.7)
        ax.set_axisbelow(True)
        ax.margins(x=0.15)

        # Y축 역순 (상위가 위로)
        ax.invert_yaxis()

        plt.tight_layout()
        return self._fig_to_base64(fig)

    def _format_number(self, num: int) -> str:
        """
        숫자를 읽기 쉬운 형태로 포맷

        Args:
            num: 포맷할 숫자

        Returns:
            포맷된 문자열
        """
        if num >= 100000000:
            return f"{num / 100000000:.1f}억"
        elif num >= 10000:
            return f"{num / 10000:.1f}만"
        elif num >= 1000:
            return f"{num:,}"
        else:
            return str(num)

    def generate_all_charts(
        self,
        results: List[Dict[str, Any]],
        platform_data: Dict[str, Dict[str, Any]],
        aggregated: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        모든 차트 생성

        Args:
            results: 크롤링 결과 리스트
            platform_data: 플랫폼별 집계 데이터
            aggregated: 전체 집계 데이터

        Returns:
            차트명: base64 이미지 딕셔너리
        """
        charts = {}

        # 1. 플랫폼별 인게이지먼트 비교 차트
        try:
            chart = self.create_platform_engagement_bar(platform_data)
            if chart:
                charts['platform_engagement'] = chart
        except Exception as e:
            logger.error(f"플랫폼 인게이지먼트 차트 생성 오류: {e}")

        # 2. 인게이지먼트 분포 파이 차트
        try:
            chart = self.create_engagement_pie(
                aggregated.get('total_likes', 0),
                aggregated.get('total_comments', 0),
                aggregated.get('total_shares', 0),
                aggregated.get('total_favorites', 0)
            )
            if chart:
                charts['engagement_pie'] = chart
        except Exception as e:
            logger.error(f"인게이지먼트 파이 차트 생성 오류: {e}")

        # 3. 플랫폼별 게시물 분포 파이 차트
        try:
            platform_counts = {p: d.get('count', 0) for p, d in platform_data.items()}
            chart = self.create_platform_posts_pie(platform_counts)
            if chart:
                charts['platform_posts'] = chart
        except Exception as e:
            logger.error(f"플랫폼 게시물 파이 차트 생성 오류: {e}")

        # 4. 조회수 비교 차트
        try:
            chart = self.create_views_bar(platform_data)
            if chart:
                charts['views_bar'] = chart
        except Exception as e:
            logger.error(f"조회수 차트 생성 오류: {e}")

        # 5. 상위 게시물 차트 (좋아요 기준)
        try:
            chart = self.create_top_posts_bar(results, metric='likes', top_n=10)
            if chart:
                charts['top_posts_likes'] = chart
        except Exception as e:
            logger.error(f"상위 게시물 차트 생성 오류: {e}")

        return charts
