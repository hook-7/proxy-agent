#!/usr/bin/env bash
# proxy-agent 后台服务：PID 文件 + 日志，或 systemd 前台模式（子命令 run）。
#
# 环境变量（可选）：
#   PROXY_AGENT_HOST   监听地址，默认 0.0.0.0
#   PROXY_AGENT_PORT   端口，默认 8000
#   PROXY_AGENT_LOG    日志文件，默认 <仓库根>/var/log/proxy-agent.log
#   PROXY_AGENT_PID    PID 文件，默认 <仓库根>/var/run/proxy-agent.pid
#
# 用法：
#   ./scripts/proxy-agent-service.sh start|stop|status|restart|run
#
# systemd 的 ExecStart 请指向：.../scripts/proxy-agent-service.sh run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PROXY_AGENT_HOST="${PROXY_AGENT_HOST:-0.0.0.0}"
PROXY_AGENT_PORT="${PROXY_AGENT_PORT:-8000}"
mkdir -p "${REPO_ROOT}/var/log" "${REPO_ROOT}/var/run"
PROXY_AGENT_LOG="${PROXY_AGENT_LOG:-${REPO_ROOT}/var/log/proxy-agent.log}"
PROXY_AGENT_PID="${PROXY_AGENT_PID:-${REPO_ROOT}/var/run/proxy-agent.pid}"

_uvicorn_cmd() {
  if [[ -n "${PROXY_AGENT_UVICORN_CMD:-}" ]]; then
    # shellcheck disable=SC2086
    exec ${PROXY_AGENT_UVICORN_CMD}
  fi
  if command -v uv >/dev/null 2>&1; then
    exec uv run uvicorn proxy_agent.app:app \
      --host "${PROXY_AGENT_HOST}" --port "${PROXY_AGENT_PORT}"
  fi
  if [[ -x "${REPO_ROOT}/.venv/bin/uvicorn" ]]; then
    exec "${REPO_ROOT}/.venv/bin/uvicorn" proxy_agent.app:app \
      --host "${PROXY_AGENT_HOST}" --port "${PROXY_AGENT_PORT}"
  fi
  exec python3 -m uvicorn proxy_agent.app:app \
    --host "${PROXY_AGENT_HOST}" --port "${PROXY_AGENT_PORT}"
}

_is_running() {
  local pid
  [[ -f "${PROXY_AGENT_PID}" ]] || return 1
  pid="$(cat "${PROXY_AGENT_PID}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" 2>/dev/null
}

cmd_start() {
  if _is_running; then
    echo "proxy-agent 已在运行 (pid $(cat "${PROXY_AGENT_PID}"))"
    exit 1
  fi
  echo "启动 proxy-agent → http://${PROXY_AGENT_HOST}:${PROXY_AGENT_PORT}"
  echo "日志: ${PROXY_AGENT_LOG}"
  # 追加日志；子进程继承当前环境（含 .env 由应用自行加载）
  nohup bash "${SCRIPT_DIR}/proxy-agent-service.sh" _child >>"${PROXY_AGENT_LOG}" 2>&1 &
  echo $! >"${PROXY_AGENT_PID}"
  sleep 0.3
  if _is_running; then
    echo "已启动 pid $(cat "${PROXY_AGENT_PID}")"
  else
    echo "启动失败，请查看 ${PROXY_AGENT_LOG}"
    rm -f "${PROXY_AGENT_PID}"
    exit 1
  fi
}

cmd_stop() {
  if ! _is_running; then
    echo "proxy-agent 未在运行"
    rm -f "${PROXY_AGENT_PID}"
    exit 0
  fi
  local pid
  pid="$(cat "${PROXY_AGENT_PID}")"
  echo "停止 pid ${pid} …"
  kill "${pid}" 2>/dev/null || true
  for _ in {1..30}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      break
    fi
    sleep 0.2
  done
  if kill -0 "${pid}" 2>/dev/null; then
    echo "进程未退出，发送 SIGKILL"
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${PROXY_AGENT_PID}"
  echo "已停止"
}

cmd_status() {
  if _is_running; then
    echo "运行中 pid $(cat "${PROXY_AGENT_PID}") → http://${PROXY_AGENT_HOST}:${PROXY_AGENT_PORT}"
  else
    echo "未运行"
    rm -f "${PROXY_AGENT_PID}"
    exit 1
  fi
}

cmd_restart() {
  cmd_stop || true
  cmd_start
}

cmd_run() {
  # systemd / 进程管理器：前台运行，不写 PID 文件
  _uvicorn_cmd
}

usage() {
  echo "用法: $0 {start|stop|status|restart|run}"
  exit 1
}

# 内部：由 start 经 nohup 拉起，勿直接调用
cmd_child() {
  _uvicorn_cmd
}

main() {
  case "${1:-}" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    status) cmd_status ;;
    restart) cmd_restart ;;
    run) cmd_run ;;
    _child) cmd_child ;;
    *) usage ;;
  esac
}

main "$@"
