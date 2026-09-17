"""출력 경로 검증과 안전한 산출물 쓰기.

이 툴은 **입력을 절대 건드리지 않는다.** 그런데 `--out-dir` 안에 산출물 이름의
심볼릭 링크를 심어 두면, 평범한 `open(path, 'w')` 는 링크를 따라가 **입력 파일을
덮어쓴다**. (이 저장소의 visitaudit·calmbark·circadia 가 실제로 이 결함을 달고
출시된 적이 있다.) 그래서 모든 쓰기는 이 모듈을 통과한다:

    · 대상이 심볼릭 링크면 거절 (`O_NOFOLLOW` 로 커널에도 한 번 더 못 박는다)
    · 대상이 하드링크(`nlink > 1`)면 거절 — 링크는 따라갈 것이 없어 `O_NOFOLLOW`
      가 막지 못한다. 덮어쓰면 다른 이름의 같은 파일이 같이 바뀐다
    · `--out-dir` 이 이미 **파일**이거나 권한이 없으면 트레이스백이 아니라
      한국어 한 줄 + 종료코드 2
"""

import os

from doseaudit.errors import RefuseError
from doseaudit.sanitize import safe_text


def prepare_out_dir(path):
    """`--out-dir` 을 검증하고 필요하면 만든다. 실패는 전부 `RefuseError`(exit 2).

    마지막 한 조각만 보지 않는다 — 상위 폴더가 심볼릭 링크면 거기를 따라
    엉뚱한 곳에 쓰게 되므로, **실제로 도달하는 경로**를 인쇄해 두고 검사한다.
    """
    if os.path.islink(path):
        raise RefuseError(
            "`--out-dir` 이 심볼릭 링크입니다: %s\n"
            "       링크를 따라가면 엉뚱한 폴더에 쓰게 되므로 거절합니다."
            % safe_text(os.path.basename(path.rstrip("/")) or path)
        )
    if os.path.exists(path) and not os.path.isdir(path):
        raise RefuseError(
            "`--out-dir` 이 폴더가 아니라 파일입니다: %s\n"
            "       덮어쓰지 않고 멈춥니다 — 다른 경로를 주세요."
            % safe_text(os.path.basename(path))
        )
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        raise RefuseError("`--out-dir` 을 만들 권한이 없습니다: %s" % safe_text(path))
    except OSError as exc:
        raise RefuseError("`--out-dir` 을 만들지 못했습니다 (%s): %s"
                          % (type(exc).__name__, safe_text(path)))
    if not os.access(path, os.W_OK | os.X_OK):
        raise RefuseError("`--out-dir` 에 쓸 권한이 없습니다: %s" % safe_text(path))

    return path


def resolved_note(path):
    """상위 폴더가 링크라 **실제로 쓰이는 곳**이 다르면 그 경로를 돌려준다.

    막지는 않는다 — macOS 의 `/tmp` 부터가 `/private/tmp` 로 가는 링크고,
    링크로 엮인 작업 폴더는 흔하다. 다만 **어디에 썼는지를 숨기지 않는다**:
    산출물을 나중에 못 찾는 일이 없도록 콘솔에 한 줄 덧붙인다.
    """
    resolved = os.path.realpath(path)
    return resolved if resolved != os.path.abspath(path) else None


def open_artifact(out_dir, filename):
    """산출물 하나를 안전하게 연다. 링크가 걸려 있으면 `RefuseError`.

    텍스트 파일 객체를 돌려주며, 호출부는 `with` 로 쓴다.
    """
    target = os.path.join(out_dir, filename)
    if os.path.islink(target):
        raise RefuseError(
            "출력 파일 자리에 심볼릭 링크가 있습니다: %s\n"
            "       따라가면 입력 파일을 덮어쓸 수 있으므로 거절합니다 — 링크를 치우세요."
            % safe_text(filename)
        )
    try:
        stat = os.lstat(target)
    except FileNotFoundError:
        stat = None
    except OSError as exc:
        raise RefuseError("출력 파일을 확인하지 못했습니다 (%s): %s"
                          % (type(exc).__name__, safe_text(filename)))
    if stat is not None and stat.st_nlink > 1:
        raise RefuseError(
            "출력 파일이 하드링크입니다(링크 수 %d): %s\n"
            "       덮어쓰면 같은 알맹이를 가리키는 다른 파일까지 바뀝니다 — 거절합니다."
            % (stat.st_nlink, safe_text(filename))
        )

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except OSError as exc:
        # ELOOP (심볼릭 링크) 를 포함한 모든 실패를 한국어 한 줄로 돌려준다.
        raise RefuseError("출력 파일을 열지 못했습니다 (%s): %s"
                          % (type(exc).__name__, safe_text(filename)))
    return os.fdopen(fd, "w", encoding="utf-8", newline="")


def preflight(out_dir, filenames, inputs=()):
    """산출물 **전부**를 쓰기 전에 한 번에 검사한다.

    하나씩 쓰다가 세 번째에서 거절하면 앞의 두 개만 남은 폴더가 생기고, 사용자는
    그걸 완전한 결과로 착각한다. 링크가 걸려 있다면 **아무것도 쓰기 전에** 멈춘다.

    `inputs` 에는 이 실행이 **읽은** 파일들을 준다. 산출물 이름이 그중 하나와
    같은 파일을 가리키면 거절한다 — `--tracker out/트래커불일치.csv --out-dir out/`
    한 번이면 입력 트래커가 진단 결과로 덮여 사라진다. 이 툴은 입력을 고치지
    않겠다고 했고, 그 약속에는 '실수로 덮어쓰지 않는다'가 포함된다.
    """
    guarded = []
    for path in inputs:
        if not path:
            continue
        try:
            guarded.append(os.stat(path))
        except OSError:
            continue
    for name in filenames:
        target = os.path.join(out_dir, name)
        try:
            existing = os.stat(target)
        except OSError:
            existing = None
        if existing is not None:
            for source in guarded:
                if (existing.st_dev, existing.st_ino) == (source.st_dev, source.st_ino):
                    raise RefuseError(
                        "산출물이 **입력 파일과 같은 파일**을 가리킵니다: %s\n"
                        "       쓰면 읽은 자료가 사라집니다 — 다른 `--out-dir` 을 주세요."
                        % safe_text(name))
        if os.path.islink(target):
            raise RefuseError(
                "출력 파일 자리에 심볼릭 링크가 있습니다: %s\n"
                "       따라가면 입력 파일을 덮어쓸 수 있으므로 거절합니다 — 링크를 치우세요."
                % safe_text(name))
        try:
            stat = os.lstat(target)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RefuseError("출력 파일을 확인하지 못했습니다 (%s): %s"
                              % (type(exc).__name__, safe_text(name)))
        if stat.st_nlink > 1:
            raise RefuseError(
                "출력 파일이 하드링크입니다(링크 수 %d): %s\n"
                "       덮어쓰면 같은 알맹이를 가리키는 다른 파일까지 바뀝니다 — 거절합니다."
                % (stat.st_nlink, safe_text(name)))
        if not os.access(target, os.W_OK):
            # 링크만 보고 넘어가면, 읽기 전용 파일 하나 때문에 세 번째 산출물에서
            # 멈추면서 앞의 두 개는 이미 쓰인 폴더가 남는다.
            raise RefuseError(
                "출력 파일에 쓸 권한이 없습니다: %s\n"
                "       하나라도 쓸 수 없으면 아무것도 쓰지 않습니다 — "
                "반쯤 채워진 결과 폴더는 완전한 결과로 오해됩니다."
                % safe_text(name))
