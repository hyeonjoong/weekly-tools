# weekly-tools — 주간 자동 제작 툴 모음

매주 드문 드문 유용한 툴을 하나씩 만들어 이 저장소에 하위 폴더로 추가·커밋합니다.
같은 폴더가 내 컴퓨터(`~/Downloads/02_프로젝트/깃헙/`)에 그대로 쌓여 바로 실행할 수 있습니다.

- 폴더명은 `이름-역할` 규칙 — 한눈에 뭐하는 툴인지 보입니다.
- 각 폴더의 `실행.command`를 더블클릭하면 바로 동작을 볼 수 있고, `사용법.md`에 한글 안내가 있습니다.

## 설치된 툴

| 툴 (폴더) | 한 줄 설명 | 바로 실행 | 추가일 |
|----------|-----------|----------|--------|
| [factorscan-설문요인분석](factorscan-설문요인분석) | 설문 척도 CSV → 요인분석 적합성(KMO·Bartlett)·요인 수(고유값/Kaiser/평행분석)·요인적재량(Varimax)·공통성·수정된 문항-총점 상관을 한 번에 진단 (numpy만; SPSS식 척도 타당도 표를 재현가능하게) | `실행.command` 더블클릭 · 또는 `python3 -m factorscan.cli 설문.csv --config 설정.json` | 2026-07-02 (목) |
| [logflow-사용자로그분석](logflow-사용자로그분석) | 사용자 이벤트 로그 CSV → 세션화·이벤트/사용자별 집계·DAU/WAU/MAU·리텐션(코호트 day-N)·퍼널 전환율을 한 번에 요약 (표준 라이브러리만, tz 보정·결측 처리 지원) | `실행.command` 더블클릭 · 또는 `python3 -m logflow.cli 로그.csv --funnel 단계1,단계2` | 2026-06-29 (월) |
| [surveyscan-설문응답분석](surveyscan-설문응답분석) | 설문 응답 CSV → 문항별 기술통계·결측 요약·역문항 자동 재코딩·하위척도 점수·Cronbach α(신뢰도)·문항-총점 상관·문항제거시 α (표준 라이브러리만) | `실행.command` 더블클릭 · 또는 `surveyscan 설문.csv -c 설정.json` | 2026-06-26 (금) |
| [paperforge-논문아이디어발굴기](paperforge-논문아이디어발굴기) | 보유 데이터(EEG·워치·호흡·설문·MoA) 매니페스트에서 멀티모달 논문 아이디어 매트릭스 생성 — 가설·변수·분석법·저널·표본 실현가능성 | `실행.command` 더블클릭 · 또는 `paperforge manifest.json` | 2026-06-25 (목) |
| [citecheck-인용DOI검증](citecheck-인용DOI검증) | 원고 인용/DOI를 Crossref로 검증 — 깨진 DOI·메타데이터 불일치·철회 탐지 | `실행.command` 더블클릭 · 또는 `citecheck refs.bib` | 2026-06-25 (목) |

> `citecheck`는 단독 저장소([github.com/hyeonjoong/citecheck](https://github.com/hyeonjoong/citecheck))로도 공개돼 있습니다. 이후 만드는 툴은 이 모노레포 하위 폴더로 쌓입니다.

## 새 툴 바로 쓰는 법

각 툴 폴더에서 한 번만 설치하면 명령어가 전역으로 등록됩니다:

```bash
cd ~/Downloads/02_프로젝트/깃헙/<폴더이름>
python3 -m pip install -e .
```

또는 그냥 폴더 안의 **`실행.command`를 더블클릭**하세요.

## 최신 상태로 당기기

자동 실행이 새 툴을 푸시하므로, 가끔 최신으로 맞추려면:

```bash
cd ~/Downloads/02_프로젝트/깃헙
git pull
```
