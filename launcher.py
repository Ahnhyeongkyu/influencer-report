"""
픽스업 인플루언서 리포트 - 런처
더블클릭으로 앱 실행
"""

import os
import sys
import subprocess
import webbrowser
import time
import socket
import threading
import signal

# 앱 설정
APP_NAME = "픽스업 인플루언서 리포트"
DEFAULT_PORT = 8501
APP_FILE = "src/app.py"


def get_base_path():
    """실행 파일 기준 경로 반환"""
    if getattr(sys, 'frozen', False):
        # PyInstaller로 패키징된 경우
        return os.path.dirname(sys.executable)
    else:
        # 일반 Python 실행
        return os.path.dirname(os.path.abspath(__file__))


def find_free_port(start_port=8501):
    """사용 가능한 포트 찾기"""
    port = start_port
    while port < start_port + 100:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            port += 1
    return start_port


def wait_for_server(port, timeout=30):
    """서버가 시작될 때까지 대기"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(('localhost', port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def open_browser(port):
    """브라우저 열기"""
    url = f"http://localhost:{port}"
    webbrowser.open(url)


def main():
    """메인 실행 함수"""
    print("=" * 50)
    print(f"  {APP_NAME}")
    print("=" * 50)
    print()

    # 기본 경로 설정
    base_path = get_base_path()
    os.chdir(base_path)

    # 앱 파일 확인
    app_path = os.path.join(base_path, APP_FILE)
    if not os.path.exists(app_path):
        print(f"[오류] 앱 파일을 찾을 수 없습니다: {app_path}")
        input("Enter를 눌러 종료...")
        sys.exit(1)

    # 사용 가능한 포트 찾기
    port = find_free_port(DEFAULT_PORT)
    print(f"[INFO] 포트 {port}에서 서버 시작 중...")

    # config/.env에서 환경변수 로드
    env_file = os.path.join(base_path, "config", ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
        except Exception as e:
            print(f"[WARN] .env 로드 실패: {e}")

    # Streamlit 서버 시작
    env = os.environ.copy()
    env['STREAMLIT_SERVER_HEADLESS'] = 'true'
    env['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'

    # Python 실행 파일 경로
    if getattr(sys, 'frozen', False):
        # 패키징된 경우 - 시스템 Python 사용
        python_exe = 'python'
    else:
        python_exe = sys.executable

    cmd = [
        python_exe, '-m', 'streamlit', 'run',
        APP_FILE,
        '--server.port', str(port),
        '--server.headless', 'true',
        '--browser.gatherUsageStats', 'false',
        '--theme.base', 'light',
    ]

    try:
        # 서버 프로세스 시작
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )

        print(f"[INFO] 서버 시작 대기 중...")

        # 서버 시작 대기
        if wait_for_server(port):
            print(f"[SUCCESS] 서버가 시작되었습니다!")
            print(f"[INFO] 브라우저를 여는 중...")
            print()
            print(f"  주소: http://localhost:{port}")
            print()
            print("-" * 50)
            print("  앱을 종료하려면 이 창을 닫거나 Ctrl+C를 누르세요")
            print("-" * 50)

            # 브라우저 열기
            threading.Timer(1.0, lambda: open_browser(port)).start()

            # 서버 출력 표시 (선택사항)
            try:
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        # 주요 로그만 표시
                        if 'error' in output.lower() or 'warning' in output.lower():
                            print(f"  {output.strip()}")
            except KeyboardInterrupt:
                print("\n[INFO] 종료 중...")
        else:
            print("[오류] 서버 시작 시간 초과")
            process.terminate()

    except FileNotFoundError:
        print("[오류] Python 또는 Streamlit을 찾을 수 없습니다.")
        print("      Python과 필요한 패키지가 설치되어 있는지 확인하세요.")
        input("Enter를 눌러 종료...")
        sys.exit(1)
    except Exception as e:
        print(f"[오류] {e}")
        input("Enter를 눌러 종료...")
        sys.exit(1)
    finally:
        # 정리
        if 'process' in locals():
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
        print("[INFO] 앱이 종료되었습니다.")


if __name__ == "__main__":
    main()
