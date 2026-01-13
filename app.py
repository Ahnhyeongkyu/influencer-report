"""
Streamlit Cloud Entry Point
픽스업 인플루언서 캠페인 리포트 자동화 시스템
"""
import sys
from pathlib import Path

# src 폴더를 Python 경로에 추가
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# 메인 앱 임포트 및 실행 (Streamlit은 __name__ 체크 없이 직접 실행)
from app import main
main()
