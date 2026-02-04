"""
텍스트 처리 유틸리티

크롤링 결과의 유니코드/인코딩 문제를 처리하는 공통 함수
"""

import re


def decode_unicode_escapes(text: str, fix_latin1: bool = False) -> str:
    """유니코드 이스케이프 시퀀스를 디코딩 (\\uXXXX -> 실제 문자)

    이모지 등 surrogate pair도 올바르게 처리합니다.

    Args:
        text: 디코딩할 텍스트
        fix_latin1: True이면 UTF-8이 Latin-1로 잘못 해석된 경우도 수정
                    (Dcard 등 대만 플랫폼에서 사용)

    Returns:
        디코딩된 텍스트
    """
    if not text:
        return text
    try:
        # Latin-1 → UTF-8 복원 (대만/중국어 플랫폼 전용)
        if fix_latin1:
            try:
                fixed = text.encode('latin-1').decode('utf-8')
                if fixed != text:
                    fixed = _replace_basic_escapes(fixed)
                    return fixed
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass

        # \uXXXX 패턴을 실제 문자로 변환
        if '\\u' in text:
            def replace_unicode(match):
                try:
                    return chr(int(match.group(1), 16))
                except ValueError:
                    return match.group(0)

            decoded = re.sub(r'\\u([0-9a-fA-F]{4})', replace_unicode, text)

            # surrogate pair 처리 (이모지 등)
            try:
                decoded = decoded.encode('utf-16', 'surrogatepass').decode('utf-16')
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
        else:
            decoded = text

        return _replace_basic_escapes(decoded)
    except Exception:
        return text


def _replace_basic_escapes(text: str) -> str:
    """기본 이스케이프 시퀀스 치환"""
    text = text.replace('\\n', '\n').replace('\\r', '\r')
    text = text.replace('\\t', '\t').replace('\\"', '"')
    text = text.replace('\\/', '/')
    return text
