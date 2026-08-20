"""`.docx` / `.xlsx` 는 zip 입니다 — 열기 전에 폭탄을 먼저 봅니다.

표준 라이브러리 `zipfile` 은 압축 해제 크기를 제한하지 않고, `ElementTree` 는
DTD 엔티티 확장(billion laughs)에 취약합니다. 남이 보내 준 파일을 여는 툴이므로
둘 다 막습니다. 못 여는 파일은 조용히 넘기지 않고 **사유와 함께 자백**합니다.
"""

import posixpath
import xml.parsers.expat as expat
import zipfile

MAX_MEMBERS = 5000              # 정상 워드/엑셀 파일은 보통 수십~수백 개
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_MEMBER_BYTES = 80 * 1024 * 1024
MAX_RATIO = 250                 # 압축률이 이보다 높고 크기도 크면 폭탄으로 봅니다


class ArchiveError(Exception):
    """압축 파일을 안전하게 열 수 없을 때 (파일명만 노출, 내용은 노출하지 않음)."""


def open_zip(path: str) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ArchiveError("zip 형식이 아니거나 손상됨")
    except OSError:
        raise ArchiveError("파일을 열 수 없음")
    try:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ArchiveError("압축 항목이 %d개로 상한(%d) 초과"
                               % (len(infos), MAX_MEMBERS))
        total = 0
        for info in infos:
            name = info.filename
            if name.startswith("/") or name.startswith("\\"):
                raise ArchiveError("압축 안에 절대 경로 항목이 있음")
            if len(name) > 1 and name[1] == ":":
                raise ArchiveError("압축 안에 드라이브 경로 항목이 있음")
            if ".." in posixpath.normpath(name).split("/"):
                raise ArchiveError("압축 안에 경로 탈출 항목이 있음")
            total += info.file_size
            if (info.compress_size > 0 and info.file_size > (1 << 20)
                    and info.file_size / info.compress_size > MAX_RATIO):
                raise ArchiveError("압축 폭탄 의심(압축률 %.0f배)"
                                   % (info.file_size / info.compress_size))
        if total > MAX_TOTAL_BYTES:
            raise ArchiveError("압축 해제 크기 %.0fMB 로 상한(%dMB) 초과"
                               % (total / 1024 / 1024, MAX_TOTAL_BYTES // 1024 // 1024))
    except ArchiveError:
        zf.close()
        raise
    except Exception:
        zf.close()
        raise ArchiveError("압축 목록을 읽을 수 없음")
    return zf


def read_member(zf: zipfile.ZipFile, name: str,
                limit: int = MAX_MEMBER_BYTES) -> bytes:
    try:
        with zf.open(name) as handle:
            data = handle.read(limit + 1)
    except KeyError:
        raise ArchiveError("필요한 항목이 없음: %s" % name)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError):
        # RuntimeError = 암호가 걸린 항목
        raise ArchiveError("항목을 읽을 수 없음(암호 또는 손상): %s" % name)
    if len(data) > limit:
        raise ArchiveError("항목 크기가 상한을 넘음: %s" % name)
    return data


def guard_xml(data: bytes, what: str = "XML") -> bytes:
    """DTD/엔티티가 들어 있는 XML 은 파싱하지 않습니다(엔티티 폭탄 방어).

    바이트 문자열에서 `<!DOCTYPE` 를 찾는 방식은 **뚫립니다** — UTF-16 로 저장하면
    `<\\x00!\\x00D…` 가 되어 리터럴이 안 맞고, 선언 앞에 8KB 짜리 주석을 넣으면
    앞부분만 보는 검사를 피해 갑니다(둘 다 실제로 재현했습니다).
    그래서 파서(expat)에게 직접 물어봅니다. 선언 핸들러가 한 번이라도 불리면 거부합니다.
    """
    parser = expat.ParserCreate()

    def refuse(*_args):
        raise _DeclarationFound()

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = lambda *a: False
    try:
        parser.Parse(data, True)
    except _DeclarationFound:
        raise ArchiveError("%s 에 DTD/엔티티 선언이 있어 파싱을 거부함" % what)
    except expat.ExpatError as exc:
        raise ArchiveError("%s 이 손상됨(%s)" % (what, expat.errors.messages.get(
            exc.code, "형식 오류")))
    return data


class _DeclarationFound(Exception):
    """내부 신호 — 밖으로 나가지 않습니다."""
