"""
인증 모듈

Streamlit 앱을 위한 간단한 세션 기반 인증
"""

import os
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

import streamlit as st

logger = logging.getLogger(__name__)

# 환경 변수에서 인증 정보 로드 (하드코딩 금지)
_REQUIRED_ENV_VARS = ("APP_USERNAME", "APP_PASSWORD")


def get_credentials() -> Tuple[str, str]:
    """
    환경 변수에서 인증 정보 로드

    Returns:
        (username, password)

    Raises:
        RuntimeError: 환경 변수 미설정 시
    """
    username = os.getenv("APP_USERNAME")
    password = os.getenv("APP_PASSWORD")
    if not username or not password:
        missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
        logger.error(f"필수 환경 변수 미설정: {missing}")
        raise RuntimeError(
            f"인증에 필요한 환경 변수가 설정되지 않았습니다: {', '.join(missing)}. "
            "APP_USERNAME, APP_PASSWORD 환경 변수를 설정하세요."
        )
    return username, password


def hash_password(password: str) -> str:
    """
    비밀번호 해시 생성

    Args:
        password: 원본 비밀번호

    Returns:
        해시된 비밀번호
    """
    return hashlib.sha256(password.encode()).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """
    사용자 인증 확인 (해시 비교)

    Args:
        username: 입력된 사용자명
        password: 입력된 비밀번호

    Returns:
        인증 성공 여부
    """
    import hmac
    valid_username, valid_password = get_credentials()

    # 타이밍 공격 방지를 위해 hmac.compare_digest 사용
    username_match = hmac.compare_digest(username.encode(), valid_username.encode())
    password_match = hmac.compare_digest(password.encode(), valid_password.encode())

    return username_match and password_match


def _check_rate_limit() -> bool:
    """로그인 시도 횟수 제한 확인. 5회 실패 시 60초 잠금."""
    now = datetime.now()
    attempts = st.session_state.get("_login_attempts", [])
    # 60초 이내 시도만 유지
    attempts = [t for t in attempts if now - t < timedelta(seconds=60)]
    st.session_state._login_attempts = attempts
    return len(attempts) < 5


def _record_failed_attempt():
    """실패한 로그인 시도 기록."""
    if "_login_attempts" not in st.session_state:
        st.session_state._login_attempts = []
    st.session_state._login_attempts.append(datetime.now())


def init_session_state():
    """
    세션 상태 초기화
    """
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if "username" not in st.session_state:
        st.session_state.username = None

    if "login_time" not in st.session_state:
        st.session_state.login_time = None


def check_session_timeout(timeout_minutes: int = 60) -> bool:
    """
    세션 만료 확인

    Args:
        timeout_minutes: 세션 타임아웃 (분)

    Returns:
        세션 유효 여부
    """
    if not st.session_state.get("login_time"):
        return False

    login_time = st.session_state.login_time
    if datetime.now() - login_time > timedelta(minutes=timeout_minutes):
        # 세션 만료
        logout()
        return False

    return True


def login(username: str, password: str) -> bool:
    """
    로그인 처리

    Args:
        username: 사용자명
        password: 비밀번호

    Returns:
        로그인 성공 여부
    """
    if verify_credentials(username, password):
        st.session_state.authenticated = True
        st.session_state.username = username
        st.session_state.login_time = datetime.now()
        logger.info(f"로그인 성공: {username}")
        return True
    else:
        logger.warning(f"로그인 실패: {username}")
        return False


def logout():
    """
    로그아웃 처리
    """
    st.session_state.authenticated = False
    st.session_state.username = None
    st.session_state.login_time = None
    logger.info("로그아웃")


def is_authenticated() -> bool:
    """
    인증 상태 확인

    Returns:
        인증 여부
    """
    init_session_state()

    if not st.session_state.authenticated:
        return False

    # 세션 타임아웃 확인
    if not check_session_timeout():
        return False

    return True


def show_login_form():
    """
    로그인 폼 표시
    """
    st.markdown(
        """
        <style>
        .login-container {
            max-width: 400px;
            margin: 0 auto;
            padding: 2rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown("### 로그인")
        st.markdown("---")

        with st.form("login_form", clear_on_submit=False):
            username = st.text_input(
                "사용자명",
                placeholder="사용자명을 입력하세요",
            )
            password = st.text_input(
                "비밀번호",
                type="password",
                placeholder="비밀번호를 입력하세요",
            )

            submitted = st.form_submit_button(
                "로그인",
                use_container_width=True,
                type="primary",
            )

            if submitted:
                if not _check_rate_limit():
                    st.error("로그인 시도 횟수를 초과했습니다. 잠시 후 다시 시도하세요.")
                elif username and password:
                    if login(username, password):
                        st.session_state._login_attempts = []
                        st.success("로그인 성공!")
                        st.rerun()
                    else:
                        _record_failed_attempt()
                        st.error("사용자명 또는 비밀번호가 올바르지 않습니다.")
                else:
                    st.warning("사용자명과 비밀번호를 입력하세요.")


def show_user_info():
    """
    사용자 정보 및 로그아웃 버튼 표시
    """
    if is_authenticated():
        with st.sidebar:
            st.markdown("---")
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(f"**{st.session_state.username}**님 로그인")
            with col2:
                if st.button("로그아웃", use_container_width=True):
                    logout()
                    st.rerun()


def require_auth(func):
    """
    인증 필요 데코레이터

    사용법:
        @require_auth
        def my_page():
            st.write("인증된 사용자만 볼 수 있습니다")

    Args:
        func: 래핑할 함수

    Returns:
        래핑된 함수
    """
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            show_login_form()
            return None
        return func(*args, **kwargs)
    return wrapper
