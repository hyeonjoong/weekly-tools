# draftcheck — 원고 투고 점검

투고 직전 원고 파일(`.docx` / `.md` / `.tex` / `.txt`) 하나를 받아, **사람 눈으로는 전수 대조가 불가능한
기계적 정합성 오류**를 줄번호가 붙은 한국어 수정 목록으로 뱉는 오프라인 CLI.

---

## 목적 / Why this exists

**한국어.** 공저자 코멘트를 다 반영하고 저널 사이트에 업로드하기 직전 30분, 또는 리비전 원고를 다시
올리기 직전에 이런 불안이 옵니다 — "참고문헌 번호 안 밀렸나? Figure 3 본문에서 언급했나? 초록 N이랑
Table 1 N이랑 맞나?" 지금은 Ctrl+F를 서른 번 누르거나, 시간이 없어서 **그냥 안 합니다.** 그리고
리뷰어 1번 코멘트("Reference 27 is not cited in the text", "Figure 3 is never referred to")로 되돌아옵니다.
이 확인 작업은 지루하고, 전수 대조가 필요하고, 실수가 조용히 통과되고, 리비전마다 반복됩니다 —
사람이 가장 못하고 기계가 가장 잘하는 조합입니다. draftcheck는 그 대조를 **결정론적으로** 수행합니다.
LLM에게 원고를 붙여넣고 "확인해줘"라고 하는 것과 다른 점이 여기입니다: 참고문헌 60개와 본문 인용
200개의 교차 대조는 세는 일이지 추론하는 일이 아니고, 세는 일에서 조용히 지어내지 않는 쪽은 파서입니다.

**English.** In the last half hour before you upload a manuscript — or re-upload a revision — a specific
anxiety shows up: did the reference numbers shift, is Figure 3 actually referred to in the text, does the
N in the abstract match Table 1? Today that means pressing Ctrl+F thirty times, or more often skipping it
entirely, and then getting it back as Reviewer 1's first comment. The work is exhaustive, boring, silently
forgiving of mistakes, and repeated at every revision — exactly the combination humans are worst at and
machines are best at. draftcheck does that cross-check deterministically, offline, and read-only. It is
for a clinical or pharmaceutical researcher who submits and revises papers routinely and would rather
find "reference 27 does not exist" themselves than hear it from an editor.

**What it is not.** It never judges your science, your English, or your logic, and it never checks whether
a cited paper actually exists — that is [citecheck](../citecheck-인용DOI검증)'s job. citecheck asks
*"does this reference exist out in the world?"*; draftcheck asks *"is this document consistent with
itself?"* The two do not replace each other. draftcheck also exports `references.csv` in a layout citecheck
reads directly, so a submission check can flow straight into a DOI check.

---

## 설치

```bash
cd ~/Downloads/02_프로젝트/깃헙/draftcheck-원고투고점검
python3 -m pip install -e .
```

외부 의존성이 **하나도 없습니다** (`dependencies = []`). `.docx`는 표준 라이브러리 `zipfile` +
`xml.etree.ElementTree`로 직접 읽습니다. 네트워크 호출은 0건입니다.

설치하지 않고 폴더 안에서 바로 쓰려면 `python3 -m draftcheck.cli ...`, 또는 **`실행.command`를 더블클릭**하세요.

---

## 사용법

```bash
draftcheck 원고.docx                                        # 화면 요약만
draftcheck 원고.docx --limits examples/journals/sleepmed.json
draftcheck 원고.docx --out-dir 점검_20260806                 # 리포트 3종 저장
draftcheck 원고.docx --strict                               # 문제 있으면 종료 코드 ≠ 0
draftcheck 원고.docx --dump-text | less                     # 추출된 텍스트 직접 확인
```

### 실제 실행 예와 실제 출력

번들 예제 `examples/manuscript_flawed.md`는 결함을 **일부러 심어 둔** 합성 임상시험 원고입니다.

```
$ draftcheck examples/manuscript_flawed.md --limits examples/journals/sleepmed.json
draftcheck — manuscript_flawed.md  (md, 줄 번호 기준)
본문 1,068단어 / 초록 307단어 / 인용 스타일: numeric(번호형) (자동 판별)
------------------------------------------------------------------------------

■ 치명 5건
      L9  초록의 표본수 48이 본문·표 어디에서도 확인되지 않습니다 (본문/표의
              표본수: 45, 23, 22).
          → 초록과 본문·Table 1의 N을 맞추세요. 탈락자 반영 후 초록만 옛
            숫자로 남는 일이 흔합니다.
     L59  'p = 0.000' — p 값은 정확히 0이 될 수 없습니다.
          → 'p < 0.001'로 보고하세요(대부분의 저널 통계 지침).
     L73  본문 인용 [27]에 해당하는 참고문헌이 목록에 없습니다 (목록은 26개).
          → 번호가 밀렸는지 확인하거나 해당 문헌을 목록에 추가하세요.
    L106  참고문헌 26번이 본문에서 한 번도 인용되지 않았습니다 — Espie CA,
            Emsley R, Kyle SD. Durability of digital cognitive….
          → 본문에 인용을 넣거나 목록에서 빼세요(리뷰어가 가장 자주 지적하는
            항목).
    L114  그림 3이 본문에서 한 번도 언급되지 않았습니다.
          → 본문 적절한 위치에 (그림 3)을 언급하세요 — 리뷰어 단골 지적입니다.

■ 경고 7건
       —  p 값 표기가 섞여 있습니다 (앞자리 0을 쓴 것과 안 쓴 것이 함께 있음:
               예 'p = 0.030' vs 'p = .03').
          → 저널 지침에 맞춰 하나로 통일하세요 (APA는 앞자리 0 없이 'p =
            .03').
      L3  초록 단어수 307 — 한도 250 (57 초과).
          → 투고 시스템에서 자동 반려되는 항목입니다. 먼저 줄이세요.
     L11  결과 문장에 'p < 0.05'처럼 임계값만 있습니다.
          → 정확한 p 값을 소수 셋째 자리까지 보고하세요 (0.001 미만이면 'p <
            0.001').
     L19  본문에서 약어 'ISI'가 정의보다 먼저 사용되었습니다 (첫 정의는 줄
             33).
          → 첫 등장 위치에서 풀어 쓰고 괄호로 약어를 정의하세요.
     L59  p 값만 있고 효과크기나 95% CI가 없습니다: 'Sleep efficiency also
             differed between the arms (p = 0.041).…'
          → 효과크기(d, g, η², OR 등)와 95% 신뢰구간을 함께 보고하세요.
     L69  [17]이 [18]보다 뒤에 처음 등장합니다 — 번호형 스타일은 본문 첫 등장
             순서대로 번호를 붙여야 합니다.
          → 인용 번호를 다시 매기거나(EndNote는 자동) 순서를 확인하세요.
    L118  표 번호가 건너뜁니다: 2 (캡션은 1, 3).
          → 번호를 연속으로 다시 매기세요.

■ 분량 (Sleep Medicine (예시 프로파일) 기준)
  제목 문자수             113 / 한도 120     ✓
  초록 단어수             307 / 한도 250     ✗ 57 초과
  본문 단어수           1,068 / 한도 4,000   ✓
  참고문헌 개수             26 / 한도 50      ✓
  그림+표 개수              5 / 한도 6       ✓

■ 이 점검이 실제로 본 것 (자기 보고)
  · 본문에서 번호형 인용 표기 26개(고유 26개)를 인식했고, 참고문헌 목록에서
    항목 26개를 읽었습니다. (줄 번호 기준)

------------------------------------------------------------------------------
치명 5 · 경고 7 · 정보 0
```

같은 원고의 **깨끗한 대조본**을 돌리면 이렇게 나옵니다. 여기서 치명이 하나라도 나오면 이 툴은 소음이므로,
그 상태를 테스트로 고정해 두었습니다:

```
$ draftcheck examples/manuscript_clean.md --limits examples/journals/sleepmed.json --quiet
manuscript_clean.md: 치명 0 · 경고 0 · 정보 1
```

### 인식하지 못하면 '이상 없음'이 아니라 '점검 불가'

조용히 통과시키는 체커는 없느니만 못합니다. 인용 표기를 하나도 못 찾았거나 참고문헌 목록을 못 찾으면
결과가 이렇게 바뀝니다(`--strict`에서는 종료 코드 3):

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃★ 점검 불가 — 아래 항목은 '이상 없음'이 아닙니다                          ┃
┃  참고문헌 섹션(References / 참고문헌 제목)을 찾지 못했습니다 — 인용 교차  ┃
┃  대조를 수행하지 못했습니다.                                             ┃
┃  → 해당 부분은 반드시 눈으로 확인하세요.                                 ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

### 출력 파일 (`--out-dir`)

| 파일 | 내용 |
|---|---|
| `점검결과.md` | 항목별 근거와 줄번호가 붙은 한국어 리포트 — 공저자에게 그대로 전달 가능 |
| `문제목록.csv` | `줄번호, 심각도, 유형, 대상, 설명, 권고` — 엑셀에서 정렬·필터 |
| `references.csv` | `Study ID, Authors, Year, Title, Journal, Article DOI, PMID, parse_ok` — **citecheck가 그대로 읽는 형식** (진단용 `parse_ok` 열이 하나 더 있고, citecheck는 모르는 열을 무시합니다) |

```bash
draftcheck 원고.docx --out-dir 점검_20260806
citecheck 점검_20260806/references.csv     # 이어서 DOI 실존·철회 검증 (네트워크 필요)
```

---

## 점검 항목 7종

| # | 항목 | 대표 검출 | 등급 |
|---|---|---|---|
| 1 | **인용 ↔ 참고문헌** | 목록에 없는 인용 번호, 한 번도 인용되지 않은 문헌, 첫 등장 순서 역전, 목록 번호 중복·건너뜀 | 치명/경고 |
| 2 | **그림·표 번호** | 본문 미언급 그림/표, 캡션 없는 번호 언급, 번호 건너뜀, 언급 순서 역전 | 치명/경고 |
| 3 | **표본수(N) 일관성** | 초록의 N이 본문·표 어디에서도 확인되지 않음 | 치명 |
| 4 | **통계 보고** | `p = 0.000`, p > 1, 결과 문장에 임계값(`p < 0.05`)만, 효과크기·CI 없는 p값, 앞자리 0 표기 혼재 | 치명/경고 |
| 5 | **약어** | 정의 전 사용, 끝까지 정의 없음, 같은 섹션 안 재정의, 정의만 하고 미사용 | 경고/정보 |
| 6 | **분량** | 제목 문자수·초록/본문 단어수·참고문헌 수·그림표 수 ↔ `--limits` JSON | 경고 |
| 7 | **인식 실패 보고** | 인용 0개 또는 참고문헌 목록 미발견 → **'점검 불가'** (조용히 통과 금지) | 치명 |

### 단어 수 세기 규칙

저널 한도와 맞춰 보려면 세는 규칙이 명시돼 있어야 합니다. draftcheck는 이렇게 셉니다
(테스트가 손으로 센 값과 대조합니다):

- 공백으로 나눈 뒤 양끝 문장부호를 떼고, **영숫자나 한글이 하나라도 있으면 1단어**
- **번호형 인용 `[3]`, `[3-5]`, `[1,4-6]`은 세지 않습니다**
- 하이픈어(`non-contact`) · 숫자(`48`) · `p<0.05` 는 각각 **1단어**
- **한글은 띄어쓰기 단위(어절)로 1단어** (`참가자 45명이` = 2단어)
- 마크다운 표시(`#`, `**`, `|`)와 LaTeX 명령은 글자로 세지 않고, 명령의 인자만 셉니다
- 초록 단어수: 초록 섹션에서 `Keywords…` 줄을 뺀 것
- 본문 단어수: 초록 다음부터 참고문헌 전까지에서 **캡션 줄과 표 내용을 뺀 것**(소제목은 포함)

---

## 한계 / Notes

- **`.docx`는 1급 지원입니다.** 추적 변경으로 삭제된 글(`w:del`)은 제외하고, EndNote/Word 필드 인용은
  화면에 보이는 결과 텍스트만 읽습니다(필드 코드 안의 숫자를 인용으로 세지 않습니다). 표는 실제
  `w:tbl` 셀 단위로 읽습니다. 다만 `.docx`에서 `L숫자`는 **문단 번호**이지 Word 화면의 줄 번호가 아닙니다.
- **인용 스타일은 번호형(밴쿠버)이 1급, 저자-연도가 2급입니다.** 저자-연도는 표기 변형이 끝없어서
  등급을 한 단계 낮춰 보고하고, "인용 N개 중 M개가 목록과 매칭"이라는 **자기 커버리지를 스스로 출력**합니다.
  그 숫자가 낮으면 결과를 믿지 마세요. LaTeX `\cite{}`/`\bibitem{}` 키 대조도 지원합니다.
- **표본수 점검(항목 3)은 일부러 좁게 만들었습니다.** `N = 48`, `48 participants`, `참가자 48명` 같은
  명시적 라벨만 보고, "초록의 값이 본문·표 어디에도 없다"는 한 방향만 치명으로 봅니다. 군별 N(23/22)은
  총 N의 부분집합이므로 걸리지 않습니다. 거짓 양성 하나면 이 툴은 두 번 다시 안 열리기 때문입니다.
- **원고를 고쳐 주지 않습니다.** 읽기 전용이며 원본 파일을 절대 수정하지 않습니다. 출력은 `--out-dir`에만
  만들어지고 파일 이름은 고정 3개입니다.
- **저널 한도 DB를 내장하지 않습니다**(금방 낡습니다). `examples/journals/`의 예시 프로파일은 참고용이며,
  실제 한도는 저널 홈페이지에서 확인해 `--limits` JSON으로 주세요.
- **하지 않는 것:** 영문 교정·문법, 과학적 타당성 판단, DOI 실존 검증(→ citecheck), 참고문헌 스타일 변환,
  저널 템플릿 서식 적용, 표절 검사, PDF 입력.
- **구조화 초록의 소제목이 섹션 제목과 겹치면** 초록 경계를 잘못 잡을 수 있습니다. 그럴 때 조용히
  '초록 0단어'로 넘어가지 않고 "초록 제목은 찾았지만 내용을 읽지 못했습니다"라고 알린 뒤 관련 점검을
  건너뜁니다. 제목에 메모가 붙어 있어도(`References (정리 필요)`) 인식합니다.
- 번들 예제 원고는 **전부 합성**입니다. 실제 환자 데이터나 실제 원고가 아닙니다.
  (`examples/_make_examples.py` 가 어떤 결함을 어디에 심었는지 그대로 보여 줍니다.)

## 종료 코드

| 코드 | 뜻 |
|---|---|
| 0 | 문제 없음 (또는 `--strict` 없이 실행) |
| 1 | `--strict` 이고 치명/경고가 있음 |
| 2 | 사용법·입출력 오류 |
| 3 | `--strict` 이고 **점검 불가** (인용 0개 / 참고문헌 목록 없음) |

## 테스트

```bash
python3 -m pytest -q      # 274개, 전부 오프라인
```

심어 둔 결함 하나하나에 개별 테스트가 붙어 있고(`tests/test_planted_defects.py`),
깨끗한 대조본에서 **치명 0건·경고 0건**이 나오는 것도 `.md`/`.docx`/`.txt`/저자-연도/`\cite` 판마다
테스트로 고정돼 있습니다. `tests/test_hardening.py` 는 적대적 검토에서 실제로 잡힌 결함들의
회귀 테스트입니다(`HARDENING.md` 참고).

## 라이선스

MIT © 2026 hyeonjoong
