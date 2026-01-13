"""
유틸리티 모듈

URL 파싱, 데이터 처리 등의 유틸리티 함수 제공
"""

from .url_parser import (
    detect_platform,
    parse_urls,
    validate_url,
    normalize_url,
    PLATFORM_PATTERNS,
)

from .data_processor import (
    aggregate_results,
    group_by_platform,
    calculate_campaign_metrics,
    format_number,
    export_to_dataframe,
)

__all__ = [
    # URL Parser
    "detect_platform",
    "parse_urls",
    "validate_url",
    "normalize_url",
    "PLATFORM_PATTERNS",
    # Data Processor
    "aggregate_results",
    "group_by_platform",
    "calculate_campaign_metrics",
    "format_number",
    "export_to_dataframe",
]
