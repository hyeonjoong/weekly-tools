#!/bin/bash
# calmbark 로컬 서버 실행기
#
# 왜 서버가 필요한가: 짖음 감지는 마이크(getUserMedia)를 쓰는데, 브라우저는
# 보안 문맥(secure context)에서만 마이크를 허용한다. file:// 로 열면 마이크가
# 막히므로 localhost(127.0.0.1) HTTP 서빙이 필수다. Python 은 이 서버 역할로만
# 쓰인다 — 앱 자체는 순수 HTML/JS 이고 네트워크 요청이 없다.
#
# 안전: 서버는 127.0.0.1 (이 컴퓨터 안) 에만 바인딩된다. 같은 네트워크의 다른
# 기기에서는 접근할 수 없다. 종료는 Ctrl+C.

cd "$(dirname "$0")" || exit 1

echo "=================================================================="
echo "  calmbark — 반려견 안정 사운드 N-of-1 실험"
echo "=================================================================="
echo "  짖음을 마이크로 감지해 논문(NBR 1st revision) 티어 프레임워크로"
echo "  합성한 저각성 사운드를 자동 재생하고, 모든 에피소드·개입 여부를"
echo "  CSV 로 남깁니다. 효과를 주장하지 않고 측정합니다."
echo ""
echo "    · 감지: 250–4000 Hz 대역 에너지 휴리스틱 (ML 아님 — 오탐 가능)"
echo "    · 사운드: 전부 실시간 합성 — 파일 0개, 초음파 성분 0"
echo "    · 데이터: 이 컴퓨터 밖으로 나가지 않음 (네트워크 요청 0, 녹음 저장 0)"
echo "    · 산출 CSV → statwise(개입/비개입 비교) · longistat(일차 추이)"
echo ""
echo "  ⚠ 볼륨 주의: 개의 청각은 사람보다 민감합니다. 기본 볼륨은 보수적이며"
echo "    상한 50%입니다. 개가 불안 반응을 보이면 즉시 중단하세요."
echo "=================================================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "[오류] python3 를 찾을 수 없습니다. macOS 에는 보통 기본 포함되어 있습니다."
  echo "       xcode-select --install 또는 python.org 설치 후 다시 실행하세요."
  read -r -p "엔터를 누르면 창이 닫힙니다..." || true
  exit 1
fi

# 빈 포트를 OS 에서 직접 받아온다 (포트 충돌 원천 회피)
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
URL="http://127.0.0.1:${PORT}/"

python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
SERVER_PID=$!
# 라운드 1 M3: 어떤 경로로 죽어도(SIGPIPE 포함) 서버가 고아로 남지 않게 —
# EXIT 트랩이 최종 청소를 맡고, PIPE/HUP 은 명시적으로 exit 시켜 EXIT 트랩을 태운다
trap 'kill "$SERVER_PID" 2>/dev/null' EXIT
trap 'echo ""; echo "서버를 종료합니다."; exit 0' INT TERM
trap 'exit 1' PIPE HUP

# 서버 기동 대기 (최대 ~5초)
READY=0
for _ in $(seq 1 50); do
  if curl -s -o /dev/null "$URL"; then READY=1; break; fi
  sleep 0.1
done
if [ "$READY" != 1 ]; then
  echo "[오류] 서버가 시작되지 않았습니다 (포트 ${PORT})."
  kill "$SERVER_PID" 2>/dev/null
  exit 1
fi

if [ ! -t 0 ]; then
  # 비대화형 (자동 점검 등): 스모크 테스트만 하고 종료 — 절대 매달리지 않는다
  echo "[비대화형 모드] 서버 스모크 테스트를 수행합니다."
  if curl -fsS "$URL" | grep -q "calmbark"; then
    echo "  · index.html 서빙 확인: OK (${URL})"
  else
    echo "  · index.html 서빙 확인: 실패"
    kill "$SERVER_PID" 2>/dev/null
    exit 1
  fi
  kill "$SERVER_PID" 2>/dev/null
  echo "  · 서버 종료. 실제 사용은 이 파일을 더블클릭(터미널 대화형)으로 실행하세요."
  exit 0
fi

echo "서버 실행 중: ${URL}   (127.0.0.1 전용 — 외부 접근 불가)"
echo ""
echo "  ① 브라우저가 열리면 '세션 시작'을 눌러 마이크를 허용하세요."
echo "  ② 3초 보정 → 감도 확인 → 모드 선택 (자세한 안내는 화면과 사용법.md)"
echo "  ③ 끝나면 CSV 를 내려받고, 이 창에서 Ctrl+C 로 서버를 종료하세요."
echo ""

if command -v open >/dev/null 2>&1; then
  open "$URL"
else
  echo "브라우저에서 직접 여세요: ${URL}"
fi

echo "종료하려면 Ctrl+C 를 누르세요."
wait "$SERVER_PID"
