# HARDENING — stalecheck 적대적 검토 기록

이 툴은 사람의 리뷰 없이 배포됩니다. 그래서 만들면서 막은 것과, 만든 뒤 적대적
서브에이전트가 찾아낸 것을 전부 여기에 적습니다.

---

## 0. 빌드 중에 설계로 막은 것 (2026-09-18)

기획서가 "이 셋 중 하나라도 없으면 만들지 않는 편이 낫다"고 못 박은 세 가지를
먼저 구현하고, 나머지를 그 위에 얹었습니다.

| 위험 | 어떻게 막았나 | 고정한 테스트 |
|---|---|---|
| **매번 우는 체커가 된다** (1순위 위험) | 검출기보다 **정규화를 먼저** 구현하고, 사용자의 실제 논문 트리에서 원시 불일치 49쌍 중 **11쌍(전부 PDF)** 이 조용해지는지부터 확인했습니다 | `test_realtree.py::test_normalization_silences_exactly_11`, `::test_all_11_normalized_false_positives_are_pdf`, `::test_the_11_normalized_pairs_differ_only_in_metadata` |
| **mtime 추측으로 방향을 말한다** | `verdicts.judge()` 에 "애매하면 최신 쪽" 경로가 **없습니다**. 2초 허용폭 밖에서만 방향을 말하고, 그 밖에는 `[판정불가]`. 불일치 사실 자체는 mtime 없이도 보고됩니다 | `test_verdicts.py` 전체(33건), 특히 `::test_no_guessing_branch_in_source` 가 `judge()` 의 `return Verdict` 개수와 `abs(` 부재를 소스에서 확인 |
| **일치를 쏟아내 사람이 읽기를 포기한다** | 일치는 **한 줄**, 치명은 콘솔 **최대 5건** + "전체는 CSV" | `test_report.py::test_matches_are_one_line`, `::test_many_matches_keep_report_short`, `::test_critical_console_capped_at_five` |
| **아카이브를 작업본으로 세어 자기 자신과 짝짓는다** (기획서가 실측 중 한 번 냈던 오류) | `_이전`·`superseded`·`이전버전`·`_archive`·`백업` 등을 기본 제외하고 **제외 목록을 자백에 인쇄**. 더 중요한 것: **작업 폴더 안의 다른 봉투**(`review/_repro/submission/`)도 작업본으로 세지 않습니다 — 이걸 놓치면 봉투끼리 짝지어져 유령 불일치 11쌍이 생깁니다(실제 트리에서 확인) | `test_scanning.py::test_nested_package_dir_is_not_counted_as_work`, `test_cli.py::test_include_archives_makes_pairing_ambiguous` |
| **NFD 한글 파일명이 통째로 안 짝지어지고 그 사실이 조용히 넘어간다** | 파일명 비교 전에 항상 `unicodedata.normalize("NFC", ...)` + casefold | `test_scanning.py::test_nfd_and_nfc_filenames_produce_same_name_key`, `test_pairing.py::test_nfd_and_nfc_names_pair` |
| **입력을 고친다** | `shutil` 미import · 파괴적 `os` 호출 부재 · `writer.py` 밖 쓰기 모드 `open` 부재를 **AST로 강제**. `os.walk(followlinks=False)` 도 AST로 강제 | `test_safety_ast.py` (129건), `test_cli.py::test_input_tree_unchanged_after_run`, `::test_input_tree_unchanged_on_refusal`, `test_realtree.py::test_real_tree_run_does_not_modify_inputs` |
| **--out-dir 에 심어 둔 링크로 입력을 덮어쓴다** (visitaudit·calmbark·circadia 가 실제로 출고했던 결함) | 산출물 5종 각각에 대해 `os.path.islink` + `st_nlink > 1` + `O_NOFOLLOW` 3중 거절. `--out-dir` 이 입력 트리 안이면 exit 2 | `test_writer.py::test_symlink_at_artifact_path_refused`, `::test_hardlink_at_artifact_path_refused`, `::test_out_dir_inside_input_refused` |
| **CSV 수식 주입** | `= + - @ \t \r` 로 시작하면 `'` 전치. **선행 공백을 붙여 우회하는 경로**(`" =cmd"`)도 막았습니다 | `test_writer.py::test_formula_prefixes_quoted` (6 파라미터) |
| **파일 안의 가짜 판정 줄이 리포트 행을 위조** | diff 미리보기의 제어문자·개행을 `�`/공백으로 치환하고 폭을 자릅니다 | `test_textdiff.py::test_preview_cannot_forge_a_verdict_line` |
| **집 폴더 이름이 산출물에 샌다** | 산출물·콘솔에 상대경로만 싣습니다 | `test_report.py::test_paths_in_rows_are_relative`, `::test_console_has_no_absolute_home_path` |
| **`\| head` 가 종료코드를 뒤집는다** | 종료코드는 출력과 무관하게 계산 | `test_cli.py::test_head_pipe_does_not_flip_exit_code`, `::test_broken_pipe_is_not_a_traceback` |
| **자백 없는 리포트가 "전부 확인했다"로 읽힌다** | `[커버리지 자백]` 이 비면 `ReportIntegrityError` 로 죽고 리포트를 출력하지 않습니다 | `test_report.py::test_report_refuses_without_confession`, `::test_report_refuses_with_stub_confession` |
| **안심시키는 문구** | 이 툴은 `최신입니다`·`제출 가능`·`동기화 완료`·`문제 없음`·`safe to submit`·`이상 없음` 같은 말을 **하지 않습니다** — 소스에 0건이고, 문서에서 부정문 밖에 나오면 테스트가 실패합니다 | `test_safety_ast.py::test_forbidden_phrase_absent_from_source`, `::test_forbidden_phrase_only_in_negation_in_docs` |

### 기획서 실측값과의 대조 (이 툴의 척추)

`논문_투고/` 16개 논문 폴더에 엔진을 돌려 기획서가 센 값을 **전부 재현**했습니다
(`tests/test_realtree.py`, 트리가 없으면 skip):

| 항목 | 기획서 실측 | 구현 결과 |
|---|---|---|
| 짝 | 245쌍 | **245** ✓ |
| 원시 바이트 일치 | 196 | **196** ✓ |
| 원시 불일치 | 49 | **49** ✓ |
| 정규화로 사라지는 오탐 | 11 (전부 PDF) | **11, 전부 PDF** ✓ |
| 남는 실제 불일치 | 38 | **38** ✓ |
| 확장자별 | py 17 · pdf 9 · png 5 · docx 5 · md 2 | **동일** ✓ |
| 영향 폴더 | 6 / 16 | **6 / 16** ✓ |

**단, 한 가지를 바로잡았습니다.** 위 245쌍은 기획서의 측정 조건 그대로
(아카이브 폴더를 작업본에 포함)일 때의 값입니다. 출시 기본값은 아카이브를 제외하므로
**짝 237쌍 · 원시일치 188**이 되고, **줄어든 8쌍은 전부 '일치'였습니다** — 즉
**실제 불일치 38쌍과 확장자 분포는 두 조건에서 동일**합니다. 두 값 모두 테스트로
박아 두었습니다(`test_default_excludes_eight_archive_pairs`).

기획서가 지목한 **실물 3건**도 그대로 재현됩니다:

- `analysis/06_synthesis.py` — 재현성 예치본에 `three values the manuscript reports`
  블록 부재. **기획서는 +19행이라 적었으나 실제는 +29행/-0행**입니다(같은 커밋의
  두 번째 헝크 10행을 기획 세션이 세지 않았습니다). 테스트는 **+29/-0** 으로 고정했습니다.
- `analysis/08_prisma_figure.py` — `matplotlib.rcParams['pdf.fonttype'] = 42` 부재, +5행/-0행 ✓
- `figures/fig5_tiered_framework.png` — 봉투 `07-30 11:19 · 601,491 B` < 작업 `07-31 10:43 · 599,103 B` ✓

세 건 모두 `[치명] 봉투가 구세대`. 그리고 **치명 0건이 나와야 하는 폴더**
(`2.워치HRV_결측정보성`, 30쌍 전부 일치)에서 exit 0 을 확인했습니다.

---

## 1라운드 — 적대 패널 4인 병렬 (2026-09-18)

정정 담당(correctness) · 엣지케이스 파괴자 · 문서정직성+유용성 · 보안+테스트품질,
네 명을 Agent 툴로 동시에 띄웠습니다. **"nothing material" 은 한 명도 없었고**, 전원이
실제로 재현 가능한 결함을 들고 왔습니다. 아래는 고친 것 전부입니다.

### 조용히 틀리는 결함 (가장 위험한 분류)

| # | 결함 | 왜 위험한가 | 수정 | 회귀 테스트 |
|---|---|---|---|---|
| 1 | **기본 확장자 6종 밖의 파일을 세지도, 자백하지도 않았다** | 봉투가 전부 `.tif`/`.xlsx` 면 `읽음 0/0 · 치명 0건 · exit 0` — PLOS ONE 봉투의 일상이다. `읽음 0/0` 은 "전부 읽었다"로 읽힌다 | 확장자별로 세어 자백에 인쇄하고, **비교한 쌍이 0인데 봉투에 파일이 있으면 exit 3**. 붙여 쓸 수 있는 `--ext tif` 를 함께 인쇄 | `test_round1_fixes.py::test_all_tif_envelope_exits_three_not_zero` 외 7건 |
| 2 | **`[판정불가]` 쌍이 있어도 exit 0** | `cp -p`·Dropbox·rsync `--times` 는 mtime 을 보존한다. 내용이 다른데 방향을 못 정한 715쌍이 "깨끗함"으로 나갔다 | `[판정불가]` 가 하나라도 있으면 **exit 3** (3이 1보다 우선) | `test_round3_fixes.py::test_undecidable_pairs_exit_three_not_zero`, `::test_undecidable_outranks_critical` |
| 3 | **읽지 못한 디렉터리가 흔적 없이 사라졌다** | `os.walk` 는 권한 오류를 기본으로 삼킨다. `chmod 000` 폴더 하나가 통째로 없어지고 `못 읽음 0` 이 인쇄됐다 | `onerror=` 로 받아 자백 + exit 3 | `test_round3_fixes.py::test_unreadable_directory_is_confessed` |
| 4 | **같은 이름의 작업본이 여럿일 때 봉투와 맞는 쪽을 골랐다** | 판정이 **구조적으로 항상 '일치'** 가 된다. 갈라진 최신본이 있어도 조용하다 | 후보 전부가 같은 내용일 때만 짝짓고, 아니면 `이름중복` 으로 판정하지 않음 | `test_pairing.py::test_duplicate_names_one_matching_one_diverged_is_ambiguous` |
| 5 | **0바이트·확장자 다른 파일이 내용 해시로 엮였다** | `__init__.py`(0B) ↔ `빈파일.csv`(0B) 가 짝이 되어 **짝지음 비율 100%** 가 되고 양쪽의 결손이 가려졌다 | 같은 확장자 + 크기 > 0 일 때만 이름 없는 짝을 인정 | `test_pairing.py::test_zero_byte_files_are_not_cross_paired_by_hash` |
| 6 | **봉투가 여럿일 때 `짝없음.csv` 가 거짓을 적었다** | A 봉투에 든 파일이 B 기준으로 "작업폴더에만 있음" 으로 실렸다 | `Analysis.work_only` 를 전 봉투 합산으로 | `test_round1_fixes.py::test_work_only_is_summed_across_packages` |
| 7 | **봉투 쪽 아카이브 폴더 제외가 자백되지 않았다** | `submission/03_archive/` 안의 낡은 파일이 흔적 없이 빠졌다 | 제외 목록에 봉투 라벨을 붙여 인쇄 | `test_round1_fixes.py::test_excluded_dirs_inside_the_envelope_are_confessed` |
| 8 | **`--inspect` 가 `--package` 없이는 동작하지 않았다** | 두 문서가 안내하는 **워크플로 1단계**가 그냥 거절됐다 | 봉투 없이도 스캔 결과를 인쇄. 단, **판정하지 않으므로 exit 2** (`--inspect && 제출` 이 통과하지 않게) | `test_round1_fixes.py::test_inspect_without_package_still_prints_the_scan` |

### 입력을 건드리는 결함

| # | 결함 | 수정 | 회귀 테스트 |
|---|---|---|---|
| 9 | **`--out-dir` 봉쇄가 어휘 비교라 네 갈래로 뚫렸다** — 심볼릭 부모, `/tmp`→`/private/tmp`(공격자 없이도 발생), 대소문자(APFS 기본), NFC/NFD(한글 폴더). 리포트 5개가 입력 트리 안에 떨어졌다 | `realpath` + NFC + casefold (`writer.canonical`/`is_inside`), 폴더 생성 **후** 재확인 | `test_round2_fixes.py` 6건 |
| 10 | **`_open_artifact` 에 TOCTOU** — 검사와 열기 사이에 하드 링크를 심으면 입력 파일이 잘렸다. 리뷰어가 **실제로 원고 파일을 CSV 바이트로 덮어썼다**(24,938회 시도 중 1회 성공) | `O_TRUNC` 없이 연 뒤 **열린 fd** 를 `fstat`(S_ISREG + nlink) 하고 통과했을 때만 `ftruncate` | `test_round2_fixes.py::test_hardlink_planted_after_check_does_not_truncate` |
| 11 | **`--package` 가 심볼릭 링크면 "작업 폴더 안" 검사를 통과했다** — 트리 **밖** 폴더를 봉투로 비교했다 | `engine._validate_roots` 도 실체 경로로 비교, 봉투를 realpath 로 정규화해 작업 스캔에서 확실히 제외 | `test_round3_fixes.py::test_package_symlink_pointing_outside_work_refused` |
| 12 | **거절이 다섯 번째 산출물에서 나면 앞의 네 개가 이미 쓰여 있었다** | 다섯 자리를 **모두 먼저** 검사한 뒤 쓴다 | `test_round2_fixes.py::test_artifact_targets_checked_before_any_write` |

### 리포트를 위조당하는 결함

| # | 결함 | 수정 | 회귀 테스트 |
|---|---|---|---|
| 13 | **파일 이름이 리포트 줄을 위조했다** — 이름에 개행을 넣어 `[치명] 봉투가 구세대 — 0쌍` 을 가짜로 인쇄시키고, ANSI 로 앞 줄을 지우고, 백틱으로 `세대점검.md` 의 코드 펜스를 닫아 `## 가짜 제목` 을 렌더시켰다 | 모든 경로·라벨을 `textdiff.safe_path` 로 통과(제어문자 → U+FFFD, 백틱 → `ˋ`, 길이 제한), 마크다운 펜스는 본문의 최장 백틱열보다 길게 | `test_round2_fixes.py` 5건 |
| 14 | **`세대점검.md` 에 홈 디렉터리와 계정 이름이 실렸다** — `실행:` 줄이 `argv` 를 그대로 담았다. 공저자에게 보내는 파일이다 | 경로형 인자를 `.../마지막칸` 으로 축약 | `test_round2_fixes.py::test_markdown_command_line_has_no_absolute_path` |

### 해시·수치가 틀리는 결함

| # | 결함 | 수정 | 회귀 테스트 |
|---|---|---|---|
| 15 | **PDF 정규식이 제한 없는 `*`** — ① 128KB 에 10.7초(1MB 는 ~11분) 걸리는 O(n²) 폭주 ② 스트림 안의 우연한 `/ID[` 를 만나면 **본문을 삼켜** 진짜 차이를 "메타데이터만 다름"으로 **적극적으로 거짓말**했다 | 전부 `{0,256}`/`{0,512}` 로 길이 제한 (5MB PDF 0.85초) | `test_normalize.py::test_every_pdf_pattern_is_length_bounded` |
| 16 | **실제 PDF 의 절반이 정규화되지 않았다** — 사용자 디스크의 PDF 40개 중 **15개가 XMP 날짜**를 함께 싣는다. 다시 저장만 해도 `[치명]` 이 떴다 | `xmp:CreateDate`·`ModifyDate`·`MetadataDate`·`xmpMM:InstanceID`·`DocumentID` 를 태그형·속성형 모두 제거 | `test_normalize.py::test_xmp_metadata_only_difference_collapses` (5 파라미터) + **실측 회귀 11쌍이 그대로 유지됨**(과잉 정규화가 아님을 실데이터로 확인) |
| 17 | **zip 해시에 길이 구분이 없어 충돌이 구성 가능했다** — `("a", b"Xb\0Y")` 와 `("a", b"X"),("b", b"Y")` 가 같은 다이제스트 → **서로 다른 문서를 '일치'로 판정** | 엔트리마다 길이 접두사 + 고정 길이(32B) 다이제스트로 이중 프레이밍 | `test_normalize.py::test_zip_length_framing_prevents_collision` |
| 18 | **이름이 중복된 zip 엔트리는 마지막 것만 해시됐다** | `infolist()` 로 전부 훑는다 | `test_normalize.py::test_duplicate_zip_entry_names_are_all_hashed` |
| 19 | **`docProps/` 가 없는 zip 을 `정규화적용=none` 으로 적었다** — 이미 컨테이너 수준을 벗겨 냈는데 "원시 바이트로 비교했다"고 적는 거짓말 | `zip-container` 태그 신설 | `test_normalize.py::test_zip_without_docprops_is_labelled_zip_container` |
| 20 | **`+N/-N` 이 `diff -u` 와 달랐다** — `--`/`++` 로 시작하는 줄이 헤더로 오인돼 사라졌다(YAML front matter 삭제가 `-2` 로 표시). 줄끝만 바뀐 경우는 `+0행/-0행` 이라 "아무것도 안 바뀜"으로 읽혔다 | `SequenceMatcher` opcode 로 세고, 0/0 이면 `줄 내용은 같고 줄끝·마지막 개행만 다름` 이라고 적는다. **주의: `SequenceMatcher` 는 최소 편집 스크립트가 아니다** — 줄이 대량으로 재배치되거나 중복 줄이 많으면 GNU `diff -u` 보다 큰 수가 나올 수 있다(표시용 참고값이며 판정·종료코드에는 쓰이지 않는다) | `test_round2_fixes.py` 6건 |
| 21 | **건너뛴 큰 파일이 두 번 세어졌다** — 봉투 이름이 `submission` 이 아니면(`SUBMISSION_BRM` 류) 분모가 부풀고 같은 경로가 두 번 인쇄됐다 | `skip_subtrees` 를 자백 목록에도 적용 | `test_round2_fixes.py::test_skipped_large_not_double_counted` |
| 22 | **봉투 안에서 읽기 실패한 파일이 라벨 없이 자백됐다** | `HashCache.label` 로 봉투 이름을 붙인다 | `test_round2_fixes.py::test_unreadable_inside_envelope_is_labelled` |
| 23 | **한 작업본에 봉투 사본이 둘이면 콘솔 두 줄이 완전히 동일했다** — 어느 파일을 다시 복사해야 하는지 알 수 없었다 | 판정 줄마다 `봉투 안:` 경로를 인쇄 | `test_round2_fixes.py::test_console_shows_the_envelope_path` |

### 터지거나 멈추는 결함

| # | 결함 | 수정 | 회귀 테스트 |
|---|---|---|---|
| 24 | **zip 폭탄** — 3.1MB `.docx` 가 압축 해제 3GB(피크 RSS 3.78GB, 메모리 풋프린트 8.6GB). 실패하면 `MemoryError` 트레이스백 | 엔트리를 버퍼에 담지 않고 흘려 보내며 해시, 압축 해제 총량 **512MB 상한**, 넘으면 원시 바이트로 물러섬 | `test_normalize.py::test_zip_bomb_does_not_exhaust_memory` |
| 25 | **한두 바이트 손상된 `.docx` → `zlib.error` 트레이스백 + exit 1** — 무작위 비트 플립 3,000회 중 **141회(4.7%)** 발생. 동기화 중단·USB 복사면 충분하다. exit 1 이라 "낡은 봉투"와 구분 불가 | `zlib.error`·`MemoryError`·`UnicodeDecodeError` 를 폴백 예외에 추가 | `test_normalize.py::test_corrupt_deflate_stream_falls_back_not_traceback` |
| 26 | **스캔 뒤 FIFO 로 바뀐 경로에서 영원히 멈췄다** | `O_RDONLY\|O_NONBLOCK` 으로 열고 `fstat` 으로 일반 파일인지 확인한 뒤에만 읽는다 | `test_round3_fixes.py::test_fifo_swapped_in_after_scan_does_not_hang` |
| 27 | **닫힌 stdout → `AttributeError` 트레이스백 + 깨끗한 트리가 exit 1** | `_SafeStream` 으로 감싸 출력 실패가 종료코드를 바꾸지 못하게 | `test_round3_fixes.py::test_closed_stdout_does_not_change_exit_code` |
| 28 | **`PYTHONIOENCODING=ascii` → `UnicodeEncodeError` + exit 1** (CI 환경에서 흔하다) | 같은 래퍼에서 `backslashreplace` 로 강등 | `test_round3_fixes.py::test_ascii_stdout_encoding_does_not_crash` |
| 29 | **`--mtime-tolerance -1` 이 시각이 같은 쌍에 `[치명]` 을 붙였다**(증거 0에서 방향 주장). `inf` 는 모든 판정을 없애고 exit 0 을 만들었다 | 유한하고 0 이상인 값만 허용. `--min-coverage`·`--max-bytes` 도 범위 검증 | `test_round3_fixes.py::test_non_finite_mtime_tolerance_refused` (4 파라미터) |
| 30 | **`실행.command` 가 한글 쉘 변수를 썼다** — bash 3.2(더블클릭 기본)는 비ASCII 식별자를 거부해 **첫 화면에 에러 두 줄**이 뜨고, 리포트가 `$리포트` 라는 이름으로 **저장소 폴더 안에** 떨어졌다 | ASCII 이름으로 교체, bash 3.2 로 실행 검증 | `test_round1_fixes.py::test_launcher_has_no_non_ascii_shell_variables` |

### 테스트 자체의 결함 (보안+테스트품질 감사관 지적)

셀 수 있는 것이 품질은 아니라는 지적을 받아들여 다음을 고쳤습니다.

- **동어반복 제거.** `test_no_critical_among_the_196_raw_matches` 는 `same_rows` 를 순회하며
  `label == SAME` 을 확인했다 — 정의상 항상 참이라 **어떤 코드 변경으로도 실패할 수 없었다.**
  이제 툴의 판정을 버리고 **디스크에서 원시/정규화 해시를 다시 계산해** 대조합니다
  (`test_the_196_raw_matches_are_byte_identical_on_disk`, `test_the_11_normalized_pairs_differ_only_in_metadata`).
- **`or True` 제거.** `test_help_text_lists_exit_codes` 는 무조건 통과했습니다.
- **교집합이 없는 비교 제거.** `assert expanduser("~") not in <tmp_path 밑에서 만든 문자열>` 은
  통과가 보장된 주장이었습니다. **실제로 샐 수 있는 문자열**(`str(tmp_path)`)로 바꿨고,
  그 과정에서 `세대점검.md` 의 절대경로 유출(위 #14)이 드러났습니다.
- **조용한 skip 제거.** `test_realtree.py` 는 ① 봉투 후보를 못 찾으면 skip ② 논문 폴더가 없으면
  skip 이었습니다. ①은 **봉투 인식이 완전히 깨진 상태**이고 ②는 폴더 이름이 한 글자 바뀌면
  회귀 13건이 사라지는 길입니다. 둘 다 **실패**로 바꿨습니다(트리 자체가 없는 컴퓨터에서만 skip).
- **AST 규칙의 fail-open 제거.** `open(p, mode_변수)` 를 읽기로 가정하던 기본값,
  `os.open` 플래그를 보지 않던 검사, 별칭(`import os as o`)·직접 import·`getattr(os,...)`·
  `__import__` 우회로를 전부 막았습니다. `os.system`/`popen`/`exec*`/`spawn*`/`fork` 를
  금지 목록에 추가했습니다. 중복이던 `test_no_shutil_import`(13 ID)는 삭제했습니다.
- **입력 불변 확인 강화.** 크기·초 단위 mtime 만 보던 스냅샷에 **내용 해시와 inode** 를 넣어,
  같은 초 안의 같은 길이 덮어쓰기도 잡습니다.

### 결과

- 테스트 **531 → 661** (파라미터 확장 포함). 늘어난 것보다 **못 잡던 것을 잡게 된 것**이 핵심입니다.
- **실측 회귀는 전 과정에서 그대로 유지**됐습니다: 짝 245 · 원시일치 196 · 원시불일치 49 ·
  정규화로 사라지는 오탐 11(전부 PDF) · 남는 실제 불일치 38 · `py 17·pdf 9·png 5·docx 5·md 2` ·
  6/16. XMP 정규화를 추가한 뒤에도 11과 38이 **둘 다 그대로**라는 점이, 새 정규화가
  과잉도 과소도 아님을 실데이터로 보여 줍니다.

---

## 2라운드 — 적대 패널 2인 병렬 (2026-09-18)

1라운드 수정이 컸으므로 **고친 것이 정말 고쳐졌는지**를 검증하는 라운드를 돌렸습니다
(정정+보안 / 문서+테스트품질). 결과는 뼈아팠습니다: **1라운드 수정 다섯 개가 한 경로만
막고 옆 경로를 그대로 두었습니다.** 전부 재현 가능한 형태로 지적됐고, 고쳤습니다.

### 1라운드 수정이 불완전했던 것

| # | 남아 있던 구멍 | 왜 놓쳤나 | 수정 | 회귀 테스트 |
|---|---|---|---|---|
| 31 | **종료코드 120 이 새어 나갔다** — `_SafeStream` 은 쓰기를 막았지만, 인터프리터 종료 시 진짜 `sys.stdout` 플러시가 실패하면 CPython 이 **상태를 120 으로 덮어쓴다**. `\| head -20`·`\| less` 로 조기 종료한 것만으로 **깨끗한 트리가 실패로 보고**됐다 | 출력 래퍼만 고치고 **프로세스 종료 경로**를 보지 않았다 | `exit_with()` 에서 플러시 실패 시 fd 를 `/dev/null` 로 갈아 끼우고 `os._exit(code)` | `test_round4_fixes.py::test_clean_tree_stays_zero_when_the_pipe_closes` (3 파라미터) 외 3건 |
| 32 | **FIFO 방어가 `.docx` 와 텍스트 diff 를 비켜 갔다** — `_open_regular` 을 만들어 놓고 `zipfile.ZipFile(경로)` 와 `open(경로,'rb')` 는 그대로 뒀다. **이 툴이 가장 많이 해시하는 형식이 `.docx`** 인데 거기서 영원히 멈췄다 | 한 함수만 고치고 같은 위험의 다른 호출자를 세지 않았다 | zip 은 검증된 **파일 객체**를 넘기고, `textdiff._read_lines` 도 `_open_regular` 을 쓴다. 이제 `stalecheck/` 안에 `writer.py` 밖의 내장 `open(` 이 **0건**임을 AST로 강제 | `test_round4_fixes.py::test_fifo_named_docx_does_not_hang`, `::test_fifo_named_md_does_not_hang_in_textdiff`, `test_safety_ast.py::test_no_builtin_open_outside_writer` |
| 33 | **`--out-dir` 을 검사 이후 바꿔치기하면 입력을 덮어썼다** — `prepare_out_dir` 은 한 번 검사하고, `_open_artifact` 는 **마지막 경로 요소만** `O_NOFOLLOW` 했다. 검사 뒤 `--out-dir` 자체를 작업 폴더로 향하는 심볼릭 링크로 바꾸면 모든 가드를 통과했다(리뷰어가 실증) | 시점(prepare)만 보고 **쓰는 순간**을 보지 않았다 | 검증한 폴더의 `(st_dev, st_ino)` 를 기억하고, 쓸 때 폴더를 `O_DIRECTORY\|O_NOFOLLOW` 로 열어 신원을 대조한 뒤 **`dir_fd=` 로** 산출물을 연다 | `test_round4_fixes.py::test_out_dir_swapped_for_symlink_after_prepare_is_refused`, `::test_out_dir_replaced_by_another_directory_is_refused` |
| 34 | **대소문자 접기가 '받아들이는 쪽'으로 샜다** — `is_inside` 를 `--out-dir` **거절**과 `--package` **수락** 양쪽에 같은 방식으로 썼다. 대소문자 구분 볼륨에서 `work/` 와 `Work/` 가 같다고 판정돼, **트리 밖 폴더를 봉투로 받아들이고** 라벨에 `../` 가 찍혔다 | "과하게 매칭해도 거절이라 안전하다"는 논리를 수락 경로에 그대로 옮겼다 | `is_inside(..., fold=False)` 를 수락 판단에만 쓴다. 거절 판단은 그대로 접는다 | `test_round4_fixes.py::test_is_inside_without_fold_rejects_case_variant`, `::test_package_validation_uses_unfolded_comparison` |
| 35 | **산출물 자리의 FIFO 에서 멈췄다** — `_open_regular` 에는 `O_NONBLOCK` 을 넣고 `_open_artifact` 에는 넣지 않았다. `fstat` 으로 거르려 했지만 **`os.open` 이 커널에서 먼저 멈춘다** | 같은 수정을 두 곳에 적용하지 않았다 | `_open_artifact` 에도 `O_NONBLOCK`, 검사 통과 후 해제 | `test_round4_fixes.py::test_fifo_at_artifact_path_does_not_hang` |

### 조용히 틀리던 것 (2라운드에서 새로 발견)

| # | 결함 | 수정 | 회귀 테스트 |
|---|---|---|---|
| 36 | **이름이 겹쳐 판정하지 못한 봉투 파일이 있어도 exit 0** — 작업 폴더 어딘가에 같은 이름의 파일이 하나 더 있다는 이유만으로 **진짜 낡은 봉투 파일이 조용히 통과**했다. 리뷰어가 실증: 중복 파일을 지우면 곧바로 exit 1 | 이름중복이 하나라도 있으면 **exit 3**. "보지 않은 것"을 "괜찮은 것"으로 내보내지 않는다 | `test_round3_fixes.py::test_ambiguous_basename_does_not_exit_zero` |
| 37 | **숨김 파일이 흔적 없이 사라졌다** — `.숨은원고.md` 가 양쪽에 있어도 `읽음 2/2 · 치명 0건` | 대상 확장자인 숨김 파일을 세어 자백에 인쇄 | `test_round3_fixes.py::test_hidden_files_are_counted_in_confession` |
| 38 | **봉투 안의 심볼릭 링크가 자백되지 않았다** — 봉투 그림이 전부 링크면 한 쌍도 비교되지 않는데 리포트에 아무 표시가 없었다(작업 폴더 쪽만 세고 있었다) | 양쪽 합산으로 인쇄 | `test_round3_fixes.py::test_symlinks_inside_the_envelope_are_confessed` |
| 39 | **zip 중복 엔트리의 순서를 잃었다** — 다이제스트만 정렬해서 `x=1,x=2` 와 `x=2,x=1` 이 같은 해시가 됐다. 중복 엔트리를 읽는 쪽에는 **서로 다른 문서**다 | 이름별로 묶어 **그 안의 순서는 보존**하고, 이름 간 순서만 무시 | `test_round4_fixes.py::test_duplicate_entry_order_changes_the_hash`, `::test_entry_order_between_different_names_is_still_ignored` |
| 40 | **`/ID` 패턴이 여전히 본문 512바이트를 삼킬 수 있었다** — 스트림 안의 우연한 `/ID[` 하나로 "p = 0.001 significant" 와 "p = 0.999 not signific" 이 같은 해시가 됐다(리뷰어가 실증). 사용자 트리의 PDF 364개에서는 0건이었지만, 발생하면 **적극적 거짓말**이다 | `\[\s*(?:<hex>\s*){0,4}\]` 로 trailer 형태만 지운다 | `test_round4_fixes.py::test_id_pattern_does_not_eat_content_in_a_stream`, `::test_real_trailer_id_is_still_stripped` |

### 테스트가 여전히 거짓말하던 것

- **`test_launcher_variable_references_are_ascii` 는 어떤 경우에도 실패할 수 없었다.**
  정규식 `[A-Za-z_\W]` 가 `{` 를 먼저 먹어서 한글 변수 참조를 못 잡았다. 리뷰어가
  `echo "$한글변수"` 를 넣고도 초록인 것을 확인했다. 이제 **검사 자체를 먼저 검증**한다
  (`offenders('echo "$한글변수" ${또다른}') == [...]`).
- **`test_safety_ast.py` 의 41개 인스턴스가 빈 루프였다.** 규칙을 모듈 수만큼 파라미터화해
  대부분의 모듈에서 검사할 노드가 아예 없었다. 세 규칙(`getattr(os,...)`·`os.open` 플래그·
  쓰기 모드 `open`)을 **모듈 전체를 한 번에 훑는 단일 테스트**로 합치고, **검사 대상이
  실제로 존재했는지**(`assert inspected`)까지 확인한다. 동어반복이던
  `test_shutil_is_in_the_forbidden_list` 는 삭제하고, `test_modules_found`(`>= 10`)는
  **모듈 집합 정확 일치**로 바꿨다.
- **`HARDENING.md` 가 금지어 검사 대상이 아니었다.** 금지 문구를 가장 많이 인용하는 문서인데
  빠져 있었다. `TEXT_FILES` 에 추가했고, 이 문서의 해당 줄도 부정문으로 다시 썼다.
- 낡은 참조 정리: 존재하지 않는 테스트 이름과 `(150건)` 표기를 실제 값으로 교정.

### 리뷰어가 뚫지 못한 것 (능동 공격 후 확인)

- **네트워크 0** — 감사 훅으로 전 CLI 실행을 추적해 이벤트 10건 전부가
  `--out-dir` 아래 산출물 열기였고 **네트워크 이벤트 0건**.
- **입력 트리 불변** — 성공·콘솔전용·`--inspect`·거절 3종·`--max-bytes` 등 7개 경로에서
  `sha256 + mode + size + mtime_ns + ino + nlink` 지문이 **전부 동일**.
- **CSV 수식 주입** — 선행 공백·탭 포함 7가지 전부 `'` 로 무력화.
- **비합리적 입력** — `nan`·`inf`·음수·`--ext ''`·3000단계 중첩·파일 10,000개(0.58초)·
  20만 행 CSV diff·4,000회 비트 플립 docx 전부 {0,1,2,3} 안에서 끝남.
- **실제 논문 트리** — 4개 폴더에서 헛경보 0건, 조용해야 할 폴더는 조용함.

### 결과

- 테스트 **652** (1라운드 후 661 → 빈 루프 36개를 걷어내고 진짜 회귀 27개를 더한 결과).
  **수는 줄었고 잡는 것은 늘었습니다.**
- 문서+테스트 리뷰어가 **돌연변이 테스트 5종**(PDF 정규화 무력화 / NFC 제거 / mtime 방향
  뒤집기 / `--out-dir` 봉쇄 제거 / `docProps` 제외 파괴)을 임시 복사본에 넣어
  **5종 모두 빨간불**(각각 34·3·35·8·18건 실패)을 확인했습니다 — 스위트에 실제로 이빨이 있습니다.
- **실측 회귀는 이 모든 변경을 통과해도 그대로**입니다: 짝 245 · 원시일치 196 · 원시불일치 49 ·
  정규화로 사라지는 오탐 11 · 실제 불일치 38 · `py 17·pdf 9·png 5·docx 5·md 2` · 6/16.
