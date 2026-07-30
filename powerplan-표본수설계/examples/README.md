# 예제 데이터 (합성 / SYNTHETIC)

이 폴더의 CSV는 **모두 컴퓨터로 생성한 가상 데이터**입니다. 실제 환자·피험자 자료가
아니며, 어떤 실제 임상시험의 결과도 담고 있지 않습니다. `powerplan`의 사용법을
보여주기 위한 예시일 뿐입니다.

**These CSV files are entirely SYNTHETIC (computer-generated). They contain no real
patient or participant data and no results from any actual clinical study.**

| 파일 | 형태 | 무엇을 보여주려고 만들었나 |
|---|---|---|
| `serene_pilot.csv` | 34행, 쉼표 구분, UTF-8 | 두 군(device/sham) 비교 · 기저값(`isi_baseline`)과 8주 추적값(`isi_week8`) · 결측 2건(빈 칸, `NA`) |
| `wowfit_pilot.csv` | 23행, **세미콜론** 구분, 한글 열 이름 | 전후 비교(사전/사후) · 두 군이 한 파일에 섞여 있어 `--filter 군=중재`가 필요한 상황 · 결측 1건 |

숫자는 불면증 중증도 척도(ISI, 0~28)와 단어인지도(%)의 대략적인 범위를 흉내 낸
난수입니다. 해석 가능한 임상적 결론을 끌어내려는 목적이 아닙니다.

사용 예:

```bash
powerplan pilot examples/serene_pilot.csv --value isi_week8 --group arm --baseline isi_baseline --power 0.8
powerplan pilot examples/wowfit_pilot.csv --pre 훈련전_단어인지도 --post 훈련후_단어인지도 --filter 군=중재 --power 0.8
```
