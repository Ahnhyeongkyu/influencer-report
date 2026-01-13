"""
플랫폼 인증 설정 모듈

각 플랫폼별 쿠키 입력 및 관리
- Instagram: sessionid, csrftoken
- Facebook: c_user, xs
- Xiaohongshu: web_session
- Dcard: _dcard_sess
"""

import json
import streamlit as st
from typing import Dict, Optional
from pathlib import Path


# 플랫폼별 필수 쿠키 정보
PLATFORM_COOKIES = {
    "instagram": {
        "display_name": "인스타그램",
        "required_cookies": ["sessionid"],
        "optional_cookies": ["csrftoken", "ds_user_id"],
        "help_text": """
**인스타그램 쿠키 추출 방법:**
1. 브라우저에서 Instagram에 로그인
2. F12 (개발자 도구) → Application → Cookies
3. `sessionid` 값을 복사하여 입력
""",
    },
    "facebook": {
        "display_name": "페이스북",
        "required_cookies": ["c_user", "xs"],
        "optional_cookies": ["fr"],
        "help_text": """
**페이스북 쿠키 추출 방법:**
1. 브라우저에서 Facebook에 로그인
2. F12 (개발자 도구) → Application → Cookies
3. `c_user`와 `xs` 값을 복사하여 입력
""",
    },
    "xiaohongshu": {
        "display_name": "샤오홍슈 (RED)",
        "required_cookies": ["web_session"],
        "optional_cookies": ["xsecappid", "a1"],
        "help_text": """
**샤오홍슈 쿠키 추출 방법:**
1. 브라우저에서 xiaohongshu.com에 로그인 (QR 코드)
2. F12 (개발자 도구) → Application → Cookies
3. `web_session` 값을 복사하여 입력
""",
    },
    "dcard": {
        "display_name": "Dcard",
        "required_cookies": [],  # Dcard는 공개 API 사용
        "optional_cookies": ["_dcard_sess"],
        "help_text": """
**Dcard 쿠키 (선택):**
Dcard는 공개 게시물에 대해 쿠키 없이도 동작합니다.
비공개 게시물 접근 시에만 쿠키가 필요합니다.
""",
    },
}


def init_platform_auth_state():
    """플랫폼 인증 상태 초기화"""
    if "platform_cookies" not in st.session_state:
        st.session_state.platform_cookies = {
            "instagram": {},
            "facebook": {},
            "xiaohongshu": {},
            "dcard": {},
        }

    if "auth_expanded" not in st.session_state:
        st.session_state.auth_expanded = False


def get_platform_cookies(platform: str) -> Dict[str, str]:
    """
    특정 플랫폼의 저장된 쿠키 가져오기

    Args:
        platform: 플랫폼 이름 (instagram, facebook, etc.)

    Returns:
        쿠키 딕셔너리
    """
    init_platform_auth_state()
    return st.session_state.platform_cookies.get(platform, {})


def set_platform_cookies(platform: str, cookies: Dict[str, str]):
    """
    특정 플랫폼의 쿠키 저장

    Args:
        platform: 플랫폼 이름
        cookies: 쿠키 딕셔너리
    """
    init_platform_auth_state()
    st.session_state.platform_cookies[platform] = cookies


def is_platform_authenticated(platform: str) -> bool:
    """
    특정 플랫폼이 인증되었는지 확인

    Args:
        platform: 플랫폼 이름

    Returns:
        인증 여부
    """
    cookies = get_platform_cookies(platform)
    config = PLATFORM_COOKIES.get(platform, {})
    required = config.get("required_cookies", [])

    if not required:
        return True  # 필수 쿠키가 없으면 항상 인증됨

    return all(cookies.get(c) for c in required)


def render_platform_auth_section():
    """플랫폼 인증 설정 UI 렌더링"""
    init_platform_auth_state()

    with st.expander("🔐 플랫폼 인증 설정", expanded=st.session_state.auth_expanded):
        st.markdown("""
        **소셜 미디어 플랫폼 인증**

        Instagram, Facebook, 샤오홍슈 등은 로그인이 필요한 데이터를 수집하려면
        쿠키를 입력해야 합니다. YouTube는 인증 없이도 동작합니다.
        """)

        # 탭으로 플랫폼 구분
        tabs = st.tabs([
            PLATFORM_COOKIES["instagram"]["display_name"],
            PLATFORM_COOKIES["facebook"]["display_name"],
            PLATFORM_COOKIES["xiaohongshu"]["display_name"],
            PLATFORM_COOKIES["dcard"]["display_name"],
        ])

        platforms = ["instagram", "facebook", "xiaohongshu", "dcard"]

        for tab, platform in zip(tabs, platforms):
            with tab:
                render_platform_cookie_input(platform)


def render_platform_cookie_input(platform: str):
    """
    개별 플랫폼 쿠키 입력 UI

    Args:
        platform: 플랫폼 이름
    """
    config = PLATFORM_COOKIES.get(platform, {})
    current_cookies = get_platform_cookies(platform)

    # 도움말 표시
    st.markdown(config.get("help_text", ""))

    # 인증 상태 표시
    is_auth = is_platform_authenticated(platform)
    if is_auth and current_cookies:
        st.success("✅ 인증됨")
    elif config.get("required_cookies"):
        st.warning("⚠️ 인증 필요")
    else:
        st.info("ℹ️ 인증 선택사항")

    # 쿠키 입력 필드
    new_cookies = {}

    # 필수 쿠키
    required = config.get("required_cookies", [])
    if required:
        st.markdown("**필수 쿠키:**")
        for cookie_name in required:
            value = st.text_input(
                cookie_name,
                value=current_cookies.get(cookie_name, ""),
                type="password",
                key=f"{platform}_{cookie_name}",
                help=f"{cookie_name} 쿠키 값을 입력하세요",
            )
            if value:
                new_cookies[cookie_name] = value

    # 선택 쿠키
    optional = config.get("optional_cookies", [])
    if optional:
        with st.expander("선택 쿠키 (추가 설정)"):
            for cookie_name in optional:
                value = st.text_input(
                    cookie_name,
                    value=current_cookies.get(cookie_name, ""),
                    type="password",
                    key=f"{platform}_{cookie_name}_opt",
                )
                if value:
                    new_cookies[cookie_name] = value

    # 저장 버튼
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("저장", key=f"save_{platform}", use_container_width=True):
            set_platform_cookies(platform, new_cookies)
            st.success("쿠키가 저장되었습니다!")
            st.rerun()

    with col2:
        if st.button("초기화", key=f"clear_{platform}", use_container_width=True):
            set_platform_cookies(platform, {})
            st.info("쿠키가 초기화되었습니다.")
            st.rerun()


def get_all_platform_auth_status() -> Dict[str, bool]:
    """
    모든 플랫폼의 인증 상태 가져오기

    Returns:
        {platform: is_authenticated}
    """
    return {
        platform: is_platform_authenticated(platform)
        for platform in PLATFORM_COOKIES.keys()
    }


def format_cookies_for_requests(platform: str) -> Dict[str, str]:
    """
    requests 라이브러리용 쿠키 포맷으로 변환

    Args:
        platform: 플랫폼 이름

    Returns:
        requests 호환 쿠키 딕셔너리
    """
    return get_platform_cookies(platform)


def format_cookies_for_selenium(platform: str) -> list:
    """
    Selenium용 쿠키 포맷으로 변환

    Args:
        platform: 플랫폼 이름

    Returns:
        Selenium 호환 쿠키 리스트
    """
    cookies = get_platform_cookies(platform)

    domains = {
        "instagram": ".instagram.com",
        "facebook": ".facebook.com",
        "xiaohongshu": ".xiaohongshu.com",
        "dcard": ".dcard.tw",
    }

    domain = domains.get(platform, "")

    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
        }
        for name, value in cookies.items()
        if value
    ]
