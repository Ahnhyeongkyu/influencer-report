# 핸드오버 문서

> 최종 업데이트: 2026.02.06

---

## v1.7.3 전체 5플랫폼 검증 완료 (2026.02.06) - 최신

### 배경
- v1.7.1 이후 XHS 및 Dcard 포함 전체 5플랫폼 통합 검증 실시
- XHS xsec_token URL 방식으로 성공적 크롤링 확인
- 5차 피드백 11건 대조 검증 완료

### 실제 크롤링 검증 결과 (2026.02.06)
| 플랫폼 | URL수 | 성공 | Author | Likes | Thumb | Content | Comments |
|--------|-------|------|--------|-------|-------|---------|----------|
| YouTube | 2 | 2/2 | O | 31M, 55M | O | O | O (좋아요 포함) |
| Instagram | 3 | 3/3 | O | 3, 418, 1 | O | O | - |
| Facebook | 4 | 4/4 | O | 270, 259, 19, 9100 | O | O | O (35, 28, 38, 523) |
| Dcard | 3 | 3/3 | O | 279, 260, 110 | O | O | - |
| XHS | 5 | 5/5 | O | 244~5668 | O | O (4/5)* | - |

> *XHS content: 5개 중 4개 정상, 1개 null (영상 전용 게시물 - 정상 케이스)

### 5차 피드백 11건 대조 검증
| # | 피드백 항목 | 검증 결과 | 비고 |
|---|------------|----------|------|
| 1 | CSV 인코딩/content 누락 | ✅ | IG/FB/Dcard content 필드 정상 |
| 2 | 같은 작성자 덮어쓰기 | ✅ (코드) | 2중 보호 로직 구현 확인, 실제 테스트 미실시 |
| 3 | 삭제/접근불가 링크 | ✅ | 기존 구현 확인 |
| 4 | 엑셀 댓글 시트 | ✅ (코드) | app.py Excel 댓글 시트 구현됨 |
| 5 | 샤오홍슈 내용 누락 | ✅ | 5개 중 4개 정상, 1개 영상 게시물 |
| 6 | FB 댓글 항상 2개 | ✅ | 35, 28, 38, 523 (다양하게 수집) |
| 7 | FB 썸네일 미수집 | ✅ | 4개 모두 URL 있음 |
| 8 | FB 좋아요 이상 수치 | ✅ | 270, 259, 19, 9100 (합리적) |
| 9 | IG 댓글 "user" 표시 | ✅ (코드) | 코드 수정 확인, 테스트 URL에 댓글 없음 |
| 10 | Dcard 유니코드 깨짐 | ✅ | HTML 태그 없이 정상 텍스트 |
| 11 | 패키징/대응 | ✅ | PDF 생성 완료 |

### 같은 작성자 덮어쓰기 방지 - 2중 보호 구현 확인
1. **크롤러 레벨** (instagram_crawler.py:1338-1364)
   - CDP 캐시 클리어 + sessionStorage/localStorage 초기화
   - about:blank 이동으로 이전 페이지 상태 제거
   - URL shortcode 검증 후 불일치 시 강제 리로드

2. **앱 레벨** (app.py:1018-1034)
   - 같은 작성자 + 다른 URL + 동일 데이터 감지
   - 감지 시 3초 대기 후 재크롤링

### 산출물
- `output/v1.7.3_full_5platform_final.json` (17개 콘텐츠)
- `output/v1.7.3_full_5platform_final_20260206_161659.pdf` (481KB)

### 미검증 항목
- **같은 작성자 여러 게시물**: 코드 로직만 확인, 실제 같은 작성자 URL 2개 테스트 미실시
- **IG 댓글 "user" 표시**: 테스트 URL들에 댓글이 없어 실제 검증 불가

---

## v1.7.1 실제 크롤링 검증 완료 (2026.02.05)

### 배경
- v1.7.0 단위 테스트 완료 상태에서 실제 크롤링 테스트 실시
- 4개 플랫폼 11개 URL 전부 성공 (YouTube 2, Instagram 3, Facebook 4, Dcard 2)
- XHS는 별도 테스트 성공 (QR 로그인 + 탐색 페이지 JS 추출)
- 1차~5차 고객 피드백 28개 항목 전수 코드 검증 완료

### v1.7.1 추가 수정사항
1. **FB 캐시 오염 방지**: about:blank + CDP 캐시 클리어 (같은 데이터 반복 방지)
2. **FB "프로필" 작성자 필터**: "프로필0", "Profile" 등 잘못된 작성자명 필터링
3. **FB share URL 개선**: 로그인된 브라우저에서 JS 리다이렉트 처리
4. **FB og:image 강화**: API/Selenium 경로 모두 og:image fallback 추가
5. **IG content/title**: Mobile API + GraphQL 경로에 content/title 필드 매핑
6. **XHS 리다이렉트 감지**: 삭제/비공개 게시물 자동 감지
7. **auth .env 자동 로드**: Streamlit 인증용 config/.env 자동 로드

### 실제 크롤링 검증 결과 (2026.02.05)
| 플랫폼 | URL수 | 성공 | Author | Likes | Thumb | Content |
|--------|-------|------|--------|-------|-------|---------|
| YouTube | 2 | 2/2 | O | 31M, 55M | O | O |
| Instagram | 3 | 3/3 | O | 3, 418, 1 | O | O |
| Facebook | 4 | 4/4 | O | 270, 259, 112, 9170 | O | O |
| Dcard | 2 | 2/2 | O | 35, 4 | O | O |
| XHS* | 1 | 1/1 | O | 103 | O | O |

> *XHS: QR 로그인 후 탐색 페이지 카드 클릭 → JS __INITIAL_STATE__ 추출 방식

### GitHub 반영
- **Commit**: 4d8914b
- **메시지**: "fix: v1.7.1 — 실제 크롤링 검증 + 추가 수정"
- **변경**: 5 files changed, 131 insertions(+), 22 deletions(-)
- **Remote**: Ahnhyeongkyu/influencer-report.git (public, main branch)

### 산출물
- `output/v1.7.1_5platform_audit_20260205_180926.pdf` (203KB)
- `output/v1.7.1_5platform_audit_20260205_180926.json` (전체 결과 데이터)

---

## v1.7.0 5차 피드백 수정 (2026.02.04)

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

### 코드 수정 파일 (10개)
| 파일 | 수정 내용 |
|------|-----------|
| `src/__init__.py` | 버전 1.7.0 |
| `src/app.py` | 같은 작성자 덮어쓰기 방지, Excel 댓글 시트 추가 |
| `src/crawlers/facebook_crawler.py` | 댓글 noise filter 제거, 썸네일 og:image+JSON 복원, 좋아요 임계값 조정 |
| `src/crawlers/instagram_crawler.py` | 댓글 author username 매핑, CDN 캐시 버스팅 (Cache-Control + _cb param) |
| `src/crawlers/dcard_crawler.py` | HTML 태그 제거 (img→[이미지], br→개행, 나머지 strip) |
| `src/crawlers/xhs_crawler.py` | regex fallback path에 content(desc) 추출 패턴 3종 추가 |
| `src/utils/data_processor.py` | content 컬럼 빈 값 시 caption/description fallback |
| `src/utils/text_utils.py` | 신규 — 공유 유니코드 escape 디코딩 유틸 (fix_latin1 옵션) |
| `src/report/generator.py` | 보안 강화 + 코드 정리 |
| `src/platform_auth.py` | 보안 강화 |

### 검증 결과 (2026.02.04)
| 테스트 | 결과 |
|--------|------|
| 전체 모듈 Import (8개) | PASS |
| text_utils 유니코드 디코딩 | PASS |
| Dcard HTML 태그 제거 | PASS |
| content fallback (빈→caption) | PASS |
| 같은 작성자 중복 감지 | PASS |
| Excel 댓글 시트 생성 | PASS |
| Facebook 썸네일 추출 (og:image+JSON) | PASS |
| Instagram 댓글 author 매핑 | PASS |
| 샤오홍슈 content 추출 패턴 | PASS |
| app.py AST 파싱 + 수정 코드 확인 | PASS |

> **주의**: 단위 테스트만 완료. 실제 Streamlit 앱 실행 + URL 크롤링 테스트는 미실시.

### GitHub 반영
- **Commit**: a4c365d
- **메시지**: "fix: v1.7.0 — 5차 고객 피드백 11건 버그 수정"
- **변경**: 12 files changed, 862 insertions(+), 268 deletions(-)
- **Remote**: Ahnhyeongkyu/influencer-report.git (public, main branch)
- **FixUp-system remote**: 접근 불가 (Repository not found)

### 강경 대응 메시지 (2026.02.04, 작성 완료 — 전송 전 CEO 확인 필요)
```
안녕하세요.

2월 2일 최봉석님이 보내주신 피드백 11건 전부 수정 완료했습니다.

[수정 내역]
1. CSV 파일 인코딩 깨짐 → 수정
2. 같은 작성자 여러 게시물 데이터 덮어쓰기 → 중복 감지 + 재크롤링 처리
3. 삭제/접근불가 링크 → 에러 사유 구분 표시
4. 게시물 내용/댓글 다운로드 → 엑셀에 댓글 시트 추가 (작성자, 내용, 좋아요 포함)
5. 샤오홍슈 표 내용 컬럼 누락 → 수정
6. 페이스북 댓글 수 항상 2개 → 수정
7. 페이스북 썸네일 미수집 → 수집 복원
8. 페이스북 좋아요 수 이상 → 검증 로직 조정
9. 인스타그램 댓글 작성자 "user" 표시 → 실제 username 매핑
10. 디카드 내용 유니코드 깨짐 → 수정

2일자 피드백 수신 후 2일 내 전수 수정 완료한 상태입니다.
GitHub에 v1.7.0으로 반영했습니다. 확인 부탁드립니다.

---

아울러, 정해민님이 보내신 계약 해지 및 환불 요청 건에 대해 말씀드립니다.

해당 요청은 수용이 어렵습니다. 사유는 아래와 같습니다.

1. 당사는 1월 14일 최초 전달 이후 현재까지 총 11회 버전을 전달했습니다 (v1.0 ~ v1.7.0). 피드백이 올 때마다 수정하여 전달했고, 이번 11건도 2일 내 전수 수정했습니다. 계약 이행 의지가 없었다면 이런 대응은 불가능합니다.

2. 계약 범위 외 작업 3건(썸네일 이미지 수집, 댓글 내용 수집, URL 목록 섹션)을 서비스로 추가 제공했습니다. 이 부분은 원래 계약에 포함되지 않은 항목입니다.

3. 1월 16일 마감 이후에도 3주간 지속적으로 수정 대응했습니다. 기한 초과에 대해서는 상호 합의 하에 진행한 것이며, 이를 이유로 한 일방적 해지는 부당합니다.

4. 착수금 55만원에 해당하는 작업은 이미 완료되어 전달된 상태이며, 오히려 잔금 55만원이 미지급 상태입니다.

따라서 계약 해지가 아닌 잔금 55만원 지급을 요청드립니다.

수정된 v1.7.0 확인하시고, 추가 수정 사항 있으면 말씀해주시면 대응하겠습니다. 다만 현재 상태에서 일방적인 계약 해지 및 환불 요청이 계속될 경우, 저희도 법적 절차를 통해 잔금 청구를 진행할 수밖에 없는 점 양해 부탁드립니다.

감사합니다.
```

### 다음 조치
- [ ] **실제 크롤링 테스트** (streamlit run src/app.py → 각 플랫폼 URL 1개씩)
- [ ] CEO 메시지 확인 후 고객 전달
- [ ] 고객 반응 대기
- [ ] 반응에 따라 법적 대응 또는 잔금 수령 진행

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

**단계:** v1.7.3 전체 5플랫폼 검증 완료 (2026.02.08)

**최신 버전:** v1.7.3

**상태:** 검증 완료 → 최종 전달 준비

---

## 3. 버전 이력

### v1.7.1 (2026.02.05) - 실제 크롤링 검증 + 추가 수정
- FB 캐시 초기화 + "프로필" 작성자 필터 + share URL 개선 + og:image 강화
- IG content/title 필드 매핑 (Mobile API + GraphQL)
- XHS 리다이렉트 감지 (삭제/비공개)
- 4개 플랫폼 11개 URL 전부 실제 크롤링 성공 검증
- auth config/.env 자동 로드
- GitHub: commit 4d8914b

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

### 2026.02.03 (정해민 내용증명 수신)
- 계약 해지 + 착수금 55만원 환불 요청
- 기한: 2026.02.04 18:00 (응답 기한)
- CEO 결정: 강경 대응 (Option A — 전수 수정 + 해지 거부)

### 2026.02.02 (최봉석 5차 피드백)
- 11건 버그 리포트 수신
- CSV 인코딩, 같은 작성자 덮어쓰기, 내용/댓글 다운로드, FB 댓글/썸네일/좋아요, IG 댓글 user, XHS 내용 누락, Dcard 유니코드

### 2026.02.04 (v1.7.0 수정 완료)
- 11건 전수 수정 완료 (코드 수정 + 단위 테스트)
- GitHub push 완료 (public remote, commit a4c365d)
- 강경 대응 메시지 작성 완료 (CEO 확인 후 전송 대기)
- **실제 크롤링 테스트 미실시** — 전달 전 필요

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
- [x] 5차 피드백 11건 수신 (2026.02.02)
- [x] v1.7.0 전수 수정 완료 (2026.02.04)
- [x] GitHub push 완료 (public, commit a4c365d)
- [x] 강경 대응 메시지 작성 완료
- [x] 단위 테스트 9개 스위트 전부 PASS
- [x] **실제 크롤링 테스트** — v1.7.3 전체 5플랫폼 17개 콘텐츠 성공 (02.06)
- [x] 사용 매뉴얼 v1.7.3 + Mac 지원 업데이트 (02.08)
- [x] GitHub push 완료 (commit fff647a, public/main)
- [ ] CEO 메시지 최종 확인 후 고객 전달
- [ ] 고객 반응 대기 (수락 or 법적 대응)
- [ ] 잔금 55만원 수령 또는 법적 절차 진행
- [ ] 완료 시 /archive 실행

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
| 고객 분쟁 | 정해민 내용증명 수신 (02.03) — 계약해지+55만원 환불 | 강경 대응: 해지 거부 + 잔금 청구 + 법적 대응 시사 |
| 잔금 미수 | 55만원 미지급 + 환불 요청 | 11회 전달 이력 + 범위외 3건 서비스 증거로 대응 |
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

### 내용증명 대응 근거 (02.04 정리)
- **이행 의지 증명**: v1.0~v1.7.0까지 11회 버전 전달 (01.14~02.04, 3주간)
- **범위 초과 서비스**: 썸네일 이미지, 댓글 내용 수집, URL 목록 섹션 (계약서에 없음)
- **즉각 대응**: 02.02 피드백 11건 → 02.04 전수 수정 (2일 내)
- **마감 후 합의 진행**: 01.16 마감 이후에도 고객 요청에 따라 수정 지속 → 묵시적 합의
- **잔금 미지급**: 착수금 55만원 분의 작업은 이미 완료 전달, 잔금 55만원 미수

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
| v1.7.0 | 02.04 | 5차 피드백 11건 수정 | 내부 검증 |
| v1.7.1 | 02.05 | 실제 크롤링 검증 + 추가 수정 | 검증 완료 |
| **v1.7.3** | **02.06** | **전체 5플랫폼 통합 검증 (17개)** | **최종 전달 준비** |
