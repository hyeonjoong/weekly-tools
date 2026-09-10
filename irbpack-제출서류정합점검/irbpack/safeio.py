"""안전한 출력 — 리포트가 유출 경로나 덮어쓰기 사고가 되지 않게 하는 층.

여기서 막는 것:
  * 심볼릭/하드링크가 심어진 산출물 경로 (입력 원본을 조용히 덮어쓰는 사고)
  * CSV 수식 인젝션 (= + - @ 로 시작하는 셀)
  * 리포트에 그대로 실리는 전화번호·주민번호 (리포트 자체가 유출 경로가 된다)
  * 파일명에 섞인 개행·제어문자 (가짜 `[치명]` 줄을 심을 수 있다)
  * 절대경로(사용자 계정명) 유출 — 산출물에는 basename 만 쓴다
"""

import csv
import os
import re

from .normalize import strip_control

ARTIFACTS = ("정합점검.md", "불일치목록.csv", "항목추출표.csv", "대조불가.csv")


class OutputError(Exception):
    """출력 경로가 안전하지 않거나 쓸 수 없을 때. CLI 가 종료코드 2로 바꾼다."""


class ReportIntegrityError(Exception):
    """[커버리지 자백] 없이 리포트를 내보내려 할 때. 절대 무시하지 않는다."""


# ------------------------------------------------------------------ 마스킹

# 한국 병원 레터헤드에서 흔한 '(02)3010-3114' · '031)787-1234' 형태까지 잡는다.
#
# 구분자에 물결(~)과 비분리 하이픈(U+2011)이 들어 있는 이유: 본문은 마스킹 전에
# canon() 을 거치면서 en/em 대시가 '~' 로 바뀐다. 워드·한글이 ' - ' 를 자동으로 en 대시로
# 고치는 일이 흔해서, 이걸 빼면 '890312–1234567' 같은 주민번호가 그대로 새어 나간다
# (라운드2 B-1 — 실제로 산출물 3종에서 발견됐다).
# 문자클래스 끝에 놓이도록 일반 하이픈을 **맨 뒤**에 둔다(범위로 해석되지 않게).
_DASHES = "~‑–—‐―−-"          # 물결 · 각종 대시 · 하이픈 (워드 자동고침 포함)
_SEP = r"[.\s)/" + _DASHES + r"]*"
_PHONE = re.compile(r"(?<![0-9" + _DASHES + r"])\(?(0\d{1,2})\)?" + _SEP + r"(\d{3,4})" + _SEP + r"(\d{4})(?![0-9" + _DASHES + r"])")
_INTL_PHONE = re.compile(r"(?<![0-9" + _DASHES + r"])((?:\+|00)?82" + _SEP + r"\d{1,2})" + _SEP
                         + r"(\d{3,4})" + _SEP + r"(\d{4})(?![0-9" + _DASHES + r"])")
# 15xx·16xx·18xx 대표번호 (0 으로 시작하지 않는다)
_SERVICE_PHONE = re.compile(r"(?<![0-9" + _DASHES + r"])(1[568]\d{2})" + _SEP + r"(\d{4})(?![0-9" + _DASHES + r"])")
_RRN = re.compile(r"(?<!\d)(\d{6})\s*[.\s" + _DASHES + r"]?\s*([0-9])\d{6}(?!\d)")
_EMAIL = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def _middle(group):
    """가운데 자리 — 첫 숫자만 남기고 가린다.

    전부 가리면 `010-****-5678` 두 개가 화면에서 똑같아 보여, 리포트가 "다름"이라고
    말하면서 같은 문자열을 보여 주는 상태가 된다(라운드2 B-5). 첫 자리만 남기면
    실무에서 거의 항상 구분되면서 노출은 한 자리로 묶인다.
    """
    return group[0] + "*" * (len(group) - 1)


def mask_pii(text):
    """리포트·CSV 로 나가는 모든 문자열에 적용. 값의 식별성은 남기고 알맹이는 가린다."""
    if not text:
        return text
    # 주민등록번호는 생년월일까지 가린다 — 리포트는 팀에 공유되는 파일이다.
    text = _RRN.sub(lambda m: "******-%s******" % m.group(2), text)
    text = _INTL_PHONE.sub(lambda m: "%s-%s-%s" % (m.group(1), _middle(m.group(2)), m.group(3)), text)
    text = _PHONE.sub(lambda m: "%s-%s-%s" % (m.group(1), _middle(m.group(2)), m.group(3)), text)
    text = _SERVICE_PHONE.sub(lambda m: "%s-****" % m.group(1), text)

    def _mail(match):
        local = match.group(1)
        head = local[:2] if len(local) > 2 else local[:1]
        return "%s****@%s" % (head, match.group(2))

    return _EMAIL.sub(_mail, text)


def mask_then_truncate(text, limit, suffix="…"):
    """**마스킹을 먼저 하고** 자른다.

    반대로 하면 '031-787-123…' 처럼 잘린 번호가 마스킹 패턴에 걸리지 않아 그대로 남는다
    (라운드1 안전 감사에서 실제로 세 산출물 전부에서 발견된 유출 경로).
    """
    masked = mask_pii(strip_control(text or ""))
    if len(masked) <= limit:
        return masked
    return masked[:limit - len(suffix)] + suffix


def safe_name(path_or_name):
    """산출물에 실을 문서 이름 — basename 만, 제어문자 제거, 길이 제한."""
    name = os.path.basename(str(path_or_name).rstrip("/\\"))
    name = mask_pii(strip_control(name)).strip()
    name = name.replace("|", "/")           # 마크다운 표를 깨뜨리지 않게
    if len(name) > 120:
        name = name[:117] + "..."
    return name or "(이름 없음)"


def safe_cell(value):
    """CSV 셀 — 수식 인젝션 차단 + 제어문자 제거 + PII 마스킹."""
    text = strip_control("" if value is None else str(value))
    text = mask_pii(text).lstrip()
    if text[:1] in ("=", "+", "-", "@"):
        text = "'" + text
    return text


def safe_line(value):
    """콘솔·마크다운 한 줄 — 개행/제어문자 제거 + PII 마스킹."""
    return mask_pii(strip_control("" if value is None else str(value)))


# ------------------------------------------------------------------ 출력 경로

def prepare_out_dir(path):
    """--out-dir 를 검증하고 만든다. 문제가 있으면 한글 메시지로 OutputError."""
    path = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.islink(path):
        raise OutputError("출력 폴더가 심볼릭 링크입니다 — 원본을 덮어쓸 수 있어 중단합니다: %s" % safe_name(path))
    if os.path.exists(path) and not os.path.isdir(path):
        raise OutputError("출력 경로에 같은 이름의 파일이 이미 있습니다(폴더가 아닙니다): %s" % safe_name(path))
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        raise OutputError("출력 폴더를 만들 권한이 없습니다: %s" % safe_name(path))
    except OSError as exc:
        raise OutputError("출력 폴더를 만들지 못했습니다(%s): %s" % (exc.strerror or exc, safe_name(path)))
    if not os.access(path, os.W_OK):
        raise OutputError("출력 폴더에 쓸 권한이 없습니다: %s" % safe_name(path))
    # 상위 경로에 심볼릭 링크가 있으면(macOS 의 /tmp 가 그렇다) 실제로 쓰이는 곳을 돌려준다 —
    # 거부하지는 않되, 리포트에는 '어디에 썼는지'가 해소된 경로로 남는다.
    return os.path.realpath(path)


def _open_nofollow(target):
    """심볼릭/하드링크를 따라가지 않고 새로 쓰기 위해 연다."""
    if os.path.islink(target):
        raise OutputError(
            "산출물 경로가 심볼릭 링크입니다 — 다른 파일을 덮어쓸 수 있어 중단합니다: %s" % safe_name(target)
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        stat = os.lstat(target)
    except OSError:
        stat = None
    if stat is not None and getattr(stat, "st_nlink", 1) > 1:
        raise OutputError(
            "산출물 경로가 하드링크입니다 — 다른 파일을 덮어쓸 수 있어 중단합니다: %s" % safe_name(target)
        )
    try:
        fd = os.open(target, flags, 0o644)
    except OSError as exc:
        raise OutputError("산출물을 쓰지 못했습니다(%s): %s" % (exc.strerror or exc, safe_name(target)))
    return fd


def write_text(out_dir, filename, text):
    """텍스트 산출물 쓰기 (링크 추적 금지)."""
    target = os.path.join(out_dir, filename)
    fd = _open_nofollow(target)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return target


def write_csv(out_dir, filename, header, rows):
    """CSV 산출물 쓰기 — 모든 셀에 safe_cell 적용, BOM 포함(엑셀 한글 깨짐 방지)."""
    target = os.path.join(out_dir, filename)
    fd = _open_nofollow(target)
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([safe_cell(cell) for cell in header])
        for row in rows:
            writer.writerow([safe_cell(cell) for cell in row])
    return target
