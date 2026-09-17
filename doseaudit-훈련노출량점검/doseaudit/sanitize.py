"""출력에 들어가는 모든 외부 문자열의 무해화.

파일명·시트명·피험자 코드는 전부 **외부 입력**이다. 파일명에 개행과 `[치명]` 을
심으면 리포트 한 줄을 통째로 위조할 수 있다. 콘솔·마크다운·CSV 어디로 나가든
이 모듈을 통과한 문자열만 쓴다.
"""

import re
import unicodedata

#: 제어문자 중 보이지 않게 줄을 갈아타거나 커서를 되돌릴 수 있는 것들.
#: 개행(\n\r)·탭·수직탭·폼피드·백스페이스·ESC(ANSI 이스케이프)·NUL 을 포함한다.
_CONTROL = {c for c in range(0x20)} | {0x7F} | {c for c in range(0x80, 0xA0)}

#: 방향 재정의(RTL override) 계열 — 파일명을 눈으로 뒤집어 보이게 만든다.
_BIDI = {0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
         0x2066, 0x2067, 0x2068, 0x2069}

#: `\n` 은 아니지만 **줄바꿈으로 읽히는** 문자들. 파이썬 `splitlines()`·자바스크립트·
#: 다수의 마크다운 렌더러가 여기서 줄을 가르므로, 시트 이름 하나로 가짜 `[치명]`
#: 줄을 만들 수 있다. (적대 패널 2라운드)
_LINE_SEPARATORS = {0x2028, 0x2029, 0x0085, 0x000B, 0x000C}

#: 눈에 보이지 않는 채로 맨 앞에 앉아, 수식 방어의 "첫 글자" 판정을 비껴가는 것들.
#: (`"\ufeff=cmd|..."` 가 따옴표 없이 CSV 로 나가면 엑셀이 실행할 수 있다.)
_INVISIBLE = {0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0x180E}

_REPLACEMENT = "�"

#: Access Code 의 생김새(영숫자 2자 이상). 이게 아니면 사람 이름일 수 있다고 본다.
_CODE_SHAPE = re.compile(r"^[A-Za-z0-9]{2,32}$")

#: 파일명 토큰이 "사람 이름일 리 없다"고 볼 수 있는 생김새.
#: 끝의 일련번호는 한 자리일 수 있으므로(`..._1.xlsx`) 최소 길이를 두지 않는다.
_TOKEN_SAFE = re.compile(r"^[A-Za-z0-9]{1,32}$")


def safe_text(value, max_len=120):
    """외부 문자열을 한 줄짜리 안전한 표시용 문자열로 바꾼다.

    - 제어문자·ANSI 이스케이프·양방향 재정의 문자를 `\\ufffd` 로 치환한다
      (지우지 않고 치환한다 — 지우면 '뭔가 있었다'는 사실까지 사라진다).
    - macOS 파일명의 NFD 를 NFC 로 정규화한다(`한글` 이 자모로 쪼개져 들어온다).
    - `max_len` 을 넘으면 말줄임한다.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = unicodedata.normalize("NFC", text)
    out = []
    for ch in text:
        cp = ord(ch)
        out.append(_REPLACEMENT
                   if (cp in _CONTROL or cp in _BIDI or cp in _INVISIBLE
                       or cp in _LINE_SEPARATORS)
                   else ch)
    text = "".join(out)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def nfc(value):
    """NFC 정규화만 한다 (조인 키 비교용).

    macOS 는 파일명을 NFD 로 준다. `참여현황.xlsx` 의 Access Code 는 NFC 다.
    정규화하지 않고 비교하면 같은 코드가 다른 사람이 된다.
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFC", value if isinstance(value, str) else str(value))


def mask_filename(filename, mask_head=False):
    """`3D609A5O_mystart_81.xlsx` → `3D609A5O_…_81.xlsx`.

    파일명의 가운데 토큰은 **Login ID** 다. 조인에 쓰지 않으면서 화면에만 남으면
    스크린샷 한 장으로 새어 나간다. 어떤 파일을 읽었는지는 첫 토큰과 끝 번호로
    충분히 알아볼 수 있으므로, 가운데만 가린다.

    첫 토큰이 Access Code 규격(영숫자 2자 이상)이 아니면 **언제나** 가린다 —
    코드가 아니라면 그것은 사람 이름일 수 있기 때문이다(`홍길동_hong1234_1.xlsx`).
    `mask_head` 는 호출부가 "이건 워크북이 아니다"라고 알려 주는 표시로 남겨 둔다.

    마지막 토큰도 검사한다. `2026_동의서_김철수.pdf` 처럼 **끝에** 이름이 붙는
    파일이 벤더 폴더에 섞여 들어온다 (적대 패널 2라운드가 뚫은 자리).
    """
    text = safe_text(filename, max_len=80)
    stem, dot, ext = text.rpartition(".")
    if not dot:
        stem, ext = text, ""
    # **모든 토큰**을 검사한다. 가운데만 가리면 `2026_동의서_김철수.pdf` 의 마지막
    # 토큰이 그대로 나간다 — 벤더 폴더에는 동의서·메모가 섞여 들어오고, 그 이름에
    # 사람 이름이 붙어 있다. (적대 패널 2라운드가 실제로 뚫은 자리다.)
    parts = stem.split("_")
    masked = []
    for index, token in enumerate(parts):
        if index == 0 and not mask_head:
            masked.append(token)
        elif _TOKEN_SAFE.match(token):
            masked.append(token)
        else:
            masked.append("?")
    # 가운데 토큰(Login ID)은 코드 규격이어도 가린다 — 조인에 쓰지 않는 값이다.
    if len(masked) >= 3:
        masked[1:-1] = ["\u2026"]
    elif len(masked) == 2:
        masked[1] = "\u2026" if _TOKEN_SAFE.match(parts[1]) else "?"
    # 첫 토큰이 Access Code 규격이 아니면 **언제나** 가린다.
    # (`mask_head` 는 호출부의 의도를 남겨 두는 표시다 — 지금은 이 한 줄이
    #  두 경우를 모두 덮는다. 조건을 나눠 쓰면 절대 실행되지 않는 가지가 생긴다.)
    if masked and not _CODE_SHAPE.match(parts[0]):
        masked[0] = "?"
    stem = "_".join(masked)
    return stem + (("." + ext) if dot else "")


#: 벤더 `[요약]` 칸에서 **그대로 인쇄해도 되는** 모양: 숫자 + 짧은 단위.
#: 이 칸은 벤더가 채우는 자유 텍스트라, 담당자 실명이 적혀 오는 일이 실제로 있다.
_VENDOR_VALUE_OK = re.compile(r"^[\s0-9.,+\-]*[가-힣A-Za-z%]{0,6}$")

#: 모양이 아닌 값을 대신할 표시. 값 자체는 인쇄하지 않는다.
VENDOR_VALUE_MASKED = "숫자가 아닌 값(가려짐)"


def vendor_display(raw):
    """벤더 `[요약]` 값을 **인쇄해도 되는 형태로** 돌려준다.

    숫자+단위 모양이면 그대로, 아니면 가린다. 이 값은 콘솔과 공유용
    `노출량점검.md` 에 그대로 실리는데, 거르지 않으면 칸에 적힌 사람 이름이
    리포트로 새고 `[치명]` 줄을 위조하는 통로가 된다 — 파일명·시트이름에
    대해서는 이미 막아 둔 구멍이다. 숫자가 아닌 값은 **어차피 재계산에
    쓰이지 않으므로**, 원문을 보여 줄 이유가 없다.
    """
    text = safe_text(raw, max_len=60)
    return text if _VENDOR_VALUE_OK.match(text) else VENDOR_VALUE_MASKED
