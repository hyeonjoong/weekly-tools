"""PDF 텍스트 레이어 추출 (표준 라이브러리만).

의도적으로 **소박한** 추출기다. 목표는 "읽히면 읽고, 안 읽히면 못 읽었다고
말하는 것" 이지, 완전한 PDF 렌더링이 아니다:

  * FlateDecode 콘텐츠 스트림에서 Tj/TJ/'/" 의 문자열만 모은다.
  * 한글은 대부분 CID 코드(16진 문자열)라서 폰트의 ToUnicode CMap
    (beginbfchar/beginbfrange)을 모아 코드→문자로 되돌린다.
  * 되돌리지 못한 글자 비율이 높으면 **읽지 못한 문서로 보고**한다
    (조용히 깨진 글자를 점검에 넣는 쪽이 훨씬 위험하다).

스캔 이미지 PDF·암호화 PDF 는 처음부터 "못 읽음" 이다.
"""

import re
import zlib

MIN_MAPPED_RATIO = 0.60      # 이보다 많이 깨지면 못 읽은 문서로 센다
MIN_CHARS = 40               # 이보다 적게 나오면 텍스트 레이어가 없다고 본다
MAX_STREAMS = 4000

_DICT_STREAM = re.compile(rb"<<(?P<dict>(?:[^<>]|<<(?:[^<>]|<<[^<>]*>>)*>>)*)>>\s*stream\r?\n", re.S)
_BFCHAR = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_HEXPAIR = re.compile(rb"<([0-9A-Fa-f]+)>")


_FONT_HINT = re.compile(
    rb"/(Length1|Length2|Type1C|FontFile\d?|CIDFontType\d?|OpenType)\b|/Subtype\s*/(Image|Type1C|OpenType|TrueType)\b"
)


def _is_font_program(head):
    """내장 폰트·이미지 스트림인가 (본문 텍스트가 아니다).

    '/Length 12673' 와 '/Length1 268664' 를 구분해야 하므로 단어 경계를 지킨다.
    """
    return bool(_FONT_HINT.search(head))


MAX_INFLATED = 4 * 1024 * 1024       # 스트림 하나당 상한 (압축 폭탄 방어)
MAX_TOTAL_CONTENT = 24 * 1024 * 1024  # 훑어볼 콘텐츠 전체 상한 — 넘으면 못 읽은 문서로 본다


def _inflate(payload):
    """FlateDecode 스트림을 상한선까지만 푼다."""
    try:
        return zlib.decompressobj().decompress(payload, MAX_INFLATED)
    except zlib.error:
        return None


def _hex_to_units(raw):
    """'0041' → [0x41] (2바이트 단위 우선, 홀수 길이는 1바이트). 16진수가 아니면 []."""
    text = re.sub(r"\s", "", raw.decode("ascii", "ignore"))
    if not text or not all(char in "0123456789abcdefABCDEF" for char in text):
        return []
    if len(text) % 4 == 0 and len(text) >= 4:
        return [int(text[i:i + 4], 16) for i in range(0, len(text), 4)]
    if len(text) % 2 == 0:
        return [int(text[i:i + 2], 16) for i in range(0, len(text), 2)]
    return []


def _decode_target(raw):
    """bfchar/bfrange 의 목적지 <...> 를 UTF-16BE 문자열로."""
    text = raw.decode("ascii", "ignore")
    if len(text) % 2:
        text = text[:-1]
    try:
        data = bytes.fromhex(text)
    except ValueError:
        return ""
    try:
        return data.decode("utf-16-be", "ignore")
    except (UnicodeDecodeError, LookupError):
        return ""


def _parse_cmap(payload, table):
    """ToUnicode CMap 조각을 코드→문자 표에 합친다."""
    for chunk in _BFCHAR.findall(payload):
        items = _HEXPAIR.findall(chunk)
        for index in range(0, len(items) - 1, 2):
            codes = _hex_to_units(items[index])
            target = _decode_target(items[index + 1])
            if len(codes) == 1 and target:
                table.setdefault(codes[0], target)
    for chunk in _BFRANGE.findall(payload):
        items = _HEXPAIR.findall(chunk)
        index = 0
        while index + 2 < len(items) + 1 and index + 2 <= len(items) - 1:
            low = _hex_to_units(items[index])
            high = _hex_to_units(items[index + 1])
            target = _decode_target(items[index + 2])
            index += 3
            if len(low) != 1 or len(high) != 1 or not target:
                continue
            span = min(high[0] - low[0], 65535)
            if span < 0:
                continue
            base = ord(target[-1])
            prefix = target[:-1]
            for offset in range(span + 1):
                table.setdefault(low[0] + offset, prefix + chr(min(base + offset, 0x10FFFF)))


_ESCAPES = {b"n": "\n", b"r": "\n", b"t": "\t", b"b": "", b"f": "", b"(": "(", b")": ")", b"\\": "\\"}


def _literal_string(data, start):
    """(...) 문자열을 읽어 (원시 바이트, 다음 위치) 로."""
    depth = 1
    index = start
    out = bytearray()
    while index < len(data) and depth:
        char = data[index:index + 1]
        if char == b"\\":
            nxt = data[index + 1:index + 2]
            if nxt in _ESCAPES:
                out.extend(_ESCAPES[nxt].encode("latin-1", "ignore"))
                index += 2
                continue
            if nxt.isdigit():
                digits = data[index + 1:index + 4]
                digits = digits[:len(digits) - (len(digits) - len(re.match(rb"[0-7]*", digits).group(0)))]
                if digits:
                    out.append(int(digits, 8) & 0xFF)
                    index += 1 + len(digits)
                    continue
            index += 2
            continue
        if char == b"(":
            depth += 1
        elif char == b")":
            depth -= 1
            if not depth:
                index += 1
                break
        out.extend(char)
        index += 1
    return bytes(out), index


_NUMBER = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_OPERATOR = re.compile(rb"[A-Za-z*'\"]+")


def _extract_content_text(payload, table, stats):
    """콘텐츠 스트림에서 표시 문자열을 뽑되, 줄바꿈 위치를 좌표로 판단한다.

    PDF 는 글자마다 위치를 새로 지정하는 경우가 흔해서(한글 문서가 특히 그렇다)
    Td/TD/Tm/T* 의 **세로 이동량이 0인지**로 같은 줄 여부를 가른다.
    그렇게 하지 않으면 '연/구/계/획' 처럼 한 글자씩 잘린다.
    """
    lines = []
    current = []
    pending = []
    numbers = []
    last_y = None
    index = 0
    length = len(payload)

    def flush_line():
        if current:
            lines.append("".join(current))
            del current[:]

    def flush_text():
        if not pending:
            return
        text = "".join(
            _render(kind, raw, table, stats) if kind != "kern" else raw
            for kind, raw in pending
        )
        del pending[:]
        current.append(text)

    while index < length:
        char = payload[index:index + 1]
        if char in b" \t\r\n":
            index += 1
            continue
        if char == b"(":
            raw, index = _literal_string(payload, index + 1)
            pending.append(("lit", raw))
            continue
        if char == b"<":
            if payload[index + 1:index + 2] == b"<":
                end = payload.find(b">>", index + 2)
                index = (end + 2) if end != -1 else length
                continue
            end = payload.find(b">", index)
            if end == -1:
                break
            pending.append(("hex", payload[index + 1:end]))
            index = end + 1
            continue
        if char == b"/":
            match = re.match(rb"/[^\s/\[\]<>(){}]*", payload[index:])
            index += match.end() if match else 1
            continue
        if char in b"[]{}":
            index += 1
            continue
        match = _NUMBER.match(payload, index)
        if match:
            value = float(match.group(0))
            numbers.append(value)
            if pending:                       # TJ 배열 안의 커닝 — 크면 띄어쓰기
                if value <= -180:
                    pending.append(("kern", " "))
            index = match.end()
            continue
        match = _OPERATOR.match(payload, index)
        if not match:
            index += 1
            continue
        operator = match.group(0)
        index = match.end()
        if operator in (b"Tj", b"TJ"):
            flush_text()
        elif operator in (b"'", b'"'):
            flush_line()
            flush_text()
        elif operator in (b"Td", b"TD"):
            delta_y = numbers[-1] if numbers else 0.0
            if abs(delta_y) > 0.01:
                flush_text()
                flush_line()
        elif operator == b"T*":
            flush_text()
            flush_line()
        elif operator == b"Tm":
            new_y = numbers[-1] if numbers else None
            if last_y is not None and new_y is not None and abs(new_y - last_y) > 0.01:
                flush_text()
                flush_line()
            last_y = new_y
        elif operator in (b"BT", b"ET"):
            flush_text()
            flush_line()
            last_y = None
        del numbers[:]
    flush_text()
    flush_line()
    return lines


def _render(kind, raw, table, stats):
    if kind == "hex":
        units = _hex_to_units(raw)
        out = []
        for unit in units:
            mapped = table.get(unit)
            if mapped is None and unit < 0x100:
                mapped = table.get(unit)
            if mapped is None:
                stats["unmapped"] += 1
                out.append("�")
            else:
                stats["mapped"] += 1
                out.append(mapped)
        return "".join(out)
    text = raw.decode("latin-1", "ignore")
    if table:
        rebuilt = []
        for char in text:
            mapped = table.get(ord(char))
            if mapped is not None:
                stats["mapped"] += 1
                rebuilt.append(mapped)
            else:
                stats["mapped" if char.isprintable() else "unmapped"] += 1
                rebuilt.append(char)
        return "".join(rebuilt)
    stats["mapped"] += len(text)
    return text


def extract_pdf_text(path):
    """(줄 목록, 오류메시지, 비고) — 오류메시지가 있으면 '못 읽은 문서'."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return [], "파일을 열지 못했습니다(%s)" % exc, ""
    if not data.startswith(b"%PDF"):
        return [], "PDF 형식이 아닙니다", ""
    if re.search(rb"/Encrypt\s+\d+\s+\d+\s+R", data):
        return [], "암호가 걸린 PDF 입니다", ""

    table = {}
    contents = []
    budget = MAX_TOTAL_CONTENT
    truncated = False
    for count, match in enumerate(_DICT_STREAM.finditer(data)):
        if count >= MAX_STREAMS:
            break
        head = match.group("dict")
        body_start = match.end()
        body_end = data.find(b"endstream", body_start)
        if body_end == -1:
            continue
        payload = data[body_start:body_end]
        if b"/FlateDecode" in head:
            payload = _inflate(payload)
            if payload is None:
                continue
        elif b"/Filter" in head:
            continue                                  # DCT/CCITT 등 이미지 스트림
        if b"beginbfchar" in payload or b"beginbfrange" in payload:
            _parse_cmap(payload, table)
        elif _is_font_program(head):
            continue          # 내장 폰트 프로그램 — 안에 'Tj' 바이트가 우연히 들어 있다
        elif b"BT" in payload and (b"Tj" in payload or b"TJ" in payload):
            if budget <= 0:
                truncated = True
                continue
            contents.append(payload[:budget])
            budget -= len(payload)

    if not contents:
        return [], "텍스트 레이어가 없습니다(스캔 이미지 PDF로 보입니다)", ""

    stats = {"mapped": 0, "unmapped": 0}
    lines = []
    for payload in contents:
        lines.extend(_extract_content_text(payload, table, stats))
    lines = [line.strip() for line in lines if line and line.strip()]
    total = stats["mapped"] + stats["unmapped"]
    if total == 0 or sum(len(line) for line in lines) < MIN_CHARS:
        return [], "텍스트 레이어에서 글자를 거의 찾지 못했습니다(스캔본이거나 폰트가 내장되지 않음)", ""
    ratio = stats["mapped"] / float(total)
    if ratio < MIN_MAPPED_RATIO:
        return [], ("PDF 글자를 해독하지 못했습니다(글자 되돌리기 성공률 %.0f%% — "
                    "폰트 ToUnicode 정보 없음). .docx 로 주시면 읽습니다" % (ratio * 100)), ""
    note = ""
    if truncated or budget <= 0:
        note = ("PDF 내용이 너무 커서 앞부분 %dMB 만 읽었습니다 — 뒤쪽 값은 빠졌을 수 있습니다"
                % (MAX_TOTAL_CONTENT // (1024 * 1024)))
    if ratio < 0.98:
        note = "PDF 텍스트 레이어에서 글자 %.0f%% 만 해독했습니다 — 누락된 값이 있을 수 있습니다" % (ratio * 100)
    return lines, "", note
