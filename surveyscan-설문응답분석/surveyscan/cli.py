"""surveyscan 명령줄 인터페이스.

사용 예:
  surveyscan responses.csv --config scale.json
  surveyscan responses.csv --id-col ID --json
"""
from __future__ import annotations

import argparse
import codecs
import csv
import json
import math
import os
import sys
from typing import Dict, List, Optional

from . import __version__
from .analyze import analyze
from .config import SCORE_METHODS, ConfigError, auto_config, load_config
from .dataio import (
    DataError,
    SurveyData,
    load_csv,
    normalize_label,
    sniff_delimiter,
)
from .report import render, render_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="surveyscan",
        description="설문 응답 CSV 분석: 문항 기술통계 · Cronbach α/ω · 하위척도 점수 · 역문항 처리 · "
        "결측 요약 · 임상 심각도 구간 · 집단 비교(Welch t/ANOVA)",
    )
    p.add_argument("csv", help="설문 응답 CSV 경로 (행=응답자, 열=문항)")
    p.add_argument(
        "-c", "--config",
        help="하위척도/역문항/심각도구간(severity_bands) 설정 JSON 경로 "
        "(없으면 숫자 컬럼 전체를 한 척도로 분석)",
    )
    p.add_argument(
        "--id-col",
        action="append",
        default=[],
        metavar="이름",
        help="분석에서 제외할 ID 컬럼(여러 번 지정 가능)",
    )
    p.add_argument(
        "--group-col",
        metavar="이름",
        help="집단 비교 기준 컬럼(치료군·성별·기관 등). 이 컬럼으로 하위척도 점수를 나눠 "
        "Welch t/ANOVA·효과크기(Hedges g)·Holm 보정 p 를 냄. 문항 분석에서는 제외됨",
    )
    p.add_argument(
        "--time-col",
        metavar="이름",
        help="사전-사후(반복측정) 시점 컬럼. 같은 ID를 시점 간에 짝지어 변화량·대응표본 t·"
        "효과크기(dz)·검사-재검사 ICC·반응자 분석을 냄. 문항 분석에서는 제외됨 "
        "(--id-col 로 응답자 ID 를 함께 지정해야 함)",
    )
    p.add_argument("--time-pre", metavar="라벨", help="사전 시점 라벨(시점이 3개 이상이면 필수)")
    p.add_argument("--time-post", metavar="라벨", help="사후 시점 라벨(시점이 3개 이상이면 필수)")
    p.add_argument(
        "--pair-id",
        action="append",
        default=[],
        metavar="이름",
        help="사전-사후 짝짓기에 쓸 ID 컬럼(기본: --id-col 전체 조합). 여러 번 지정 가능",
    )
    p.add_argument(
        "--nonparam",
        action="store_true",
        help="순위 기반(비모수) 검정을 함께 출력: 집단비교는 Mann-Whitney U/Kruskal-Wallis, "
        "사전-사후는 Wilcoxon 부호순위. t/ANOVA 를 대체하지 않는 민감도 분석",
    )
    p.add_argument(
        "--encoding",
        metavar="utf-8",
        help="CSV 인코딩(기본: UTF-8 로 읽고 실패하면 CP949 자동 시도). 예: cp949, euc-kr",
    )
    p.add_argument(
        "--na-number",
        action="append",
        default=[],
        type=float,
        metavar="값",
        help="결측 코드로 쓰인 숫자(예: --na-number 999). 여러 번 지정 가능",
    )
    p.add_argument(
        "--delimiter",
        default=",",
        help="CSV 구분자 (기본: 콤마). 'tab'/'\\t' 는 탭, 'auto' 는 헤더를 보고 자동 판별",
    )
    p.add_argument(
        "--score-method",
        choices=SCORE_METHODS,
        default=None,
        help="하위척도 점수 산출: mean(가용문항 평균) 또는 sum(비례배분 총합). "
        "config 값을 덮어씀. ISI·PHQ-9 등은 보통 sum",
    )
    p.add_argument(
        "--ci-level",
        type=float,
        default=0.95,
        metavar="0.95",
        help="신뢰구간 신뢰수준 (0<값<1, 기본 0.95)",
    )
    p.add_argument(
        "--format",
        choices=["text", "json", "md", "markdown"],
        default=None,
        help="출력 형식: text(기본) / json / md",
    )
    p.add_argument(
        "--item-freq",
        action="store_true",
        help="문항별 응답 선택지 빈도표를 추가 출력. config에 정수 scale_min/scale_max 가 "
        "있어야 나옴(없으면 표 생략)",
    )
    p.add_argument(
        "--quality",
        action="store_true",
        help="응답 품질(부주의응답) 선별 지표 추가: longstring·IRV·결측률. "
        "자동 제외 기준이 아니라 원자료를 눈으로 확인할 대상을 좁히는 선별 도구",
    )
    p.add_argument(
        "--longstring-min",
        type=int,
        default=None,
        metavar="N",
        help="--quality 의 longstring 플래그 기준(이 값 이상이면 플래그). "
        "기본: max(3, 문항수/2) 휴리스틱",
    )
    p.add_argument("--json", action="store_true", help="--format json 과 동일(하위호환)")
    p.add_argument(
        "--scores-out",
        metavar="파일.csv",
        help="응답자별 하위척도 점수를 CSV로 저장(원자료에 다시 붙여 분석용)",
    )
    p.add_argument("-o", "--output", help="결과를 파일로 저장(미지정 시 표준출력)")
    p.add_argument("--version", action="version", version=f"surveyscan {__version__}")
    return p


def _write_scores_csv(path: str, data: SurveyData, result: Dict[str, object]) -> None:
    """응답자별 하위척도 점수를 CSV로 저장. ID 컬럼이 있으면 앞에 붙인다.

    수식 인젝션 방지: '=', '+', '-', '@' 등으로 시작하는 ID 값은 앞에 작은따옴표를 붙여
    스프레드시트에서 수식으로 실행되지 않게 한다.
    """
    id_cols = data.id_columns
    subs = result["subscales"]
    group_col = getattr(data, "group_column", None)
    used_names: Dict[str, int] = {}

    def _uniq(name: str) -> str:
        """헤더 이름 중복 방지(같은 이름 두 열이면 엑셀·pandas에서 조용히 섞인다)."""
        n = used_names.get(name, 0)
        used_names[name] = n + 1
        return name if n == 0 else f"{name}_{n + 1}"

    # 원본 CSV 줄 번호를 항상 첫 열로 낸다. 빈 줄을 건너뛰면 '몇 번째 응답자'와
    # '파일의 몇 번째 줄'이 어긋나서, 이 열 없이 엑셀에 붙이면 응답자가 통째로
    # 밀린다(사람마다 남의 점수가 붙는 조용한 사고).
    header = [_uniq("원본CSV행")] + [_csv_safe(_uniq(c)) for c in id_cols]
    # 집단 컬럼은 ID 로도 지정했을 수 있으므로 중복 출력하지 않는다.
    write_group = bool(group_col) and group_col not in id_cols
    if write_group:
        header.append(_csv_safe(_uniq(str(group_col))))
    # 시점 컬럼도 함께 낸다 — 반복측정 자료에서 시점 없이 점수만 있으면 어느 방문의
    # 값인지 알 수 없어 병합 자체가 불가능하다.
    time_col = getattr(data, "time_column", None)
    write_time = bool(time_col) and time_col not in id_cols
    if write_time:
        header.append(_csv_safe(_uniq(str(time_col))))
    for s in subs:
        header.append(_csv_safe(_uniq(str(s["name"]))))
        # 심각도 구간이 정의된 하위척도는 응답자별 구간 라벨도 함께 낸다
        # (임상 표에 바로 쓰는 값 — 사람이 다시 구간을 매기다 실수하는 것을 막는다).
        if s.get("bands"):
            header.append(_csv_safe(_uniq(str(s["name"]) + "_심각도")))
    n = data.n_respondents
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in range(n):
            line_no = data.source_lines[r] if r < len(data.source_lines) else r + 2
            row = [line_no]
            if id_cols:
                row += [_csv_safe(data.id_values[r].get(c, "")) for c in id_cols]
            if write_group:
                gv = data.group_values[r] if r < len(data.group_values) else ""
                row.append(_csv_safe(gv))
            if write_time:
                tv = data.time_values[r] if r < len(data.time_values) else ""
                row.append(_csv_safe(tv))
            for s in subs:
                val = s["scores"][r]
                # 비유한값(inf/nan)은 셀에 쓰지 않는다 — 엑셀·통계패키지가 텍스트로
                # 읽어 열 전체를 문자열로 만들거나 조용히 잘못된 값을 만든다.
                row.append(
                    "" if val is None or not math.isfinite(val) else f"{val:.6g}"
                )
                if s.get("bands"):
                    bs = s.get("band_scores") or []
                    lab = bs[r] if r < len(bs) else None
                    row.append(_csv_safe(lab) if lab else "")
            w.writerow(row)


def _csv_safe(s: str) -> str:
    """스프레드시트 수식 인젝션 방지용 프리픽스 처리."""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _same_file(a: str, b: str) -> bool:
    """두 경로가 같은 파일을 가리키는지(심볼릭 링크·상대경로 포함)."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.realpath(a) == os.path.realpath(b)


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 출력 경로가 입력 CSV와 같으면 원자료가 덮어써져 복구가 불가능하다
    # (탭 완성 한 번의 실수로 임상 원자료가 사라진다). 쓰기 전에 막는다.
    for opt, path in (("--scores-out", args.scores_out), ("-o/--output", args.output)):
        if path and _same_file(path, args.csv):
            print(
                f"오류: {opt} 경로가 입력 CSV와 같습니다 — 원자료를 덮어쓸 수 없습니다: {path}",
                file=sys.stderr,
            )
            return 2
    if args.scores_out and args.output and _same_file(args.scores_out, args.output):
        print(
            "오류: --scores-out 과 -o 경로가 같습니다(서로 덮어씁니다).", file=sys.stderr
        )
        return 2

    if not (0.0 < args.ci_level < 1.0):
        print("오류: --ci-level 은 0과 1 사이여야 합니다.", file=sys.stderr)
        return 2

    if args.delimiter == "auto":
        try:
            delimiter = sniff_delimiter(args.csv, args.encoding)
        except OSError as e:
            print(f"오류: 파일을 읽을 수 없습니다: {e}", file=sys.stderr)
            return 2
        shown = {",": "콤마", "\t": "탭", ";": "세미콜론", "|": "파이프"}.get(
            delimiter, delimiter
        )
        print(f"참고: 구분자를 '{shown}' 로 자동 판별했습니다.", file=sys.stderr)
    else:
        delimiter = "\t" if args.delimiter in ("tab", "\\t", "\t") else args.delimiter
    if len(delimiter) != 1:
        print(
            "오류: --delimiter 는 한 글자여야 합니다(탭은 'tab', 자동판별은 'auto').",
            file=sys.stderr,
        )
        return 2

    if args.encoding:
        try:
            # 빈 바이트열 decode 는 코덱을 찾지 않고 통과하므로 codecs.lookup 으로 확인한다.
            codecs.lookup(args.encoding)
        except LookupError:
            print(f"오류: 알 수 없는 인코딩입니다: {args.encoding}", file=sys.stderr)
            return 2

    # 시점 라벨은 자료 쪽과 같은 방식으로 정규화해야 보이지 않는 공백 때문에 매칭이
    # 조용히 실패하지 않는다(엑셀에서 복사한 라벨에 NBSP 가 흔하다).
    time_pre = normalize_label(args.time_pre) if args.time_pre is not None else None
    time_post = normalize_label(args.time_post) if args.time_post is not None else None
    if (time_pre is None) != (time_post is None):
        print("오류: --time-pre 와 --time-post 는 함께 지정해야 합니다.", file=sys.stderr)
        return 2
    if (time_pre or time_post) and not args.time_col:
        print("오류: --time-pre/--time-post 는 --time-col 과 함께 써야 합니다.", file=sys.stderr)
        return 2
    if args.pair_id and not args.time_col:
        print("경고: --pair-id 는 --time-col 과 함께 써야 적용됩니다(무시됨).", file=sys.stderr)
    if args.nonparam and not (args.group_col or args.time_col):
        # 비교 대상이 없으면 순위 검정이 붙을 표 자체가 없다 — 조용히 아무것도 안 나오면
        # 사용자는 '비모수로 돌렸다'고 착각한다.
        print(
            "경고: --nonparam 은 --group-col 또는 --time-col 과 함께 써야 출력됩니다"
            "(비교 대상이 없어 무시됨).",
            file=sys.stderr,
        )

    try:
        data = load_csv(
            args.csv,
            id_columns=args.id_col,
            na_numbers=args.na_number,
            delimiter=delimiter,
            group_column=args.group_col,
            time_column=args.time_col,
            encoding=args.encoding,
        )
    except FileNotFoundError:
        print(f"오류: 파일을 찾을 수 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except IsADirectoryError:
        print(f"오류: 폴더가 아니라 CSV 파일 경로를 지정하세요: {args.csv}", file=sys.stderr)
        return 2
    except PermissionError:
        print(f"오류: 파일을 읽을 권한이 없습니다: {args.csv}", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        if args.encoding:
            print(
                f"데이터 오류: CSV를 '{args.encoding}' 로 읽을 수 없습니다. 파일의 실제 "
                "인코딩을 확인하거나, --encoding 을 빼고 다시 실행해 보세요"
                "(UTF-8 → CP949 순으로 자동 시도합니다).",
                file=sys.stderr,
            )
        else:
            print(
                "데이터 오류: CSV를 UTF-8(또는 CP949)로 읽을 수 없습니다. "
                "--encoding 으로 인코딩을 직접 지정하거나, 엑셀이면 'CSV UTF-8'로 저장하세요.",
                file=sys.stderr,
            )
        return 2
    except DataError as e:
        print(f"데이터 오류: {e}", file=sys.stderr)
        return 2
    except csv.Error as e:
        # 예: 한 셀이 131,072자를 넘는 경우. ValueError 가 아니라서 위에서 안 잡힌다.
        print(
            f"데이터 오류: CSV를 읽을 수 없습니다 ({e}). 셀 하나가 지나치게 길거나 "
            "따옴표가 짝이 맞지 않는지 확인하세요.",
            file=sys.stderr,
        )
        return 2

    # ID 컬럼 오타 경고(P4): 지정했지만 헤더에 없던 이름
    if data.unknown_id_columns:
        print(
            "경고: --id-col 로 지정한 컬럼이 헤더에 없습니다(오타?): "
            + ", ".join(data.unknown_id_columns),
            file=sys.stderr,
        )

    try:
        if args.config:
            try:
                cfg = load_config(args.config)
            except IsADirectoryError:
                print(f"오류: 폴더가 아니라 config JSON 파일을 지정하세요: {args.config}", file=sys.stderr)
                return 2
            except PermissionError:
                print(f"오류: config 파일을 읽을 권한이 없습니다: {args.config}", file=sys.stderr)
                return 2
            except UnicodeDecodeError:
                print(
                    "설정 오류: config JSON을 UTF-8로 읽을 수 없습니다. UTF-8로 저장하세요.",
                    file=sys.stderr,
                )
                return 2
        else:
            # 자동설정: 숫자 컬럼만 사용. 제외되는 컬럼(전부 결측/텍스트)을 알린다(P3).
            dropped = data.nonnumeric_columns()
            if dropped:
                print(
                    "참고: 숫자값이 없어 분석에서 제외된 컬럼: "
                    + ", ".join(dropped)
                    + " (의도와 다르면 --config 로 명시하세요)",
                    file=sys.stderr,
                )
            cfg = auto_config(data.numeric_columns())
    except FileNotFoundError:
        print(f"오류: config 파일을 찾을 수 없습니다: {args.config}", file=sys.stderr)
        return 2
    except (ConfigError, json.JSONDecodeError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    # UTF-8 이 아닌 인코딩으로 읽혔으면 알린다(글자가 깨지지 않았는지 확인용).
    if (
        getattr(data, "encoding_used", "utf-8-sig") != "utf-8-sig"
        and not getattr(data, "encoding_forced", False)
    ):
        print(
            f"참고: UTF-8 로 읽히지 않아 '{data.encoding_used}' 로 읽었습니다 — "
            "리포트의 한글 컬럼명이 깨져 보이면 --encoding 으로 직접 지정하세요.",
            file=sys.stderr,
        )

    unknown_pair = [c for c in (args.pair_id or []) if c not in data.id_columns]
    if unknown_pair:
        print(
            "오류: --pair-id 로 지정한 컬럼은 --id-col 로도 지정해야 합니다"
            "(분석 문항과 구분하기 위함): " + ", ".join(unknown_pair),
            file=sys.stderr,
        )
        return 2

    # 점수 방식 CLI 덮어쓰기(config 값보다 우선).
    if args.score_method is not None:
        cfg.score_method = args.score_method

    if args.longstring_min is not None and args.longstring_min < 2:
        print("오류: --longstring-min 은 2 이상이어야 합니다.", file=sys.stderr)
        return 2
    if args.longstring_min is not None and not args.quality:
        print(
            "경고: --longstring-min 은 --quality 와 함께 써야 적용됩니다(무시됨).",
            file=sys.stderr,
        )

    try:
        result = analyze(
            data,
            cfg,
            conf=args.ci_level,
            item_freq=args.item_freq,
            quality_check=args.quality,
            longstring_min=args.longstring_min,
            use_nonparam=args.nonparam,
            time_pre=time_pre,
            time_post=time_post,
            pair_id_columns=args.pair_id or None,
        )
    except ValueError as e:
        print(f"분석 오류: {e}", file=sys.stderr)
        return 2
    except OverflowError:
        print(
            "분석 오류: 값이 너무 커서 계산할 수 없습니다. 입력에 1e150 이상의 비정상적으로 "
            "큰 수(엑셀 오입력·센서 오류 등)가 있는지 확인해 지우거나 결측으로 바꾼 뒤 다시 "
            "실행하세요. (척도 범위를 설정해도 이 계산은 그 전에 실패합니다.)",
            file=sys.stderr,
        )
        return 2

    fmt = args.format
    if fmt is None:
        fmt = "json" if args.json else "text"
    elif fmt == "markdown":
        fmt = "md"

    if fmt == "json":
        # 응답자별 리스트(점수·심각도 라벨)는 길어서 JSON 출력에선 생략(요약만 남김).
        # 응답자 단위 값이 필요하면 --scores-out 으로 CSV를 받으세요.
        slim = {k: v for k, v in result.items()}
        slim["subscales"] = [
            {k: v for k, v in s.items() if k not in ("scores", "band_scores")}
            for s in result["subscales"]
        ]
        # allow_nan=False: 비유한값이 있으면 조용히 깨진 JSON을 내보내지 않고 막는다.
        try:
            text = json.dumps(slim, ensure_ascii=False, indent=2, allow_nan=False)
        except ValueError:
            print(
                "분석 오류: 결과에 유한하지 않은 값이 있어 JSON으로 출력할 수 없습니다.",
                file=sys.stderr,
            )
            return 2
    elif fmt == "md":
        text = render_markdown(result)
    else:
        text = render(result)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError as e:
            print(f"오류: 결과를 저장할 수 없습니다: {e}", file=sys.stderr)
            return 2
        print(f"저장됨: {args.output}", file=sys.stderr)
    else:
        print(text)

    # 점수 CSV 내보내기(선택). 리포트를 성공적으로 낸 **뒤에** 쓴다 — 뒤이어 실패하는
    # 실행이 응답자 단위 임상 점수 파일만 덩그러니 남기지 않도록.
    if args.scores_out:
        try:
            _write_scores_csv(args.scores_out, data, result)
        except OSError as e:
            print(f"오류: 점수 CSV를 저장할 수 없습니다: {e}", file=sys.stderr)
            return 2
        # 알림은 stderr 로 — stdout 에 섞이면 `--format json | jq` 파이프가 깨진다.
        print(f"점수 저장됨: {args.scores_out}", file=sys.stderr)
    return 0


def main() -> None:  # console-script 진입점
    raise SystemExit(run())


if __name__ == "__main__":
    main()
