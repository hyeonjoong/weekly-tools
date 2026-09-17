"""CSV 쓰기 — 엑셀에서 열어도 **수식이 실행되지 않게**.

`=HYPERLINK(...)` 로 시작하는 문자열이 셀에 들어가면 엑셀은 그것을 수식으로
실행한다. 피험자 코드는 파일명에서 온 외부 입력이므로 그대로 흘려보내면 안 된다.

다만 **숫자는 그대로 둔다.** `-3`(부호 붙은 차이) 을 `'-3` 으로 바꿔 버리면
트래커 불일치 표가 전부 문자열이 되어 정렬도 합계도 되지 않는다. 그래서
"숫자로 읽히면 그대로, 아니면 위험문자 앞에 `'`" 가 규칙이다.
"""

import csv
import re

from doseaudit.paths import open_artifact
from doseaudit.sanitize import safe_text

#: 엑셀·LibreOffice·구글시트가 수식 시작으로 보는 문자들.
_DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n")
_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?%?$")

#: 첫 글자 판정 전에 건너뛰는 공백류. 전각 공백(`\u3000`)은 이 툴의 로캘에서
#: 키보드 한 번이면 나오고, `str.strip()` 이 이미 지우는 경우가 대부분이지만
#: 방어는 겹쳐 두는 쪽이 맞다.
_SKIPPABLE = (" \t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
              "\u2007\u2008\u2009\u200a\u202f\u205f\u3000")


def escape_cell(value):
    """한 칸을 안전하게. 숫자는 손대지 않는다."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = safe_text(text, max_len=500)
    if not text:
        return ""
    if _NUMBER_RE.match(text.strip()):
        return text
    # `safe_text` 가 앞쪽 제어문자·비가시문자를 `\ufffd` 로 바꿔 두므로, 그걸
    # 건너뛴 다음 첫 글자로 판단한다 — `"\t=1+1"`·`"\ufeff=1+1"` 이 그대로
    # 빠져나가지 않도록. 공백도 함께 건너뛴다(`" =1+1"`).
    head = text.lstrip("\ufffd" + _SKIPPABLE)
    if head and head[0] in _DANGEROUS_PREFIX:
        return "'" + text
    return text


def write_csv(out_dir, filename, header, rows):
    """`utf-8-sig` (엑셀이 한글을 깨지 않게) 로 CSV 한 장을 쓴다."""
    with open_artifact(out_dir, filename) as fh:
        fh.write("﻿")
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow([escape_cell(h) for h in header])
        for row in rows:
            writer.writerow([escape_cell(c) for c in row])
    return filename
