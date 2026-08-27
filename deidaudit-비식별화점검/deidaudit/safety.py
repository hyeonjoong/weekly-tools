"""경로 안전성과 CSV 수식 인젝션 방어.

이 툴의 존재 이유가 "유출을 막는 것"이므로, 툴 자신이 유출 경로가 되는
길을 코드로 막습니다.

* 키 파일(원본ID↔가명ID, 날짜 오프셋)은 `--out-dir` 안에 절대 쓰이지 않습니다.
  내보낼 폴더를 통째로 압축해 보내는 것이 정상 사용 패턴이기 때문입니다.
  심볼릭 링크를 경유한 우회도 realpath 로 막습니다.
* 산출물은 `--out-dir` 아래에만 씁니다(경로 탈출 방어).
* CSV 셀이 스프레드시트에서 수식으로 실행되지 않도록 이스케이프합니다.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# 스프레드시트가 수식으로 해석하는 선두 문자들.
_FORMULA_LEAD = ("=", "+", "@", "\t", "\r", "\x00")

# "-3.5", "-1e9" 처럼 정상적인 음수는 이스케이프하지 않습니다.
# (내보내기 사본의 수치를 조용히 망가뜨리면 분석이 깨집니다.)
_NUMERIC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class PathSafetyError(Exception):
    """경로 안전성 위반 — 호출자가 종료코드 2로 바꿔 보고합니다."""


def real(path) -> Path:
    """심볼릭 링크를 모두 푼 절대경로를 돌려줍니다(존재하지 않아도 동작)."""
    return Path(os.path.realpath(os.path.abspath(os.path.expanduser(str(path)))))


def _compare_key(path: Path) -> tuple:
    """파일시스템이 같다고 볼 경로를 같은 키로 만듭니다.

    macOS(APFS/HFS+)는 **대소문자와 유니코드 정규화 형태를 구별하지 않습니다.**
    `realpath` 는 둘 다 그대로 두므로, 문자열만 비교하면 `OUT` 과 `out`,
    NFC `내보내기` 와 NFD `내보내기` 가 서로 다른 폴더로 보입니다 —
    그 틈으로 키 파일이 내보내기 폴더 안에 떨어집니다.
    """
    parts = []
    for piece in path.parts:
        normalized = unicodedata.normalize("NFC", piece)
        parts.append(normalized.casefold())
    return tuple(parts)


def lexical(path) -> Path:
    """심볼릭 링크를 **풀지 않은** 절대경로(`.`/`..` 만 정리)."""
    return Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(path)))))


def _contains(child: Path, parent: Path) -> bool:
    c = _compare_key(child)
    p = _compare_key(parent)
    if c == p:
        return True
    return len(c) > len(p) and c[: len(p)] == p


def is_within(child, parent) -> bool:
    """`child` 가 `parent` 아래(또는 같은 경로)인지 판정합니다.

    **경로 문자열 그대로(lexical)와 심볼릭 링크를 푼 뒤(real) 둘 다** 봅니다.
    `--out-dir/보안` 이 밖을 가리키는 심볼릭 링크면 realpath 로는 '밖'이지만,
    `zip -r` 은 기본적으로 심볼릭 링크를 따라가므로 압축하면 **안에 들어갑니다.**
    대소문자·유니코드 정규화 차이도 같은 것으로 봅니다.
    """
    return _contains(lexical(child), lexical(parent)) or _contains(real(child), real(parent))


def same_file(a, b) -> bool:
    """두 경로가 **같은 실제 파일**인지 inode 로 확인합니다(하드링크 포함)."""
    try:
        sa, sb = os.stat(str(a)), os.stat(str(b))
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def find_inside(target, directory) -> Optional[Path]:
    """`directory` 안에 `target` 과 **같은 실제 파일**이 있으면 그 경로를 돌려줍니다.

    문자열 비교로는 하드링크를 잡을 수 없습니다. 키 파일을 쓴 뒤 이 검사를
    통과하지 못하면 호출자는 그 파일을 지우고 실패해야 합니다.
    """
    directory = Path(str(directory))
    if not directory.exists():
        return None
    try:
        target_stat = os.stat(str(target))
    except OSError:
        return None
    # `Path.rglob` 은 심볼릭 링크 디렉터리로 내려가지 않습니다 — `zip -r` 은 따라가므로
    # 여기서도 따라가야 합니다(순환은 방문한 실제 경로로 차단).
    seen = set()
    stack = [directory]
    while stack:
        current = stack.pop()
        key = str(real(current))
        if key in seen:
            continue
        seen.add(key)
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_dir():
                    stack.append(path)
                    continue
                st = path.stat()
            except OSError:
                continue
            if (st.st_dev, st.st_ino) == (target_stat.st_dev, target_stat.st_ino):
                return path
    return None


def ensure_key_outside(key_out, out_dir) -> None:
    """키 파일이 내보내기 폴더 안에 있으면 거부합니다.

    Raises:
        PathSafetyError: 키 파일 경로가 `out_dir` 하위이거나 같은 경로일 때.
    """
    if key_out is None or out_dir is None:
        return
    if is_within(key_out, out_dir):
        raise PathSafetyError(
            f"--key-out 경로가 --out-dir 안에 있습니다.\n"
            f"  --key-out: {real(key_out)}\n"
            f"  --out-dir: {real(out_dir)}\n"
            "  내보낼 폴더를 통째로 압축해 보내는 순간 매핑표까지 함께 나갑니다.\n"
            "  키 파일은 반드시 내보내기 폴더 **밖**(예: ~/보안/)에 두세요."
        )


def ensure_output_target(path, out_dir) -> Path:
    """산출물 경로가 `out_dir` 밖으로 탈출하지 않는지 확인합니다."""
    target = real(path)
    if not is_within(target, out_dir):
        raise PathSafetyError(f"산출물 경로가 --out-dir 밖입니다: {target}")
    return target


def ensure_not_input(target, input_paths: Iterable) -> None:
    """산출물이 입력 파일을 덮어쓰지 않는지 확인합니다(원본 읽기 전용 보장)."""
    t = real(target)
    for p in input_paths:
        if real(p) == t:
            raise PathSafetyError(f"산출물이 입력 파일과 같은 경로입니다(원본 보호): {t}")


def sanitize_cell(value: str) -> str:
    """CSV 셀을 수식 인젝션에서 안전하게 만듭니다.

    `=`, `+`, `@`, 탭/CR 로 시작하는 값과, 숫자가 아닌데 `-` 로 시작하는 값
    앞에 작은따옴표를 붙입니다. **정상적인 음수는 그대로 둡니다** — 내보내기
    사본의 수치를 조용히 바꾸면 분석이 깨지기 때문입니다.
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return text
    lead = text[0]
    if lead in _FORMULA_LEAD:
        return "'" + text
    if lead == "-" and not _NUMERIC_RE.match(text.strip()):
        return "'" + text
    return text


def needs_escaping(value: str) -> bool:
    """이 값이 CSV 수식 이스케이프 대상인지."""
    return sanitize_cell(value) != value


def write_csv(
    path: Path, header: Sequence[str], rows: Iterable[Sequence], *, sanitize: bool = True, private: bool = False
) -> int:
    """UTF-8(BOM) CSV 를 씁니다. 엑셀에서 바로 열리도록 BOM 을 붙입니다.

    Args:
        private: True 면 **생성 시점부터** 0600 으로 만듭니다(쓰고 나서 chmod 하면
            그 사이에 다른 사용자가 읽을 수 있는 창이 생깁니다).

    Returns:
        쓴 데이터 행 수.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    opener = open_private(path) if private else open(path, "w", encoding="utf-8-sig", newline="")
    with opener as fh:
        writer = csv.writer(fh)
        writer.writerow([sanitize_cell(h) if sanitize else h for h in header])
        for row in rows:
            writer.writerow([sanitize_cell(c) if sanitize else ("" if c is None else str(c)) for c in row])
            count += 1
    return count


def write_text(path: Path, text: str, *, private: bool = False) -> None:
    """UTF-8 텍스트 파일을 씁니다.

    Args:
        private: True 면 생성 시점부터 0600 (그리고 기존 심볼릭 링크를 따라가지
            않고 지운 뒤 새로 만듭니다 — 링크를 통한 밖으로의 쓰기 차단).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = open_private(path, "utf-8") if private else open(path, "w", encoding="utf-8")
    with opener as fh:
        fh.write(text)


def restrict(path: Path) -> None:
    """소유자만 읽고 쓸 수 있게 합니다(리포트도 원본과 같은 취급)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def open_private(path: Path, encoding: str = "utf-8-sig"):
    """0600 으로 **생성 시점부터** 잠긴 파일을 엽니다(쓰고 나서 chmod 하면 창이 열립니다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    return os.fdopen(fd, "w", encoding=encoding, newline="")


def write_private_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence], *, sanitize: bool = False) -> int:
    """0600 으로 생성된 CSV 를 씁니다(키 파일용).

    기본값이 `sanitize=False` 인 이유: 키 파일은 원본 ID 로 되돌아가는 **유일한**
    경로입니다. `=A1` 같은 ID 앞에 따옴표를 붙이면 원자료와 조인이 깨져
    되돌릴 수 없게 됩니다. 대신 이스케이프가 필요한 값이 있으면 호출자가
    경고를 띄웁니다.
    """
    count = 0
    with open_private(path) as fh:
        writer = csv.writer(fh)
        writer.writerow([sanitize_cell(h) if sanitize else h for h in header])
        for row in rows:
            writer.writerow([sanitize_cell(c) if sanitize else ("" if c is None else str(c)) for c in row])
            count += 1
    return count


def file_sha256(path) -> str:
    """파일 내용의 SHA-256 (테스트에서 원본 불변 검증에 사용)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_output_name(name: str) -> str:
    """입력 파일명을 산출물 파일명으로 쓸 때 경로 구분자를 제거합니다."""
    cleaned = re.sub(r"[\\/]+", "_", name).strip()
    cleaned = cleaned.replace("..", "_")
    cleaned = re.sub(r"[\x00-\x1f]", "_", cleaned)
    return cleaned or "무제"


def unique_path(directory: Path, filename: str) -> Path:
    """같은 이름이 이미 있으면 `_2`, `_3` … 을 붙여 겹치지 않는 경로를 만듭니다."""
    base = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / f"{base}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{base}_{n}{suffix}"
        n += 1
    return candidate


def list_output_files(out_dir: Path) -> List[Path]:
    """산출물 목록(정렬)."""
    if not out_dir.exists():
        return []
    return sorted(p for p in out_dir.rglob("*") if p.is_file())
