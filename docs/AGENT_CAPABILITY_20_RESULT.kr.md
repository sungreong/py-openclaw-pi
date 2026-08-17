---
title: PiAgent GPT-OSS-120B 20개 실실행 역량 평가
theme: report
intent: reference
toc: true
---

# PiAgent GPT-OSS-120B 20개 실실행 역량 평가

작성일: 2026-08-15  
실행 ID: `gptoss120b-20260815-20task-v1`  
모델: Local Bedrock `openai.gpt-oss-120b-1:0`  
평가 방식: 실제 모델 호출, Tool 감사 로그, 산출물 검사, 독립 pytest, DOCX 구조 감사

## 결론 {: .briefing-lead}

PiAgent는 현재 **로컬 파일·세션·메모리·스킬·코드 수정·구조적 문서 생성까지 연결하는 L4 실험형 에이전트**다. 결정론적인 로컬 작업은 실사용에 가까우나, 최신 뉴스의 snippet을 근거로 의미를 확장하는 문제가 남아 **deep research는 L3 보조 수준**이다.

최종 자동 평가는 **98.75/100, 19개 통과·1개 부분 통과**다. 20개 Task에서 최신 최종 시도 기준 Tool 51회를 실행했고 Tool 오류는 0건이었다. 그러나 이 숫자는 미리 선언한 형식·도구·산출물 기준 점수다. 사람 감사에서는 T20이 `제한` 표시를 누락하고 RSS 제목 수준의 근거를 정책 확정처럼 서술해 완전 통과로 보지 않았다.

핵심 해석은 다음과 같다.

- 파일 탐색, 단일 문서 Q&A, CSV 계산: 안정적
- Plan Mode, 회사 스킬 적용, Git 읽기 전용 정책: 안정적
- 동일 세션 복원, 다른 세션 조각 검색, 장기 메모리 검색: 실제 성공
- 작은 코드 수정과 pytest 검증: 안정적
- Markdown·DOCX 생성: 구조적으로 성공
- 최신 뉴스 후보 검색과 URL 동일성 검증: 가능
- 본문이 없는 뉴스의 사실성 판단과 분석 보고서: 사람 검토 필요
- LibreOffice 페이지 렌더와 육안 Word QA: 현재 환경에서는 미완료

## 평가 계약

20개 질문은 쉬운 작업부터 복합 작업까지 다음 역량을 순차적으로 확인한다.

| 구간 | 확인한 역량 | 판정 기준 |
| --- | --- | --- |
| L1 | 계산, 정렬, `ls`, `read`, `grep` | 정답, 요구 Tool, Tool 오류 없음 |
| L2 | CSV 계산, Plan Mode, 스킬 자동 선택, 동일 세션 | 값 정확성, 변경 차단, Skill 감사 로그, 세션 복원 |
| L3 | 다른 세션, 장기 메모리, 데이터 보고서, 코드 리뷰 | `search → get`, 산출물 내용, 원본 무수정 |
| L4 | 코드 수정·pytest, Git 정책, 뉴스 근거, DOCX | 독립 테스트, 읽기 전용 명령, URL 검증, OOXML |
| L5 | 메모리+다른 세션+보고서+DOCX | 모든 Tool 완주, 제한 표시, Markdown·DOCX |

질문과 자동 검사 원본은 `evaluation/agent_capability_20_tasks.json`, 전체 답변과 Tool 증거는 실행 폴더의 `tasks/TNN.json`이 기준이다.

## 20개 실제 결과

| ID | 난이도 | 실제 질문 요약 | 실제 Tool 흐름 | 자동 판정 |
| --- | ---: | --- | --- | --- |
| T01 | 1 | `(17×23)+5` 계산 | 없음 | 5/5 |
| T02 | 1 | 우선순위 구조화 정렬 | 없음 | 5/5 |
| T03 | 1 | 평가 디렉터리 목록 확인 | `ls` | 5/5 |
| T04 | 1 | `brief.md` 코드명 질의 | `read` | 5/5 |
| T05 | 1 | `FEATURE_ALPHA` 위치 검색 | `grep` | 5/5 |
| T06 | 2 | CSV 합계·최대 지역 계산 | `ls → read` | 5/5 |
| T07 | 2 | Plan Mode에서 수정 없는 계획 | `find → read → plan_note_write` | 5/5 |
| T08 | 2 | 회사 Python 가이드 정책 Q&A | `read` | 5/5 |
| T09 | 2 | 코드명 세션 저장 | 없음 | 5/5 |
| T10 | 2 | 새 Agent 인스턴스의 동일 세션 회상 | 없음 | 5/5 |
| T11 | 3 | 다른 세션 코드명 복원 | `session_fragment_search ×2 → session_fragment_get` | 5/5 |
| T12 | 3 | 사용자 선호 장기 메모리 저장 | `memory_store` | 5/5 |
| T13 | 3 | 새 세션에서 장기 메모리 복원 | `memory_search → memory_get` | 5/5 |
| T14 | 3 | CSV 기반 Markdown 보고서 | `ls → read → exec → read → write → ls` | 5/5 |
| T15 | 3 | 회사 규칙 기반 코드 리뷰 | `read ×2 → todo_read → todo_write → write → ls → read` | 5/5 |
| T16 | 4 | 경계값 버그 수정과 pytest | `ls → read ×2 → edit → exec` | 5/5 |
| T17 | 4 | 회사 Git 정책 상태 분석 | `read → exec_readonly` | 5/5 |
| T18 | 4 | 최근 베트남-한국 뉴스 후보 검증 | `news_research_bundle → news_evidence_validate` | 5/5 |
| T19 | 4 | Markdown에서 Word 생성 | `ls → read → ls → word_report_create → ls` | 5/5 |
| T20 | 5 | 메모리·다른 세션·Markdown·Word 복합 작업 | `memory_search → memory_get → session_fragment_search ×2 → session_fragment_get → write → word_report_create → ls → todo_write` | 3.75/5 |

## 메모리와 다른 세션 참고 결과

### 동일 세션

T09에서 `NEBULA-731`을 장기 메모리에 저장하지 않고 세션 대화에만 남겼다. Agent 객체를 닫고 새 객체로 실행한 T10은 Tool 없이 `NEBULA-731`을 복원했다. 세션 JSON 저장·재로딩이 실제로 동작한다.

### 다른 세션

T11은 현재 세션이 아닌 T09 세션을 지정해 `session_fragment_search`로 후보 ID를 받고 `session_fragment_get`으로 원문 조각을 읽었다. 단순히 모델이 우연히 기억한 것이 아니라 다른 세션 저장소를 Tool로 조회했다.

### 장기 메모리

T12는 “한국어 표, 위험은 RED” 선호를 `memory_store`에 저장했다. 다른 세션 T13과 T20이 `memory_search → memory_get` 순서로 같은 선호를 복원했다.

현재 한계는 다른 세션의 **대화 최종 답변**은 검색할 수 있지만 그 세션의 전체 Tool 결과 원장은 직접 검색하지 못한다는 점이다. T20은 T18의 요약 답변을 가져왔지만 `news_research_bundle`의 전체 후보·검증 레벨을 그대로 복원하지 못했다.

## 뉴스·Deep Research 판정

T18의 실제 검색 결과는 다음과 같다.

| 지표 | 값 |
| --- | ---: |
| 언어별 검색 | 영어·베트남어·한국어 3회 |
| 원시 결과 | 18건 |
| 압축 후보 | 12건 |
| 독립 발행처 | 12곳 |
| 최종 채택 | 2건 |
| URL/evidence ID 검증 | 2/2 |
| 본문 확보 | 0건 |
| 근거 수준 | 2건 모두 `snippet-only` |

`news_evidence_validate`가 검증한 것은 검색 Tool이 반환한 evidence ID와 URL의 동일성이다. 기사 내용이 사실인지, 두 기사가 서로 독립 취재인지, 정책이 실제 확정됐는지까지 검증한 것이 아니다.

T20 최신 결과는 제한 문구를 요청받았지만 이를 누락하고 “베트남이 전략을 추진하고 있다”라고 단정했다. “외국 투자 의존도 증가”도 두 제목에서 직접 확인되지 않는 추론이다. 따라서 현재 뉴스 기능은 **후보 수집과 출처 원장 작성에는 사용 가능**, **deep research 결과를 검토 없이 배포하는 용도에는 부적합**하다.

## Word/DOCX 판정

T19와 T20은 실제 `.docx`를 생성했다. 개선 후 구조 감사 결과는 다음과 같다.

| 검사 | T19 | T20 |
| --- | ---: | ---: |
| ZIP/OOXML 무결성 | 통과 | 통과 |
| Heading 스타일 | H1 1개, H2 2개 | H1 1개, H2 다수 |
| 실제 Word 표 | 1개 | 2개 |
| 표 `tblW/tblInd/tblGrid/tcW` 일치 | 통과 | 통과 |
| 첫 행 header 표시 | 통과 | 통과 |
| 접근성 감사 high/medium/low | 0/0/0 | 0/0/0 |
| HTML 태그 잔존 | 없음 | 없음 |
| 페이지 PNG 렌더 | 미실행 | 미실행 |

`render_docx.py`는 실행했지만 Windows 환경에 `soffice`가 없어 `FileNotFoundError`로 중단됐다. 따라서 표 잘림, 페이지 나눔, 한글 글꼴 대체는 육안 통과로 주장하지 않는다. Docker 이미지에는 `python-docx==1.2.0`과 `lxml==5.3.0`을 고정 설치해 구조적 Word 생성은 모델 설정만으로 가능하게 했다.

## 평가 중 발견하고 개선한 기능

| 문제 | 원인 | 개선 |
| --- | --- | --- |
| 데이터 보고서 Skill 사전검사 실패 | 필수 `python_package_install`이 `tool_allow`에서 누락 | 필수/허용 목록 정합성 수정 |
| Word Tool이 보이지 않음 | `word_report_create`의 `report` 때문에 data-report Skill 자동 선택, Tool 필터링 | 자동 선택 Skill이 명시 Tool을 숨기면 자동 선택만 취소 |
| `todo_write` 첫 호출 실패 | GPT-OSS는 배열을 보냈지만 Tool은 JSON 문자열만 허용 | 문자열과 구조화 배열 모두 허용 |
| 출력 폴더 사전 `ls` 실패 | Skill이 새 경로 작성 순서를 명시하지 않음 | `write` 후에만 검증하도록 절차 보강 |
| Word 표 구조 경고 | 자동 표 폭, header 행 미표시 | 8520 DXA 폭, 120 DXA indent, 고정 grid/cell 폭, header 적용 |
| Word에 HTML 잔존 | Markdown HTML 미정리 | 변환 전 HTML 태그 제거 |
| GPT-OSS framing token 노출 | 최종 출력 sanitizer 누락 | `<final<|message|>` 같은 제공자 토큰 제거 |
| Docker Word 테스트 skip | 문서 패키지가 이미지에 없음 | 별도 문서 requirements를 Docker에 고정 설치 |

보안상 자동 Skill 충돌 해제는 **auto mode에만** 적용된다. 수동 Skill 정책은 그대로 엄격하며, 전역 denylist·사용자 allowlist·Plan Mode에서 제거된 Tool은 복원하지 않는다.

## 현재 수준

| 영역 | 수준 | 실사용 판단 |
| --- | --- | --- |
| 일반 답변·간단 추론 | L4 | 사용 가능 |
| 로컬 파일 탐색·단일 문서 Q&A | L4 | 사용 가능 |
| 세션 지속·다른 세션 조각 | L4 | 사용 가능, 세션 ID 필요 |
| 장기 메모리 저장·검색 | L4 | 사용 가능, stale 정보 재검증 필요 |
| 회사 Skill 기반 정책 답변·리뷰 | L4 | 사용 가능 |
| 작은 코드 수정·테스트 | L4 | 테스트가 있는 범위에서 사용 가능 |
| Markdown·구조적 DOCX | L4 | 초안·내부 보고서 가능, 시각 QA 별도 |
| 최신 뉴스 후보·URL 원장 | L3 | 조사 보조 가능 |
| 주장 단위 근거 검증·deep research | L2~L3 | 사람 검토 필수 |
| 장시간 완전 자율 업무 | L3 | 완료 게이트와 재시도 관리 필요 |

종합하면 **코딩 에이전트·워크스페이스 에이전트로는 L4 실험형**, **근거 중심 리서치 에이전트로는 L3**다.

## 다음 기능 요구사항과 우선순위

| 우선순위 | ID | 요구사항 | Acceptance Criteria |
| --- | --- | --- | --- |
| P0 | CAP-01 | 주장-근거 완료 게이트 | 핵심 주장마다 evidence ID, 원문 구절, 검증 레벨이 없으면 `complete`가 아닌 `limited/incomplete` |
| P0 | CAP-02 | 다른 세션 Tool 증거 조회 | 세션 ID로 Tool 결과 원장을 검색·조회하고 T18의 evidence ID/verification을 T20이 정확히 복원 |
| P0 | CAP-03 | 요청 충족 검사 | 요청한 `제한`, 출력 경로, 검증 명령 중 하나라도 누락되면 최종 성공 처리 금지 |
| P0 | CAP-04 | full-text 연구 기준 | 3개 독립 발행처, 2개 원문, URL 검증 전에는 정상 분석 보고서 생성 금지 |
| P1 | CAP-05 | Word 렌더 Docker profile | LibreOffice와 렌더 도구를 별도 이미지 profile에 고정하고 모든 페이지 PNG 검사 |
| P1 | CAP-06 | typed Tool result | 모든 Tool이 `ok/error/no_results/limited` 구조를 반환하고 감사 로그 판정과 일치 |
| P1 | CAP-07 | Skill 지연 로딩 | frontmatter만 색인하고 선택된 SKILL.md 본문과 필요한 reference만 로딩 |
| P1 | CAP-08 | 평가 회귀 세트 | 모델/프롬프트 변경 시 20개 Task 점수와 Tool 흐름 차이를 비교 |
| P2 | CAP-09 | 장문·다중 문서 평가 | 상충 문서, 100개 문서, 표·PDF가 섞인 근거 평가 추가 |
| P2 | CAP-10 | 주가·차트·Word | 고정 데이터 fixture로 차트 포함 DOCX 생성·렌더 검증 후 실제 공급자 연결 |

## 검증 결과

- Local 전체 회귀: `100 passed in 9.75s`
- Docker Compose 전체 회귀: `100 passed in 7.23s`
- Docker 문서 확장: `python-docx==1.2.0`, `lxml==5.3.0` 설치 및 테스트 포함
- 평가 최종 상태: 20/20 실행, 자동 점수 `98.75/100`
- 최신 Task Tool 오류: 0건
- DOCX 구조·접근성 감사: 경고 0건
- DOCX 시각 렌더: `soffice` 부재로 미완료
- Compose orphan `python_ubuntu_dev`: 경고만 확인했으며 삭제하지 않음
- `pytest-asyncio` 기본 loop scope deprecation warning: 남아 있음

## 재현 방법

20개 전체 실행:

```powershell
python scripts/run_agent_capability_20.py `
  --run-id my-gptoss-eval
```

중단 후 이어서 실행:

```powershell
python scripts/run_agent_capability_20.py `
  --run-id my-gptoss-eval `
  --resume
```

일부 Task를 새 세션에서 재검증:

```powershell
python scripts/run_agent_capability_20.py `
  --run-id my-gptoss-eval `
  --tasks 14,19-20 `
  --session-suffix fix-check
```

Docker 회귀:

```powershell
docker compose -f docker-compose.yml -f docker-compose.test.yml up `
  --build --abort-on-container-exit --exit-code-from pi_agent
```

실행 산출물:

- `artifacts/agent-capability-20/runs/<run-id>/results.json`
- `artifacts/agent-capability-20/runs/<run-id>/summary.md`
- `artifacts/agent-capability-20/runs/<run-id>/tasks/TNN.json`
- 재시도 전 결과: `tasks/attempts/`
