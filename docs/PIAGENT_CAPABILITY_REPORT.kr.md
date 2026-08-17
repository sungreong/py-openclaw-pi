---
title: PiAgent GPT-OSS-120B 실실행 평가와 기능 우선순위
theme: report
intent: reference
toc: true
---

# PiAgent GPT-OSS-120B 실실행 평가와 기능 우선순위

작성일: 2026-08-15  
평가 환경: Windows 호스트 + `docker compose`의 `pi_agent` 서비스 + Local Bedrock GPT-OSS-120B  
비밀값 처리: `.env`는 Compose가 주입만 했으며 키·값을 읽거나 출력하지 않았다.

## 1. 결론

PiAgent는 현재 **단순 일상 추론·로컬 문서 질의응답·작은 코드 수정은 실사용 가능한 L3**, **외부 뉴스를 여러 단계로 조사해 정확한 분석 보고서를 만드는 능력은 L2~L3 사이**다. DOCX 파일 생성은 가능해졌지만 LibreOffice가 없어 페이지 렌더 검수까지 하는 L5 문서 제작은 아니다.

실제 Bedrock 호출 결과는 다음과 같다.

| 과제 | 실제 결과 | 판정 |
| --- | --- | --- |
| 제약이 있는 3시간 일정 작성 | 7개 조건 전부 충족 | 통과, 10/10 |
| 로컬 정책 문서 5문항 Q&A | 경계값·권한 예외 포함 5/5 정답 | 통과, 10/10 |
| 작은 Python 경계값 버그 수정 | 한 줄 최소 수정, 독립 재검증 `6 passed` | 통과, 8/10 |
| 베트남-한국 최근 뉴스 Word 보고서 | 검색·검증·DOCX 생성 완료, 본문 1건만 확보하고 핵심 문장 일부 과장 | 제한적 통과, 6/10 |

따라서 “Deep Search가 되는가?”에 대한 답은 **아직 아니다**. 현재는 날짜가 있는 다국어 검색, 출처 후보 수집, URL 동일성 검증, 일부 본문 수집, 제한 표시까지 가능한 **assisted research** 수준이다. 검색→본문 확보→상충 검토→주장 단위 근거 검증을 런타임이 강제하는 단계까지 가야 deep research라고 부를 수 있다.

## 2. 이번에 실제로 추가·검증한 기능

### `.piagent` 확장

| 경로 | 기능 | 상태 |
| --- | --- | --- |
| `.piagent/tools/news-search/tool.py` | Google/Bing RSS 날짜 검색, 3개 언어 번들 검색, URL 정규화, 근거 ID, 근거 URL 검증 | 동작 |
| `.piagent/tools/word-report/tool.py` | Markdown 제목·목록·표를 실제 DOCX 구조로 변환 | 동작 |
| `.piagent/skills/news-research-report/SKILL.md` | 검색·본문·근거 게이트·제한 보고서·Word 생성 절차 | 동작하지만 모델 준수는 완전하지 않음 |
| `.piagent/packages` | exact allowlist로 `python-docx==1.2.0` 설치 | 실제 설치·import·사용 성공 |

Docker 진단에서는 확장 도구를 포함해 32개 도구가 로드됐다. 핵심 신규 도구는 `news_search`, `news_research_bundle`, `news_evidence_validate`, `word_report_create`다.

### 코어 개선

- `simple_piagent.py --prompt-file`: Windows 셸에서 한국어가 `?`로 깨지는 문제를 피하도록 UTF-8 파일 입력을 지원한다.
- `--skill`, `--max-model-calls`: 심플 실행기에서 스킬 선택과 비용 상한을 지정할 수 있다.
- prompt 파일은 워크스페이스 안에서만 읽고 `.env` 등 차단 경로 정책을 그대로 적용한다.
- 최종 출력의 `<reasoning>`, `<analysis>`, `<thinking>` 블록을 제거한다.
- 모델 부분 출력은 버퍼링하고 정제된 최종 답만 노출해 터미널의 내부 추론 노출을 막는다.
- 문자열로 반환된 `Error:`와 `Error fetching URL`도 도구 실패로 집계한다.

## 3. 실제 Task별 결과

### E-01. 일상 일정 제약 해결

입력은 09:00~12:00 안에 운동, 50분 보고서, 고정 회의, 약국, 점심 준비와 일정 사이 10분 간격을 모두 배치하는 문제였다.

모델 결과:

| 일정 | 시간 |
| --- | --- |
| 운동 | 09:00~09:20 |
| 보고서 | 09:30~10:20 |
| 화상회의 | 10:30~11:00 |
| 약국 | 11:10~11:30 |
| 점심 준비 | 11:40~12:00 |

7개 조건을 모두 만족했다. 도구 호출은 0회였다. **구조가 명확한 일상 계획·제약 문제는 안정적**이다.

### E-02. 참고 문서 기반 Q&A

로컬 장애 대응 정책 하나만 읽게 하고 5문항을 물었다.

- SEV-1 담당자 지정: 최초 인지 후 15분 이내 — 정답
- 이메일만으로 1차 호출 인정 여부: 불인정 — 정답
- 정확히 500,000원 보상 시 사전 승인: 불필요 — 정답
- 사후 검토: 종료 후 5영업일 이내 — 정답
- Incident Commander의 보존 기간 단축 권한: 없음 — 정답

도구 흐름은 `ls → ls → read`, 정답률은 5/5다. 다만 요청 파일이 이미 명시됐는데 루트 `ls`부터 한 것은 비효율적이다. **짧고 구조화된 단일 참고문서 Q&A는 신뢰할 수 있는 수준**이지만, 대용량 다중 문서·상충 문서·표/이미지는 별도 평가가 필요하다.

### E-03. 작은 코드 수정

기준선은 `1 failed, 5 passed`였다. `requested == stock`도 정상 예약이어야 하는데 `requested >= stock`이 예외를 발생시키는 문제였다.

모델은 `>=`를 `>`로 한 줄만 수정했고 자체 테스트에서 `6 passed`, 독립 Docker 재검증에서도 `6 passed`였다. 관련 없는 파일은 수정하지 않았다.

한 번 `exec<|channel|>commentary`라는 잘못된 도구명을 생성했지만 런타임이 거부하자 정상 `exec`로 재시도해 복구했다. **작은 범위의 재현 가능한 버그 수정은 가능하지만 GPT-OSS 도구 호출 포맷 오류가 간헐적으로 존재**한다.

### E-04. 베트남-한국 최근 뉴스 Word 보고서

#### 첫 실행: 검색 도구 부재

기본 `web_search`는 결과를 얻지 못했다. 초기 실행은 근거 없이 사실을 만들어내는 실패가 있었고, 재시도에서는 안전하게 검색 실패 보고서를 만들었다. 이 단계에서는 “최신 뉴스 분석”을 맡길 수 없었다.

#### 두 번째 실행: `news_search`와 스킬 추가

- 검색 3회, 본문 수집 2회, Markdown·DOCX 생성
- 출처 6건을 제시했으나 모델이 검색 URL을 임의 재작성
- 한 URL은 반복 문자열로 약 7KB가 되어 손상
- DOCX는 실제 표 0개, Markdown 표 행 8개가 평문으로 남음
- 최대 문단 길이 7,900자

파일은 만들어졌지만 출처 무결성과 Word 품질이 불합격이었다.

#### 세 번째 실행 전 실패: 엄격 절차만 추가

모델이 영어 검색 1회 후 직접 URL을 추측해 404를 만들고, “curl을 써보겠다”는 내부 추론만 남긴 채 종료했다. 최종 sanitizer는 내부 추론을 숨겼지만 작업은 미완료였다. **SKILL.md 지시만으로 다단계 완료를 보장할 수 없다는 증거**다.

#### 세 번째 실행: 검색 번들·근거 검증·Word 도구 추가

| 지표 | 실제값 |
| --- | ---: |
| 다국어 검색 | 3회 (`en`, `vi`, `ko`) |
| 원시 검색 결과 | 18건 |
| 압축 후보 | 12건 |
| 후보 독립 발행처 | 11곳 |
| 보고서 채택 출처 | 2건 |
| 근거 ID/URL 검증 | 2/2 통과 |
| 실제 본문 확인 | 1건 |
| Word 구조 | 제목 8, 실제 표 1개, 표 3행×6열 |
| Markdown 표 잔재 | 0 |
| 2KB 초과 문단 | 0 |

출처 수와 본문 수가 부족하므로 모델은 보고서를 `제한된 근거`로 표시했다. URL은 `news_evidence_validate`를 통과한 값만 사용했고 DOCX 구조도 이전보다 명확히 개선됐다.

하지만 내용 정확도에는 두 가지 문제가 남았다.

1. 헤럴드경제 본문은 한국 산업부와 원자력안전위원회의 국내 MOU가 베트남 등 잠재 시장을 지원한다는 내용이다. 보고서는 이를 “한국이 베트남을 대상으로 규제 협력 프레임워크를 체결”했다고 써 행위자와 합의 범위를 넓혔다.
2. 본문 1건과 스니펫 1건뿐인데도 “양국 협력이 확대되고 있다”고 결론 내렸다. `제한된 근거` 표시는 했지만 주장 강도는 충분히 낮추지 못했다.

즉 **검색·파일 생성 파이프라인은 완주했지만 분석 정확도는 사람 검토 없이 배포할 수준이 아니다.**

## 4. DOCX 품질 판정

개선 전 DOCX와 전용 Word 도구 사용 후의 차이는 명확하다.

| 항목 | 개선 전 | 개선 후 |
| --- | ---: | ---: |
| 실제 Word 표 | 0 | 1 |
| 표 행 | 0 | 3 |
| Markdown 표 평문 | 8행 | 0행 |
| 최대 문단 길이 | 7,900자 | 283자 |
| ZIP/OOXML 무결성 | 정상 | 정상 |

다만 Word 페이지 렌더는 `soffice`/LibreOffice 실행 파일이 없어 실패했다. 따라서 겹침, 잘림, 페이지 나눔, 한국어 글꼴 대체는 육안 검증하지 못했다. 현재 판정은 **구조적 DOCX 생성 통과, 시각 QA 미완료**다.

## 5. 현재 수준

| 단계 | 의미 | 현재 판정 |
| --- | --- | --- |
| L1 | 일반 텍스트 답변 | 안정적 |
| L2 | 로컬 파일·공개 웹을 읽고 답변 | 단일 문서는 안정적, 웹은 제공자 품질 의존 |
| L3 | 파일 수정·명령·테스트 실행 | 작은 코딩 과제 통과 |
| L4 | Markdown/DOCX artifact 생성 | 구조적 생성 가능, 내용 검증은 부분적 |
| L5 | 문서 렌더·시각 검수까지 완료 | 미완료 |
| L6 | 장시간 deep research/외부 업무 자동화 | 미완료 |

실용적인 해석:

- 일상 계획: 사용할 수 있음
- 짧은 참고문서 Q&A: 사용할 수 있음
- 작은 코드 수정: 테스트가 있으면 사용할 수 있음
- 최신 뉴스 요약: 출처 원장과 `제한된 근거`를 전제로 보조용으로 사용
- deep search, 투자·법률·의료 등 고위험 조사: 현재 자동 결과를 그대로 사용하면 안 됨
- Word 보고서: 초안 생성 가능, 배포 전 내용·시각 검토 필요

## 6. 기능 요구사항과 우선순위

### P0 — 정확도와 완료 보장

| ID | 요구사항 | Acceptance Criteria |
| --- | --- | --- |
| FR-01 | 스킬 완료 게이트 | 뉴스 스킬 종료 전에 번들 검색 1회, 근거 검증 1회, 요청 artifact 확인을 런타임이 검사한다. 빠지면 완료가 아니라 `incomplete`로 반환한다. |
| FR-02 | 원문 URL 확보 | 검색 후보의 최소 70%에서 실제 발행처 URL을 제공한다. Google News 중계 페이지만 반환하면 본문 확인으로 세지 않는다. |
| FR-03 | 주장-근거 검증 | 보고서의 핵심 문장을 출처·근거 구절과 연결하고, 행위자·대상·수치·시점이 근거보다 넓어지면 생성 실패 또는 수정 요청한다. |
| FR-04 | typed tool result | 도구가 `status=ok/error/no_results`를 구조화해 반환하고 감사 로그의 `is_error`와 일치한다. 문자열 오류도 회귀 테스트한다. |
| FR-05 | reasoning 비노출 | CLI/chat/audit/final 어디에도 `<reasoning>` 내용이 표시되지 않는다. 실제 스트리밍 provider fixture로 검증한다. |
| FR-06 | 평가 러너 | 일상·참고문서·코딩·뉴스 fixture를 한 명령으로 실행하고 점수, 도구 오류, artifact, 근거 수를 JSON/Markdown으로 남긴다. |

### P1 — 문서·연구 품질

| ID | 요구사항 | Acceptance Criteria |
| --- | --- | --- |
| FR-07 | report Docker profile | `python-docx`와 LibreOffice를 고정 버전으로 제공하고 DOCX를 페이지 이미지로 렌더한다. |
| FR-08 | Word 시각 QA | 표 잘림, 빈 페이지, 겹침, 깨진 글꼴이 없음을 자동·수동 확인해야 성공 처리한다. |
| FR-09 | 상충 출처 탐색 | 최소 3개 독립 발행처와 2개 원문을 확보하고 서로 다른 주장·날짜·수치를 별도 섹션에 기록한다. |
| FR-10 | 도구 호출 호환성 | GPT-OSS가 생성한 도구명에서 허용되지 않는 채널 토큰을 안전하게 거부하고 한 번의 교정 재시도를 지원한다. |
| FR-11 | 패키지 공급망 강화 | exact allowlist 외에 hash, 다운로드 크기, 설치 기록, 부분 실패 정리를 검증한다. |

### P2 — 확장 과제

| ID | 요구사항 | Acceptance Criteria |
| --- | --- | --- |
| FR-12 | 주가 데이터·차트·Word thin slice | 고정 fixture로 데이터 표와 차트를 포함한 DOCX를 만들고 렌더 검증한다. 이후 실제 공급자를 연결한다. |
| FR-13 | 다중 참고문서 RAG | 문서별 인용과 상충 탐지로 100문서 규모 평가셋을 통과한다. |
| FR-14 | Central skill bridge | diff 후 선택한 스킬만 `.piagent/skills`에 적용하고 워크스페이스 변경을 덮어쓰지 않는다. |

## 7. 회귀·보안 결과

- Docker 전체 회귀: `80 passed in 10.46s`.
- `.env` 값은 읽거나 출력하지 않았다.
- 감사 로그·artifact에서 Bedrock 키 접두사나 `LOCAL_BEDROCK_API_KEY` 문자열이 발견되지 않았다.
- prompt 파일로 `.env`를 읽는 테스트는 차단됐다.
- 관련 없는 orphan container `python_ubuntu_dev`는 경고만 확인하고 삭제하지 않았다.

## 8. 재현 명령

진단:

```powershell
docker compose run --rm `
  -e PI_WORKSPACE_EXTENSIONS_ENABLED=true `
  -e PI_ALLOW_PACKAGE_INSTALL=true `
  -e PI_PACKAGE_INSTALL_ALLOWLIST=python-docx==1.2.0 `
  pi_agent python simple_piagent.py --check --workspace /app
```

뉴스 실실행:

```powershell
docker compose run --rm `
  -e PI_WORKSPACE_EXTENSIONS_ENABLED=true `
  -e PI_ALLOW_PACKAGE_INSTALL=true `
  -e PI_PACKAGE_INSTALL_ALLOWLIST=python-docx==1.2.0 `
  pi_agent python simple_piagent.py --workspace /app `
  --prompt-file artifacts/live-eval-vietnam-news-20260815/prompt-extension-v3.ko.txt `
  --skill news-research-report --session vietnam-news-extension-v3-20260815 `
  --max-model-calls 24
```

전체 회귀:

```powershell
docker compose run --rm pi_agent python -m pytest -q
```

## 9. 다음 구현 순서

다음 thin slice는 FR-01과 FR-03이다. 지금 가장 큰 위험은 파일 생성 실패가 아니라 **모델이 근거보다 강한 문장을 쓰고도 완료되는 것**이다. 이후 FR-02로 원문 URL 비율을 높이고, FR-07~08로 Word 렌더 검증을 닫는 순서가 적절하다.
