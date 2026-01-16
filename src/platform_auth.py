"""
플랫폼 인증 설정 모듈

각 플랫폼별 쿠키 입력 및 관리
- Instagram: sessionid, csrftoken
- Facebook: c_user, xs
- Xiaohongshu: web_session
- Dcard: _dcard_sess

v1.2: 브라우저 로그인 기능 추가
- 사용자가 직접 브라우저에서 로그인하면 쿠키 자동 저장
"""

import json
import time
import os
import logging
import streamlit as st
from typing import Dict, Optional
from pathlib import Path

# undetected_chromedriver for browser login
try:
    import undetected_chromedriver as uc
    HAS_UNDETECTED = True
except ImportError:
    HAS_UNDETECTED = False

logger = logging.getLogger(__name__)


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

    with st.expander("🔐 플랫폼 로그인", expanded=st.session_state.auth_expanded):
        st.markdown("""
        **소셜 미디어 플랫폼 로그인**

        Instagram, Facebook은 **"브라우저에서 로그인"** 버튼을 클릭하면 됩니다.
        샤오홍슈, Dcard는 **"인증 모드"**를 사용하세요.
        YouTube는 로그인 없이 동작합니다.
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

    # 파일에서 쿠키 로드 시도
    if not current_cookies:
        file_cookies = load_cookies_from_file(platform)
        if file_cookies:
            set_platform_cookies(platform, file_cookies)
            current_cookies = file_cookies

    # 인증 상태 표시
    is_auth = is_platform_authenticated(platform)
    if is_auth and current_cookies:
        st.success("✅ 로그인됨 - 크롤링 준비 완료")
        # 저장된 쿠키 정보 간략히 표시
        cookie_names = list(current_cookies.keys())[:3]
        st.caption(f"저장된 쿠키: {', '.join(cookie_names)}")
        st.caption("💡 쿠키가 만료된 경우에만 다시 로그인하세요.")
    elif config.get("required_cookies"):
        st.warning("⚠️ 로그인 필요")
    else:
        st.info("ℹ️ 로그인 선택사항")

    # === 브라우저 로그인 버튼 (메인) ===
    # 이미 로그인된 경우 접힌 상태로 표시
    # === 로그인 UI (이미 로그인된 경우 접힌 상태로 표시) ===
    show_login_expanded = not (is_auth and current_cookies)

    if platform in ["instagram", "facebook"]:
        with st.expander("🔐 로그인 설정" if is_auth else "🔐 로그인하기", expanded=show_login_expanded):
            st.markdown("**간편 로그인:**")
            st.caption("버튼을 클릭하면 브라우저 창이 열립니다. 로그인 후 자동으로 저장됩니다.")

            if st.button(
                f"🌐 브라우저에서 {config.get('display_name', platform)} 로그인",
                key=f"browser_login_{platform}",
                use_container_width=True,
                type="primary"
            ):
                if not HAS_UNDETECTED:
                    st.error("❌ undetected_chromedriver가 설치되지 않았습니다. 설치.bat을 다시 실행해주세요.")
                    st.code("pip install undetected-chromedriver", language="bash")
                else:
                    st.info(f"🌐 {config.get('display_name')} 로그인 창을 여는 중...")
                    st.warning("⚠️ Chrome 브라우저 창이 열립니다. 로그인 완료 후 자동으로 저장됩니다. (최대 2분)")

                    try:
                        cookies = browser_login(platform, timeout=120)
                        if cookies:
                            set_platform_cookies(platform, cookies)
                            save_cookies_to_file(platform, cookies)
                            st.success(f"✅ 로그인 성공! 쿠키 {len(cookies)}개 저장됨")
                            st.rerun()
                        else:
                            st.error("❌ 로그인 타임아웃. 2분 내에 로그인을 완료해주세요.")
                    except Exception as e:
                        st.error(f"❌ 브라우저 로그인 오류: {str(e)}")
                        st.info("💡 Chrome 브라우저가 설치되어 있는지 확인해주세요.")

    elif platform == "xiaohongshu":
        if not (is_auth and current_cookies):
            st.markdown("---")
            st.markdown("**QR 코드 로그인:**")
            st.caption("인증 모드를 사용하세요. 수집 시 QR 코드가 표시됩니다.")
            st.info("좌측 '인증 모드' 체크박스를 선택한 후 샤오홍슈 URL을 수집하세요.")

    elif platform == "dcard":
        if not (is_auth and current_cookies):
            st.markdown("---")
            st.markdown("**Cloudflare 인증:**")
            st.caption("인증 모드를 사용하세요. 수집 시 인증 화면이 표시됩니다.")
            st.info("좌측 '인증 모드' 체크박스를 선택한 후 Dcard URL을 수집하세요.")

    # === 수동 쿠키 입력 (고급) ===
    with st.expander("🔧 수동 쿠키 입력 (고급)"):
        st.caption("개발자 도구에서 쿠키를 직접 복사하여 입력할 수 있습니다.")

        new_cookies = dict(current_cookies)  # 기존 쿠키 유지

        # 필수 쿠키
        required = config.get("required_cookies", [])
        if required:
            for cookie_name in required:
                value = st.text_input(
                    cookie_name,
                    value=current_cookies.get(cookie_name, ""),
                    type="password",
                    key=f"{platform}_{cookie_name}",
                    help=f"{cookie_name} 쿠키 값",
                )
                if value:
                    new_cookies[cookie_name] = value
                elif cookie_name in new_cookies:
                    del new_cookies[cookie_name]

        # 선택 쿠키
        optional = config.get("optional_cookies", [])
        if optional:
            for cookie_name in optional:
                value = st.text_input(
                    cookie_name,
                    value=current_cookies.get(cookie_name, ""),
                    type="password",
                    key=f"{platform}_{cookie_name}_opt",
                )
                if value:
                    new_cookies[cookie_name] = value

        # 저장/초기화 버튼
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("저장", key=f"save_{platform}", use_container_width=True):
                set_platform_cookies(platform, new_cookies)
                save_cookies_to_file(platform, new_cookies)
                st.success("쿠키가 저장되었습니다!")
                st.rerun()

        with col2:
            if st.button("초기화", key=f"clear_{platform}", use_container_width=True):
                set_platform_cookies(platform, {})
                # 파일도 삭제 (크롤러와 동일한 파일명)
                cookie_file = Path("data/cookies") / f"{platform}_cookies.json"
                if cookie_file.exists():
                    cookie_file.unlink()
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


# 플랫폼별 로그인 URL
PLATFORM_LOGIN_URLS = {
    "instagram": "https://www.instagram.com/accounts/login/",
    "facebook": "https://www.facebook.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "dcard": "https://www.dcard.tw/",
}

# 플랫폼별 로그인 성공 확인 조건
PLATFORM_LOGIN_SUCCESS = {
    "instagram": lambda url: "instagram.com" in url and "login" not in url.lower(),
    "facebook": lambda url: "facebook.com" in url and "login" not in url.lower() and "checkpoint" not in url.lower(),
    "xiaohongshu": lambda url: "xiaohongshu.com" in url,
    "dcard": lambda url: "dcard.tw" in url,
}


def browser_login(platform: str, timeout: int = 120) -> Dict[str, str]:
    """
    브라우저를 열어 사용자가 직접 로그인하고 쿠키를 가져옴

    Args:
        platform: 플랫폼 이름
        timeout: 로그인 대기 시간 (초)

    Returns:
        저장된 쿠키 딕셔너리
    """
    if not HAS_UNDETECTED:
        raise RuntimeError("undetected_chromedriver가 설치되지 않았습니다")

    login_url = PLATFORM_LOGIN_URLS.get(platform)
    if not login_url:
        raise ValueError(f"지원하지 않는 플랫폼: {platform}")

    logger.info(f"{platform} 브라우저 로그인 시작")

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = None
    saved_cookies = {}

    # 플랫폼별 인증 쿠키 이름
    AUTH_COOKIES = {
        "instagram": ["sessionid"],
        "facebook": ["c_user", "xs"],
        "xiaohongshu": ["web_session"],
        "dcard": ["_dcard_sess"],
    }

    try:
        logger.info("Chrome 브라우저 시작 중...")
        print(f"[browser_login] Chrome 브라우저 시작 중... ({platform})")

        driver = uc.Chrome(options=options, use_subprocess=True)

        logger.info(f"브라우저 열림, 로그인 페이지로 이동: {login_url}")
        print(f"[browser_login] 로그인 페이지로 이동: {login_url}")

        driver.get(login_url)

        logger.info(f"로그인 페이지 열림: {login_url}")
        logger.info(f"로그인 완료를 기다리는 중... (최대 {timeout}초)")
        print(f"[browser_login] 로그인 대기 중... (최대 {timeout}초)")

        # 로그인 완료 대기 (쿠키 기반 확인)
        auth_cookie_names = AUTH_COOKIES.get(platform, [])
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 쿠키 확인
            cookies = driver.get_cookies()
            cookie_dict = {c.get("name"): c.get("value") for c in cookies if c.get("name")}

            # 인증 쿠키가 있는지 확인
            has_auth = any(name in cookie_dict for name in auth_cookie_names)

            if has_auth:
                logger.info("로그인 감지됨!")
                time.sleep(3)  # 추가 대기

                # 모든 쿠키 다시 가져오기
                cookies = driver.get_cookies()

                # 플랫폼별 필요한 쿠키 저장
                config = PLATFORM_COOKIES.get(platform, {})
                required = config.get("required_cookies", [])
                optional = config.get("optional_cookies", [])
                needed_cookies = required + optional + auth_cookie_names

                for cookie in cookies:
                    name = cookie.get("name")
                    value = cookie.get("value")
                    if name and value:
                        if not needed_cookies or name in needed_cookies:
                            saved_cookies[name] = value

                if saved_cookies:
                    logger.info(f"저장된 쿠키: {list(saved_cookies.keys())}")
                    break

            time.sleep(2)

        if not saved_cookies:
            logger.warning("로그인 타임아웃 또는 쿠키 추출 실패")

    except Exception as e:
        logger.error(f"브라우저 로그인 오류: {e}")
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

    return saved_cookies


def save_cookies_to_file(platform: str, cookies: Dict[str, str]):
    """쿠키를 파일로 저장 (크롤러와 동일한 파일명 사용)"""
    cookie_dir = Path("data/cookies")
    cookie_dir.mkdir(parents=True, exist_ok=True)
    # 크롤러와 동일한 파일명 사용: {platform}_cookies.json
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    # Selenium 호환 형식으로 저장
    selenium_cookies = []
    domains = {
        "instagram": ".instagram.com",
        "facebook": ".facebook.com",
        "xiaohongshu": ".xiaohongshu.com",
        "dcard": ".dcard.tw",
    }
    domain = domains.get(platform, "")

    for name, value in cookies.items():
        selenium_cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": True,
        })

    with open(cookie_file, "w", encoding="utf-8") as f:
        json.dump(selenium_cookies, f, indent=2, ensure_ascii=False)

    logger.info(f"쿠키 파일 저장: {cookie_file} ({len(selenium_cookies)}개)")


def load_cookies_from_file(platform: str) -> Dict[str, str]:
    """파일에서 쿠키 로드 (크롤러와 동일한 파일명 사용)"""
    cookie_file = Path("data/cookies") / f"{platform}_cookies.json"

    if cookie_file.exists():
        try:
            with open(cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Selenium 형식(리스트)이면 딕셔너리로 변환
            if isinstance(data, list):
                return {c.get("name"): c.get("value") for c in data if c.get("name")}
            # 이미 딕셔너리면 그대로 반환
            return data
        except:
            pass

    return {}
