# revcheck — 리비전 응답 점검

**제출본 · 개정본 · 응답서(point-by-point response) 세 파일을 동시에 읽어, 응답서에 적은 약속이 개정 원고에 실제로 반영됐는지 기계적으로 대조하는 오프라인 CLI.** 답하지 않은 리뷰어 코멘트, 개정본에 없는 인용 문구, 아무에게도 말하지 않고 바뀐 숫자를 찾아냅니다.

---

## 목적 / Why this exists

리비전 마감 D-1 밤 11시. 리뷰어 3명 × 코멘트 8개 + 에디터 2개 = 26건. 응답서에는 "We have revised this sentence (p.7, lines 210–214)" 같은 문장이 스무 군데 들어가 있고, 그중 절반은 인용부호 안에 **개정 후 문구를 그대로 붙여넣어** 두었습니다. 그런데 그 뒤로 공저자 코멘트를 반영해 원고를 두 번 더 고쳤습니다. 응답서에 인용해 둔 문구가 지금 원고에 아직 그대로 있는지, 아무도 모릅니다. 여기서 나는 사고는 전부 조용합니다 — 응답서는 매끄럽게 읽히고, 투고 시스템은 아무 말 없이 접수하며, 문제는 **리뷰어가 응답서를 손에 들고 원고를 열었을 때** 처음 드러납니다. 이 툴은 세 문서를 동시에 들고 26건 × 5가지를 전수 대조하는, 새벽 1시의 사람이 절대 못 하고 기계는 완벽하게 하는 일만 합니다. 임상시험 원고를 쓰는 연구자가 **투고 버튼을 누르기 직전 30초**에 씁니다.

It is 11 p.m. the night before the revision deadline. Twenty-six reviewer comments, twenty "we have revised…" claims in the response letter, half of them quoting the revised wording verbatim — and the manuscript has been edited twice more since those quotes were pasted. Nobody knows whether the quoted sentences still exist. Every failure mode here is silent: the response letter reads well, the submission system accepts the files, and the mismatch only surfaces when a reviewer opens the manuscript with your response letter in hand and finds that comment 2-5 was never answered, that the sentence you quoted is not there, or that a Results number changed without being declared. `revcheck` reads the original submission, the revised manuscript and the point-by-point response together and checks, exhaustively, what a human cannot check at 1 a.m. It is offline, reads your files read-only, and never guesses.

### 이 저장소의 원고 4종 세트에서 revcheck 의 자리

| 툴 | 보는 것 | 문서 수 | 언제 |
|---|---|---|---|
| [numcheck](../numcheck-원고수치검증) | 원고 안 **숫자의 산술**이 맞는가 (비율·p값·GRIM·CI) | 1개 | 투고 직전 |
| [draftcheck](../draftcheck-원고투고점검) | 원고가 **자기 안에서** 앞뒤가 맞는가 (인용↔참고문헌, 그림표 번호, 약어, 분량) | 1개 | 투고 직전 |
| [citecheck](../citecheck-인용DOI검증) | 참고문헌이 **실존**하는가 (DOI·철회) | 1개(+네트워크) | 투고 직전 |
| **revcheck (이 툴)** | **응답서의 약속과 개정본의 실제가 일치하는가** | **3개** | **리비전 직전** |

revcheck 는 개정본 하나만 놓고 하는 검사는 **하지 않습니다.** 숫자는 numcheck, 형식은 draftcheck, DOI 는 citecheck 의 몫이고, 리포트 끝에서 그렇게 안내합니다. 반대로 그 셋은 두 번째 파일을 받지 않으므로 diff 를 할 수 없습니다.

### 무엇을 잡아내나

1. **리뷰어 코멘트 번호 전수 점검** — 2-4 다음이 2-6이면 **치명**. 사람은 "없는 번호"를 못 봅니다. 중복 번호, 응답 본문이 30자도 안 되는 블록도 함께 셉니다.
2. **인용 문구 실존 검증(이 툴의 존재 이유)** — 응답서가 "다음과 같이 고쳤습니다"라며 인용한 문구가 개정본에 **문자 그대로** 있는가. 없으면 치명, 가장 가까운 문장을 일치율과 함께 나란히 보여 줍니다. **숫자가 다르면 일치율이 99%여도 치명**입니다.
3. **미신고 변경** — 응답서의 인용문·위치 참조로 연결되지 않은 채 바뀐 문단. **있던 문단의 숫자가 달라졌으면 치명**입니다(말없이 바뀐 숫자가 리뷰어에게 가장 나쁘게 읽힙니다). 문단이 통째로 추가·삭제된 것은 리뷰어 눈에도 보이므로 경고/정보입니다.
4. **제출본 오첨부 사고** — 본문도 참고문헌도 제출본과 완전히 같은데 "고쳤다"는 주장이 있으면 치명.
5. **검증 불가한 변경 주장** — "we have added"라고만 쓰고 인용문도 위치도 없는 응답을 따로 셉니다.
6. **위치 참조** — `lines 210–214` 가 범위를 벗어나거나 바뀌지 않은 곳을 가리키면 경고. `.docx` 는 줄 번호가 파일에 없으므로 **확인불가로 표시**하고 건수만 [정보]와 커버리지에 적습니다 — 등급을 매기지 않으므로 종료코드에도 영향을 주지 않지만, 결코 '이상 없음'으로 표시하지 않습니다.
7. **참고문헌·그림·표 증감** — "새 참고문헌 3편 추가"라고 썼는데 실제로 2편이면 경고. 추가된 문헌은 `추가문헌.csv`(citecheck 입력 스키마)로 나갑니다.

---

## 설치

```bash
cd ~/Downloads/02_프로젝트/깃헙/revcheck-리비전응답점검
python3 -m pip install -e .        # 외부 의존성 없음 (Python 3.9+)
```

설치하지 않고도 됩니다: 이 폴더에서 `python3 -m revcheck.cli ...` 로 바로 실행하거나, **`실행.command` 를 더블클릭**하면 번들 예제로 무엇을 잡는지 바로 보여 줍니다.

---

## 사용법

```bash
revcheck --old 제출본.docx --new 개정본.docx --response 응답서.docx --out-dir 결과
```

지원 형식은 `.docx` / `.md`(`.markdown`) / `.tex` / `.txt` 이며 세 자리 모두 서로 다른 형식이어도 됩니다. PDF 는 읽지 않습니다.

### 실제 출력 (번들 예제 `examples/flawed`)

```
$ revcheck --old examples/flawed/제출본.md --new examples/flawed/개정본.md \
           --response examples/flawed/응답서.md --out-dir 결과

revcheck — 리비전 응답 점검
읽기: 제출본.md(줄 60) / 개정본.md(줄 62) / 응답서.md

[치명 3건]
1. 코멘트 2-3 에 대한 응답 블록이 없습니다. (응답서에서 2-2 다음이 2-4 입니다)
   → 응답을 추가하거나, 번호 체계가 저널 양식과 다른지 확인하세요.
2. 응답 1-1 의 인용 문구가 개정본의 해당 문장과 「숫자가 다릅니다」.
   응답서: "Assuming a between-group difference of 3.0 points on the ISI with an SD of 4.5, 42 participants per arm provide 80% power at a two-sided alpha of 0.05."
   개정본에서 가장 가까운 문장(일치율 99%, 문단 19):
           "Assuming a between-group difference of 3.0 points on the ISI with an SD of 4.5, 45 participants per arm provide 80% power at a two-sided alpha of 0.05."
   → 응답서와 원고 중 어느 쪽이 최신인지 확인하세요.
3. 응답서의 인용문·위치 참조로 연결되지 않은 수정입니다 — 숫자가 다른 값으로 바뀌었습니다.
   제출본: The mean ISI decreased by 5.2 points (SD 3.1) in the active arm and by 2.1 points (SD 2.8) in the sham arm, giving a bet … ints (95% CI 1.6 to 4.6).
   개정본: The mean ISI decreased by 5.8 points (SD 3.4) in the active arm and by 2.1 points (SD 2.8) in the sham arm, giving a bet … ints (95% CI 2.1 to 5.3).
   숫자: 5.2, 3.1, 2.1, 2.8, 3.1, 95, 1.6, 4.6 → 5.8, 3.4, 2.1, 2.8, 3.7, 95, 2.1, 5.3
   → 재분석했거나 값을 고쳤다면 응답서에 한 줄로 밝히세요. 말없이 바뀐 숫자는 리뷰어에게 가장 나쁘게 읽힙니다.

[경고 4건]
4. 응답 2-2 의 변경 주장을 기계로 확인할 수단이 없습니다(인용 문구도, 위치 참조도 없음).
   응답: We have revised the masking paragraph accordingly and now describe the acoustic matching p … as used during the trial.
   → 고친 문장을 한 줄 인용하거나 위치를 적어 두면 리뷰어가 바로 확인합니다.
5. 응답 3-1 의 변경 주장을 기계로 확인할 수단이 없습니다(인용 문구도, 위치 참조도 없음).
   응답: We have added three new references that bear on this point (Jung 2022; Oh 2023) and have c … he limitations paragraph.
   → 고친 문장을 한 줄 인용하거나 위치를 적어 두면 리뷰어가 바로 확인합니다.
6. 응답서는 참고문헌 3편 추가라고 했으나 실제 증가는 2편입니다.
   추가된 문헌: Jung E, Han K. Masking integrity in devi … :10.1000/trials.2022.0077, Oh J, Ryu D. Urban-rural differences in … doi:10.1000/sh.2023.0412
   → 추가문헌.csv 를 확인하고, 빠진 문헌이 있으면 넣으세요.
7. 응답 2-1 의 위치 참조가 개정본 범위를 벗어납니다 — 'p. 7, lines 210-214' (개정본은 총 62줄입니다)

[정보]
- 본문 단어수 424 → 523 (+99), 참고문헌 3 → 5, Figure 0 → 0, Table 0 → 1
- 새로 등장한 번호: Table 2
- [Randomisation and masking (개정본 문단 15)] 응답서의 인용문·위치 참조로 연결되지 않은 수정입니다.
    제출본: (이 문장 없음 — 새로 넣은 문장)
    개정본: The sham unit was acoustically matched to the active unit in loudness and spectral profile, and neither participants nor … which unit they handled.

[커버리지 자백]
- 리뷰어 코멘트 식별: 6건 모두 확인했습니다 — R1 2건 / R2 3건 / R3 1건 — 번호 체계: Comment N-M (응답서에 아예 안 적힌 마지막 번호는 알 수 없습니다)
- 응답 본문: 6건 모두 읽었습니다
- 인용 문구 대조: 4건 모두 확인했습니다
- 위치 참조 검증: 1건 모두 확인했습니다
- 변경 문단 6건을 모두 대조했습니다. 이 중 응답서에 연결되지 않은 것이 2건(있던 값이 바뀐 것 1건)이고, 그중 2건을 위에 개별로 실었습니다. 문단 변경률 22%.
- 기계로 확인할 수 없는 변경 주장 2건 — 사람이 직접 봐야 합니다

※ 개정본 자체의 숫자·인용↔참고문헌·그림표 번호는 검사하지 않았습니다. numcheck 와 draftcheck 를 따로 돌리세요. 새로 추가된 문헌의 DOI 는 추가문헌.csv 를 citecheck 에 넣으면 확인됩니다.

종료코드 1 (치명 있음)
결과/리비전점검.md, 문제목록.csv, 변경목록.csv, 추가문헌.csv 저장
```

`examples/clean` 은 응답서대로 정확히 개정한 세트이고 **치명 0건 · 경고 0건 · 종료코드 0** 이 나옵니다. 오탐이 나지 않는지 매 실행마다 테스트가 지킵니다.

### 변경내용 추적이 켜진 .docx

```
$ revcheck --old examples/docx/제출본.docx --new examples/docx/개정본.docx \
           --response examples/docx/응답서.docx

읽기: 제출본.docx(문단 31) / 개정본.docx(문단 37) / 응답서.docx
      ※ 개정본에 변경내용 추적 흔적이 있습니다(삽입 7 / 삭제 0) — 모두 수락된 상태로 읽었습니다.
```

어느 상태로 읽었는지 **반드시** 첫머리에 밝힙니다. `--tracked reject` 를 주면 변경을 모두 거절한 원본 기준으로 읽고 그 사실을 그대로 적습니다.

### 응답서 형식이 특이해 번호를 못 잡을 때

번호 체계를 못 잡으면 이 툴은 추측하지 않고 **종료코드 3(판정불가)** 으로 멈춥니다. 그때는 번호를 직접 알려 주세요.

```bash
revcheck --old old.docx --new new.docx --response resp.docx --comments 1-1,1-2,1-3,2-1,E-1
```

### 산출물과 종료코드

| 파일 | 내용 |
|---|---|
| `리비전점검.md` | 전체 리포트 (커버리지 자백 포함) |
| `문제목록.csv` | 등급·유형·대상·설명·상세·권고 |
| `변경목록.csv` | 문단 단위 diff 전체 (절·유형·숫자변경·신고여부·신고근거 열 포함) |
| `추가문헌.csv` | 새로 추가된 참고문헌 — **citecheck 입력 스키마 그대로** (`citecheck 추가문헌.csv`) |

| 종료코드 | 뜻 |
|---|---|
| 0 | 정상 (치명·경고 없음) |
| 1 | 치명 있음 |
| 2 | 경고만 있음 |
| 3 | **판정불가** — 코멘트 번호 체계를 못 잡았거나 파일을 못 읽음. 아무것도 '이상 없음'으로 표시하지 않았다는 뜻 |

### 주요 옵션

| 옵션 | 뜻 |
|---|---|
| `--tracked accept\|reject` | 변경내용 추적 .docx 를 수락본/원본 중 어느 상태로 읽을지 (기본 accept) |
| `--comments 1-1,1-2,…` | 코멘트 번호를 직접 지정 (자동 인식이 안 될 때) |
| `--ratio 0.80` | 인용문을 '표현만 다름'으로 볼 최소 일치율 |
| `--min-quote-chars 15` | 이보다 짧은 인용은 우연 일치가 많아 검사에서 제외 |
| `--quiet` | 요약 몇 줄만 출력 |

---

## 한계 — 이 툴이 하지 않는 것 (그리고 왜)

**정직하게 지는 것이 지어내는 것보다 낫다**는 원칙으로 만들었습니다. 아래는 전부 의도적으로 하지 않는 일입니다.

- **응답의 내용적 타당성을 판단하지 않습니다.** 리뷰어가 만족할지, 반박이 설득력 있는지는 사람의 일입니다. 이 툴은 "약속한 것이 실제로 있는가"만 봅니다.
- **의미 기반 매칭을 하지 않습니다.** "비슷한 문장"을 찾아 주지 않습니다. 정규화 후 문자열 일치와 difflib 비율만 씁니다. 유사도로 판정을 시작하는 순간 이 툴은 LLM 의 열등한 복제품이 됩니다. (거꾸로 말하면, LLM 에 세 파일을 붙여넣는 방식으로는 "문자 그대로 있는가"와 "번호가 전수로 다 있는가"를 확인할 수 없습니다. 그게 이 툴이 따로 있는 이유입니다.)
- **`.docx` 의 위치 참조는 검증하지 못합니다.** 워드가 보여 주는 줄 번호는 글꼴·여백·용지에 따라 달라지는 렌더링 결과일 뿐, 파일 안에 존재하지 않습니다. 그래서 건수만 세어 '확인불가'로 보고합니다 — 실무 원고 대부분이 .docx 이므로, 이 항목은 사실상 `.md`/`.tex`/`.txt` 전용입니다. 페이지 참조는 어느 형식에서도 확인할 수 없습니다.
- **PDF 를 읽지 않습니다.** 표준 라이브러리만으로 만든 반쪽짜리 PDF 파서는 커버리지를 거짓말로 만듭니다. 워드/텍스트 원본을 쓰세요.
- **개정본 자체의 정합성을 검사하지 않습니다.** 숫자→numcheck, 인용↔참고문헌·그림표 번호·약어→draftcheck, DOI→citecheck.
- **응답서를 써 주지 않고, 변경내용 추적 마크업을 만들지도 않습니다.** 읽기만 하고 쓰지 않습니다.
- **영문 교정·문법·표절 검사를 하지 않습니다.**
- **네트워크를 쓰지 않습니다.** 호출 0건이며, 테스트가 소스에 `socket`/`urllib`/`http` import 가 없음을 정적으로 검증합니다. 미공개 원고가 밖으로 나가지 않습니다.
- **원본 파일을 절대 수정하지 않습니다.** 읽기 모드로만 엽니다. 출력 위치가 입력 파일과 같거나 심볼릭 링크면 거부합니다.

### 오탐을 줄이려고 일부러 좁힌 규칙들

- 15자 미만 인용, `[...]`·`…` 로 줄인 인용, 응답서 표에서 **셀 경계를 걸친** 인용, 따옴표 짝이 맞지 않는 블록의 인용은 **검사하지 않고 그 사실을 커버리지 자백에 적습니다**(두 칸짜리 표로 쓴 응답서의 셀 **안** 인용은 그대로 대조합니다 — 저널 양식으로 흔합니다).
- 인용문이 개정본에 없고 **제출본에 그대로 있으면** 치명이 아니라 경고입니다 — 개정 전 문장을 인용했을 수 있기 때문입니다. 다만 조용히 넘기지는 않습니다.
- 응답 본문이 리뷰어의 말을 되풀이한 인용(`the reviewer asks for "..."`)은 개정 후 문구가 아니므로 검사하지 않습니다.
- 인용문은 코멘트 원문이 아니라 **응답 본문에서만** 뽑습니다(리뷰어가 인용한 원문을 치명으로 잡지 않기 위해서).
- 문단이나 표가 **통째로 들어오거나 빠진 것**은 Results·Abstract 안이면 경고, 그 밖에서는 정보입니다(리뷰어 눈에도 보입니다). 다만 Results·Abstract 문단이 숫자를 안은 채 삭제되면 치명입니다. 위험한 것은 **있던 값이 다른 값으로 바뀐** 경우이고 그건 치명 그대로입니다.
- 숫자가 **덧붙기만** 한 것(리뷰어 요청으로 한계 문장을 새로 넣음)은 값이 바뀐 것이 아니므로 치명이 아닙니다.
- 응답서가 인용을 줄여 쓴 경우(`(SD 3.1)` 를 빼고 인용)는 어긋난 숫자가 없으면 경고(`인용축약`)입니다.
- 문단 변경률이 30% 를 넘으면 숫자 변경 문단만 개별로 나열하고 나머지는 절별 건수 요약으로 줄입니다. 60% 를 넘으면 전면 재작성으로 보고 목록을 한 줄로 강등하며, **그 사실을 리포트에 명시합니다.** 전체 목록은 항상 `변경목록.csv` 에 있습니다.

### 알려진 취약점

- **응답서에 아예 적히지 않은 '마지막 번호'는 알 수 없습니다.** 리뷰어 2의 코멘트가 4개인데 응답서에 1~3만 있으면, 이 툴은 4번이 존재한다는 사실 자체를 모릅니다(가운데 구멍과 1번부터 시작하지 않는 경우는 잡습니다). 리뷰어별 코멘트 수를 알고 있다면 `--comments` 로 알려 주세요 — 그러면 전수로 확인합니다.
- 코멘트 번호가 전혀 없는(산문형) 응답서·블록은 자동 인식이 안 됩니다. 블록을 찾았지만 번호가 없으면 경고로 알려 주고, `--comments` 로 지정하면 점검합니다.
- **숫자의 순서만 바뀐 경우**(`30 … 45` → `45 … 30`)는 숫자 변경으로 잡지 않습니다. 절을 옮겨 쓴 정상 리비전과 구분할 수 없어 오탐이 더 비싸기 때문입니다.
- 인용 번호(`[5]` → `[6]`)와 그림/표 번호는 데이터 숫자로 세지 않습니다. 참고문헌을 한 편 넣으면 뒤 번호가 전부 밀리는 것이 정상이기 때문입니다.
- 문단을 크게 재배열(이동)하면 diff 가 '삭제 + 추가'로 볼 수 있습니다. 신고된 변경에 흡수되지 않으면 미신고 변경으로 잡힐 수 있습니다.
- `.docx` 의 각주·미주는 비교하지 않습니다(있으면 그 사실을 리포트에 적습니다).
- `--tracked reject` 는 변경을 모두 되돌린 상태로 읽으므로, '개정 후 문구'가 개정본에 없는 것이 **정상**입니다. 그 모드에서 인용 불일치가 무더기로 나오는 것은 오탐이 아니라 모드의 뜻입니다.

---

## 테스트

```bash
python3 -m pytest -q      # 완전 오프라인
```

적대적 서브에이전트 검토 기록은 [`HARDENING.md`](HARDENING.md) 에 있습니다.

## 라이선스

MIT © 2026 hyeonjoong
