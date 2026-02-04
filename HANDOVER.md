# 핸드오버 문서

> 최종 업데이트: 2026.02.04

---

## v1.7.0 5차 피드백 수정 (2026.02.04) - 최신

### 배경
- v1.5.8 전달 후 최봉석 5차 피드백(11건) 수신 (2026.02.02)
- 정해민 계약 해지 + 55만원 환불 요청 내용증명 수신 (2026.02.03)
- 11건 전수 수정 후 강경 대응 전달 결정

### 5차 고객 피드백 (최봉석, 2026.02.02) 11건 수정
1. **CSV 인코딩 깨짐**: data_processor.py content 컬럼 caption/description fallback 추가
2. **같은 작성자 덮어쓰기**: app.py 동일 author+platform 중복 감지 → 재크롤링 로직
3. **삭제/접근불가 링크**: 기존 에러 핸들링으로 사유 구분 (이미 구현됨)
4. **내용/댓글 CSV·엑셀 다운로드**: Excel에 댓글 시트 추가 (플랫폼/URL/작성자/댓글내용/좋아요)
5. **샤오홍슈 표 내용 누락**: xhs_crawler.py regex fallback에 content(desc) 추출 패턴 추가
6. **페이스북 댓글 항상 2개**: 공격적 noise filter 제거 (1~3개 댓글을 0으로 만들던 버그)
7. **페이스북 썸네일 미수집**: og:image + JSON fallback으로 썸네일 복원
8. **페이스북 좋아요 이상 수치**: author 기반 검증 임계값 조정
9. **인스타 댓글 "user" 표시**: 페이지 내 username 수집 → orphan 댓글에 매핑
10. **디카드 유니코드 깨짐**: HTML 태그 제거 (img→[이미지], br→개행, 나머지 strip)
11. **패키징 + 대응**: 전체 수정 후 강경 대응 메시지 전달

### 코드 수정 파일 (9개)
| 파일 | 수정 내용 |
|------|-----------|
| `src/__init__.py` | 버전 1.7.0 |
| `src/app.py` | 같은 작성자 덮어쓰기 방지, Excel 댓글 시트 |
| `src/crawlers/facebook_crawler.py` | 댓글 noise filter 제거, 썸네일 복원, 좋아요 임계값 |
| `src/crawlers/instagram_crawler.py` | 댓글 author username 매핑, CDN 캐시 버스팅 |
| `src/crawlers/dcard_crawler.py` | HTML 태그 제거 (유니코드 깨짐 방지) |
| `src/crawlers/xhs_crawler.py` | regex fallback content(desc) 추출 추가 |
| `src/utils/data_processor.py` | content 컬럼 caption/description fallback |
| `src/report/generator.py` | 보안 강화 + 코드 정리 |
| `src/platform_auth.py` | 보안 강화 |

---

## v1.5.8 전달 완료 (2026.01.31)

### 배경
- v1.5.6까지 1차 피드백 반영 완료
- 2차 피드백(최봉석) 수신 → v1.5.7에서 Instagram 중복/Facebook share/v/Dcard 차단 수정
- v1.5.8에서 지표 표시 개선 + Facebook engagement 정확도 대폭 개선
- 2026.01.31 고객 전달 완료

### 2차 고객 피드백 (최봉석)
1. **Instagram 중복 버그**: URL #1과 #5(다른 작성자)에서 동일 데이터 표시
2. **Facebook share/v URL**: 좋아요 0, 작성자 "프로필0"으로 표시
3. **Dcard 연속 차단**: 첫 번째 크롤링 성공, 두 번째부터 Cloudflare 차단
4. **게시물 본문 추출**: 콘텐츠 본문이 리포트에 제대로 표시 안 됨
5. **조회수 표시**: 데이터 있는 게시물도 "수집 불가"로 일괄 표시

### v1.5.7 수정 완료 (2026.01.29)
1. ✅ **Instagram 중복 버그**: URL 검증 로직 추가 (shortcode 일치 확인)
   - `instagram_crawler.py`: 페이지 로드 후 현재 URL vs 요청 URL 비교
   - 불일치 시 재로드하여 정확성 보장
2. ✅ **Facebook share/v URL**: 단축 URL 리다이렉트 처리
   - `facebook_crawler.py`: `/share/v/`, `/share/p/`, `/share/r/` 감지
   - requests.head → 실제 URL 추출 → Selenium fallback
3. ✅ **Dcard 연속 차단**: 딜레이 2초 → 8초 증가
   - `dcard_crawler.py`: `delay: float = 8.0`
   - `app.py`: Dcard 전용 8초 딜레이
4. ✅ **콘텐츠 본문 표시**: description/caption fallback 추가
   - `generator.py`: `title > content > description > caption` 순서

### v1.5.8 수정 완료 (2026.01.30~01.31)

**지표 표시 개선:**
- `generator.py`: `format_metric()` 함수 신규 추가
- None → "수집 불가" / 0 → "-" / 숫자 → 포맷팅
- 조회수, 좋아요, 공유, 저장 모두 동일 로직 적용 (PDF + 앱 화면 모두)
- `data_processor.py`: views NaN 보존 (None→0 변환 방지)
- `data_processor.py`: `generate_summary_table` 데이터 유무 기반 판단
- `app.py`: `format_views_display` 데이터 유무 기반 (pd.isna 처리)
- 기존 `PLATFORM_VIEW_SUPPORT` 딕셔너리 제거

**Facebook engagement 정확도 대폭 개선 (01.31):**
- HTML entity 디코딩 수정 (`&amp;` → `&` 처리)
- 댓글 작성자 텍스트가 engagement 수치에 혼입되는 버그 수정
- double-escaping 문제 수정 (JSON 파싱 시 `\\u` → `\u`)
- 브라우저 세션 재사용으로 안정성 향상
- sportsenternews 등 likes/comments 정확도 개선 (likes 78→1,100 등)
- 한국어 숫자 접미사 정규식 추가 (천/만/억)
- pfbid 기반 author 매칭 6가지 패턴 구현
- author 기반 full source 검증 (likes, comments)
- engagement indicator scoring 시스템
- **총 24회 반복 테스트** (페이스북_1.pdf ~ 페이스북_24.pdf)

### 테스트 결과 (2026.01.31)
| 항목 | 결과 |
|------|------|
| Instagram 중복 테스트 | ✅ 5개 URL 모두 다른 작성자 표시 |
| Facebook share/v URL | ✅ 리다이렉트 처리 동작 확인 |
| Facebook engagement 정확도 | ✅ sportsenternews likes 1,100 정상 추출 |
| 지표 표시 (format_metric) | ✅ PDF + 앱 화면 모두 데이터 유무별 정확 표시 |
| YouTube 실제 크롤링 | ✅ 조회수/좋아요/description 정상 |
| 인스타그램 실제 크롤링 | ✅ 정상 |
| 샤오홍슈 실제 크롤링 | ✅ 정상 |
| 디카드 실제 크롤링 | ✅ 정상 |
| PDF 생성 | ✅ 정상 |

### 코드 수정 파일 (12개)
| 파일 | 수정 내용 |
|------|-----------|
| `src/__init__.py` | 버전 1.5.8 |
| `src/app.py` | Dcard 8초 딜레이, format_views_display 데이터 기반 |
| `src/crawlers/instagram_crawler.py` | URL 검증 로직 (중복 방지) |
| `src/crawlers/facebook_crawler.py` | share/v 리다이렉트, engagement 정확도 대폭 개선 |
| `src/crawlers/dcard_crawler.py` | 딜레이 8초, 차단 대응 |
| `src/crawlers/youtube_crawler.py` | 마이너 수정 |
| `src/crawlers/xhs_crawler.py` | 마이너 수정 |
| `src/report/generator.py` | format_metric(), description fallback |
| `src/report/templates/report.html` | 리포트 템플릿 개선 |
| `src/utils/data_processor.py` | views NaN 보존, summary table 데이터 기반 |
| `src/utils/url_parser.py` | URL 파싱 개선 |
| `src/platform_auth.py` | 인증 처리 개선 |

### 전달 파일
- **fixup_influencer_report_v1.5.8_final.zip** (1.2MB, 33개 파일)
- 실제 크롤링 결과 PDF 5개 포함:
  - 결과물_유튜브.pdf
  - 결과물_인스타그램.pdf
  - 결과물_페이스북.pdf (24회 테스트 후 최종)
  - 결과물_샤오홍슈.pdf
  - 결과물_디카드.pdf

### GitHub 반영
- **Commit**: dbae6eb
- **메시지**: "fix: v1.5.8 - Instagram 중복, Facebook share/v 및 engagement 정확도, Dcard 차단 대응, 지표 표시 개선"
- **변경**: 12 files changed, 5,806 insertions(+), 1,634 deletions(-)
- **Remote**: FixUp-system/Report-fixtab.git (main branch)

### 고객 전달 메시지 (2026.01.31)
```
최봉석님, 안녕하세요.

말씀하신 부분 수정 완료했습니다.

[계약 범위 내 수정]
- 인스타그램: URL별 데이터 중복 표시 문제 수정
- 페이스북: share/v 링크 좋아요/작성자 0 표시 문제 수정
- 페이스북: engagement 수치 정확도 개선 (likes/comments 교차검증 추가)
- 디카드: 연속 크롤링 시 차단 문제 대응 (딜레이 조절)
- 조회수/좋아요/공유: 데이터 유무에 따라 정확하게 표시되도록 개선

[계약 범위 외 추가 작업 - 서비스 제공]
- 썸네일 이미지 수집 및 리포트 반영
- 댓글 내용 수집 기능 추가
- URL 목록 섹션 추가

각 플랫폼별 크롤링 결과 PDF도 함께 첨부합니다.
확인하시고 문제 있으면 말씀해주세요.

감사합니다.
```

### 고객 대응 전략 (법적 보호)
- **계약 범위 내/외 구분**: 모든 전달 메시지에 계약 범위 구분 기록
- **범위 외 항목 3건**: 썸네일 이미지, 댓글 내용 수집, URL 목록 섹션
- **증거 보존**: 메시지에 범위 내/외 명시하여 추후 분쟁 시 활용
- **추가 피드백 대응**: "이전 피드백에 없었던 새로운 항목"으로 프레이밍
- **사과 자제**: 과도한 사과 지양, 사실 기반 소통

### 완료 항목 (전부 완료)
- [x] v1.5.8 패키지 생성 (1.2MB, 33파일)
- [x] 실제 전 플랫폼 크롤링 테스트
- [x] 고객 전달 메시지 작성
- [x] 고객 전달 (2026.01.31)
- [x] GitHub push (commit dbae6eb)
- [x] GitHub 반영 고객 안내

### 다음 조치
- [ ] 고객 확인 대기
- [ ] 피드백 시 "새로운 항목" 프레이밍으로 대응
- [ ] 확인 완료 후 잔금 55만원 요청
- [ ] 잔금 입금 확인
- [ ] 입금 완료 후 /archive 실행

---

## v1.5.2 전달 완료 (2026.01.27)

### 전달 정보
- **파일**: 픽스업_인플루언서_리포트_v1.5.2.zip (281KB)
- **상태**: 고객 전달 완료

### v1.5.2 수정 내역
1. ✅ Facebook 작성자 URL 기반 추출 (댓글 작성자 혼동 해결)
2. ✅ Facebook 썸네일 비활성화 (부정확한 이미지 제거)
3. ✅ 샤오홍슈 ICP备 제목 문제 해결
4. ✅ PDF BodyText 스타일 중복 오류 해결
5. ✅ 인스타/페이스북 쿠키 인증 시스템 개선

### 최종 검증 결과 (2026.01.27)
| 플랫폼 | 작성자 | 수치 | 상태 |
|--------|--------|------|------|
| YouTube | HYBE LABELS | 3893만 likes, 20.4억 views | ✅ |
| Instagram | samsunggaryb82 | 27 likes | ✅ |
| Facebook | natgeo | 874 likes, 2663 shares | ✅ |
| Xiaohongshu | Bobo的科技好物 | 45 likes | ✅ |
| Dcard | 國立成功大學 | 3 likes, 10 comments | ✅ |

### 핵심 수정 코드 위치
- Facebook 작성자 URL 추출: `facebook_crawler.py:1094-1103, 1895`
- Facebook 썸네일 비활성화: `generator.py:885-886`

---

## 1. 프로젝트 기본 정보

| 항목 | 내용 |
|------|------|
| 프로젝트명 | 멀티플랫폼 인플루언서 캠페인 성과 리포트 자동화 시스템 |
| 도메인 | web/bot |
| 고객명 | 픽스업 (정해민, 배진환, 최봉석) |
| 연락처 | 카카오톡 단체방 |
| 계약금액 | 110만원 |
| 착수금/잔금 | 55만원(완료) / 55만원(대기중) |
| 마감일 | 2026.01.16 (기한 초과) |
| AS 기간 | 2개월 (2026.03.16까지) |

---

## 2. 현재 상태

**단계:** v1.7.0 5차 피드백 11건 수정 완료 (2026.02.04)

**최신 버전:** v1.7.0

**상태:** 수정 완료 → 고객 전달 예정

---

## 3. 버전 이력

### v1.7.0 (2026.02.04) - 5차 피드백 11건 수정
- CSV content 컬럼 caption/description fallback
- 같은 작성자 덮어쓰기 방지 (중복 감지 + 재크롤링)
- Excel 댓글 시트 추가
- 샤오홍슈 regex fallback content 추출
- 페이스북 댓글 noise filter 제거 + 썸네일 복원 + 좋아요 임계값 조정
- 인스타그램 댓글 author username 매핑 + CDN 캐시 버스팅
- 디카드 HTML 태그 제거 (유니코드 깨짐 방지)

### v1.5.8 (2026.01.31) - 최종 전달
- Instagram 중복 버그 수정
- Facebook share/v URL 리다이렉트 처리
- Facebook engagement 정확도 대폭 개선 (24회 반복 테스트)
- Dcard 연속 차단 대응 (딜레이 8초)
- 지표 표시 데이터 유무 기반 개선
- 전달 파일: fixup_influencer_report_v1.5.8_final.zip (1.2MB, 33파일)
- GitHub: commit dbae6eb

### v1.5.7 (2026.01.29)
- Instagram URL 검증 로직
- Facebook share/v 리다이렉트
- Dcard 딜레이 8초
- 콘텐츠 본문 description/caption fallback

### v1.5.2 (2026.01.27) - 전달됨
- Facebook 작성자 URL 기반 추출
- Facebook 썸네일 비활성화
- 샤오홍슈 ICP备 제목 해결
- PDF BodyText 스타일 중복 수정

### v1.2.9 (2026.01.26)
- FB 썸네일 필터링 강화
- XHS 댓글 DOM fallback
- 보고서 Best 카드 레이아웃 개선

### v1.2.8 (2026.01.26)
- FB Author 쿠키 없는 별도 세션 방식 해결
- FB Thumbnail 메신저/채팅 필터링
- FB Content savable_description 패턴
- Dcard nodriver fallback

### v1.2.7 (2026.01.23)
- 썸네일 크기 80mm
- URL 목록 섹션
- XHS content DOM 추출

### v1.2.6 (2026.01.21) - 전달됨
- 인코딩 깨짐 수정 (인스타/페이스북)
- 디카드 내용 수집 추가
- 샤오홍슈 개선
- 썸네일/댓글 추가 (범위 외 서비스)
- YouTube 50억+ 조회수 수정

### v1.4.0 (2026.01.19) - 전달됨
- Instagram 크롤링 데이터 추출 수정
- 5개 플랫폼 전체 테스트 성공

### v1.2 (2026.01.16) - 전달됨
- Instagram JSON 패턴 추출
- Facebook undetected_chromedriver
- 브라우저 로그인 버튼

### v1.1 (2026.01.14) - 전달됨
- 설치.bat 수정
- 샤오홍슈 JSON 파싱
- PDF 리포트 포맷

### v1.0 (2026.01.14) - 전달됨
- 최초 로컬 실행 버전

---

## 4. 플랫폼별 특이사항

| 플랫폼 | 인증 방식 | 특이사항 |
|--------|----------|----------|
| YouTube | 불필요 | yt-dlp 사용, 가장 안정적 |
| Instagram | 쿠키 필요 (선택) | undetected-chromedriver, 쿠키 없이도 일부 작동 |
| Facebook | 쿠키 필요 (선택) | undetected-chromedriver, engagement 교차검증 |
| Dcard | 불필요 | API 우선 → nodriver fallback, 8초 딜레이 |
| Xiaohongshu | QR 로그인 필수 | 앱으로 스캔, 이후 쿠키 자동 저장 |

---

## 5. 고객 대응 이력

### 2026.01.31 (v1.5.8 전달)
- 전체 수정 완료 전달
- 계약 범위 내/외 구분하여 메시지 발송
- GitHub 반영 안내
- 실제 크롤링 결과 PDF 5개 첨부

### 2026.01.29 (정해민 분쟁 경고)
- "분쟁 절차" 언급
- 2일 무응답(01.27~01.29)에 대한 불만
- v1.5.5 전달됨

### 2026.01.27 (v1.5.2 전달)
- Facebook 작성자/썸네일 수정
- 샤오홍슈 ICP备 수정
- 전달 완료

### 2026.01.26 (최봉석 검수 3차)
- FB share/v URL 5개 제공 (전부 비공개 콘텐츠)
- 샤오홍슈 내용/댓글 수집 안됨
- 인스타 @user 문제

### 2026.01.21 (최봉석 검수 2차)
- 인코딩 깨짐, 내용 누락 등 8건 피드백
- v1.2.6 전달

### 2026.01.19 (최봉석 검수 1차 + 정해민 불만)
- "오류 투성이", "고객 개발팀 시간 낭비"
- v1.4.0 전달

---

## 6. 기술적 수정 상세

### Facebook Engagement 정확도 개선 (v1.5.8, 2026.01.31)
- **HTML entity 디코딩**: `&amp;` → `&` 처리 후 JSON 파싱
- **댓글 오염 방지**: 댓글 작성자 텍스트가 likes/comments 수치에 혼입되는 버그 수정
- **Double-escaping 수정**: `\\u` → `\u` 변환 후 JSON 파싱
- **브라우저 세션 재사용**: 매 URL마다 새 브라우저 대신 기존 세션 활용
- **한국어 숫자 접미사**: `천/만/억` 정규식으로 `1.1만` → `11000` 변환
- **pfbid author 매칭**: 6가지 패턴으로 작성자 정확 추출
- **Author 기반 검증**: 작성자명이 포함된 소스에서 likes/comments 교차 검증
- **Engagement indicator scoring**: 각 추출 값에 신뢰도 점수 부여

### YouTube 조회수 오버플로우 수정
- 문제: PSY 강남스타일 (50억 조회수) → -2147483648 표시
- 원인: pandas astype(int)가 32비트 정수로 변환
- 해결: `astype('int64')` 변경 (data_processor.py:261)

### Facebook 작성자 추출 개선 (v1.2.6~v1.5.2)
- og:title 메타 태그 파싱
- video_owner JSON 패턴
- 쿠키 없는 별도 세션 방식 (v1.2.8)
- URL 기반 추출 (v1.5.2)

### Facebook 댓글 수 수정
- 문제: max() 사용 시 잘못된 큰 값 선택 (1847926)
- 해결: 첫 번째 유효한 값 사용 + 100만 이상 값 필터링

---

## 7. 다음 작업

- [x] v1.5.8 전달 완료 (2026.01.31)
- [x] GitHub push 완료 (commit dbae6eb)
- [ ] 고객 확인 대기
- [ ] 확인 완료 시 잔금 55만원 요청
- [ ] 잔금 입금 확인
- [ ] 입금 완료 후 /archive 실행

---

## 8. 결제 상태

| 항목 | 금액 | 상태 | 날짜 |
|-----|-----|-----|-----|
| 착수금 | 55만원 | ✅ 완료 | 2026.01.06 |
| 잔금 | 55만원 | ⏳ 대기 | - |
| **합계** | **110만원** | | |

---

## 9. 리스크 및 대응

| 항목 | 리스크 | 대응 |
|------|--------|------|
| 고객 분쟁 | 정해민 "분쟁 절차" 언급 (01.29) | 계약 범위 내/외 구분 증거 확보, 사과 자제 |
| 잔금 미수 | 고객 불만으로 잔금 거부 가능성 | 범위 외 작업 3건 서비스 제공 기록 |
| Instagram | 구조 변경 가능 | JSON 패턴 업데이트, AS 대응 |
| Facebook | 플랫폼 변경 빈번 | engagement 교차검증 시스템 구축 완료 |
| Dcard | Cloudflare 재차단 | nodriver fallback + 8초 딜레이 |
| Xiaohongshu | Rate limit | QR 인증 후 쿠키 자동 저장 |

---

## 10. 법적 대응 전략

### 배경
- CEO가 별도 건(40만원)에서 소송 진행 중
- 과거 사과가 "잘못 인정 증거"로 사용되는 상황
- 이번 건에서도 같은 실수 반복 방지 필요

### 원칙
1. **사과 자제**: "죄송합니다" 대신 사실 기반 소통
2. **범위 구분**: 모든 전달 메시지에 "계약 범위 내" / "계약 범위 외" 명시
3. **증거 보존**: 범위 외 작업 3건(썸네일, 댓글 내용, URL 목록)을 서비스로 제공한 기록
4. **추가 피드백 대응**: "이전 피드백에 없었던 새로운 항목" 프레이밍
5. **소통 유지**: 무응답 절대 금지 (01.27~01.29 무응답으로 분쟁 경고 받음)

### 지연 원인 분석 (객관적)
- 핵심 버그 수정: ~65-70% (인코딩, 작성자 추출, engagement 정확도 등)
- 추가 기능 개발: ~20-25% (썸네일, 댓글 내용, URL 목록)
- 리포트 레이아웃: ~10%

---

## 11. 교훈 및 개선점

1. **초반 테스트 부족**: 1차 전달 전 더 꼼꼼한 테스트 필요
2. **외부 플랫폼 의존성**: 크롤링 프로젝트는 플랫폼 변화에 취약 - 고객에게 사전 안내 필요
3. **범위 명확화**: 추가 요청은 반드시 범위 외임을 문서로 남길 것
4. **일정 버퍼**: 외부 요인 대응 위해 일정 여유 필요
5. **소통 단절 금지**: 2일 무응답 → 분쟁 경고. 최소 하루 1회 진행 상황 공유
6. **사과 주의**: 과도한 사과는 법적으로 불리하게 작용할 수 있음

---

## 12. 전달 이력 전체

| 버전 | 날짜 | 내용 | 결과 |
|-----|------|------|------|
| v1.0 | 01.14 | 최초 로컬 실행 | 설치.bat 바로 종료됨 |
| v1.1 | 01.14 | bat 수정, PDF 포맷 | 버그 발견 |
| v1.2 | 01.16 | JSON 패턴 추출 | 버그 발견 |
| v1.2.1 | 01.16 | 인증 모드 수정 | 전달 완료 |
| v1.2.6 | 01.21 | 인코딩/내용/썸네일/댓글 | 전달 완료 → 피드백 3차 |
| v1.2.8 | 01.26 | FB Author 별도 세션 | 미전달 (CEO 승인 전) |
| v1.4.0 | 01.19 | Instagram 수정 | 전달 완료 → 피드백 1차 |
| v1.5.2 | 01.27 | FB 작성자 URL, 썸네일 비활성화 | 전달 완료 → 피드백 추가 |
| v1.5.5 | 01.29 | 지표 표시 개선 | 전달 완료 |
| v1.5.8 | 01.31 | 전체 수정 + FB 정확도 | 전달 완료, 확인 대기 |
| **v1.7.0** | **02.04** | **5차 피드백 11건 수정** | **전달 예정** |
