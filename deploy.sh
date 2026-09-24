#!/usr/bin/env bash
# TACO小剧场：可独立下载运行的安装与运维入口。
set -Eeuo pipefail
umask 077
REPO_URL='https://github.com/tztmr/video_web_play.git'
BRANCH=main
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${TACO_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/taco-cinema}"
STATE_FILE="$STATE_DIR/install-dir"
INSTALL_DIR=''
ACTION=''
INSTALL_SYSTEM=1
STACK_ARGS=(--entry-from-menu)
DOCKER=(docker)
if [[ -t 1 ]]; then
  ACCENT=$'\033[1;36m'; GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
  ACCENT=''; GREEN=''; RED=''; RESET=''
fi

info() { printf '%s[信息]%s %s\n' "$ACCENT" "$RESET" "$*"; }
success() { printf '%s[完成]%s %s\n' "$GREEN" "$RESET" "$*"; }
fail() { printf '%s[错误]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
TACO小剧场 · 一键安装与运维
  bash deploy.sh                          打开中文菜单
  bash deploy.sh --install                 下载源码并安装（已有目录则继续部署）
  bash deploy.sh --update                  拉取 main 并更新服务
  bash deploy.sh --configure               修改域名、HTTPS 与出口线路
  bash deploy.sh --status | --logs         查看状态或最近日志
  bash deploy.sh --start | --stop | --restart
  bash deploy.sh --backup                  备份账号、分享、设备配置与 .env
  bash deploy.sh --setup-link              查看管理员初始化入口
  bash deploy.sh --version                 比较本地与 GitHub main
  bash deploy.sh --refresh-geoip           更新当月大陆 IP 地址库并部署
  bash deploy.sh --uninstall               移除服务容器，保留代码与数据卷
  bash deploy.sh --check                   只检查部署配置
  bash deploy.sh --directory /opt/taco-cinema --install

部署参数：--domain 域名、--direct、--proxy 代理地址、--no-install（不安装系统依赖）。
仅传部署参数时仍兼容旧版直接部署方式。首次默认目录 /opt/taco-cinema。
仓库：https://github.com/tztmr/video_web_play.git
所有公开入口默认禁止大陆 IP，分享链接同样受限制。
HELP
}

select_action() {
  [[ -z "$ACTION" ]] || fail '一次只能指定一个操作。'
  ACTION="$1"
}
while (($#)); do
  case "$1" in
    --install|--update|--configure|--status|--logs|--start|--stop|--restart|--backup|--setup-link|--version|--uninstall|--check|--refresh-geoip)
      select_action "${1#--}"; shift ;;
    --directory|--domain|--proxy)
      (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || fail "$1 缺少参数。"
      if [[ "$1" == --directory ]]; then INSTALL_DIR="$2"; else STACK_ARGS+=("$1" "$2"); fi
      shift 2 ;;
    --direct) STACK_ARGS+=("$1"); shift ;;
    --no-install) INSTALL_SYSTEM=0; STACK_ARGS+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知参数：$1（可用 --help 查看说明）" ;;
  esac
done
# The sentinel keeps empty arrays safe under macOS Bash 3.2 with nounset.
if [[ -z "$ACTION" && ${#STACK_ARGS[@]} -gt 1 ]]; then ACTION=install; fi

valid_path() {
  case "$1" in
    ''|/|/opt|/root|/home|/usr|/var|/etc|"$HOME"|*'/../'*|*/..|*'/./'*|*/.|*$'\n'*|*$'\r'*) return 1 ;;
    /*) return 0 ;;
    *) return 1 ;;
  esac
}
resolve_directory() {
  if [[ -z "$INSTALL_DIR" ]]; then
    if [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]]; then
      IFS= read -r INSTALL_DIR < "$STATE_FILE" || true
    elif [[ -f "$SCRIPT_DIR/compose.yaml" && -f "$SCRIPT_DIR/server.py" ]]; then
      INSTALL_DIR="$SCRIPT_DIR"
    fi
  fi
  INSTALL_DIR="${INSTALL_DIR%/}"
  if [[ -n "$INSTALL_DIR" ]]; then valid_path "$INSTALL_DIR" || fail '部署目录记录无效，请显式指定 --directory 绝对路径。'; fi
}
save_directory() {
  [[ ! -L "$STATE_DIR" && ! -L "$STATE_FILE" ]] || fail '部署状态路径不能是符号链接。'
  mkdir -p -- "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  local staging
  staging="$(mktemp "$STATE_DIR/.install-dir.XXXXXX")"
  printf '%s\n' "$INSTALL_DIR" > "$staging"
  chmod 600 "$staging"
  mv -- "$staging" "$STATE_FILE"
}
run_root() {
  if ((EUID == 0)); then "$@"; else
    command -v sudo >/dev/null || fail '此操作需要 root 或 sudo 权限。'
    sudo "$@"
  fi
}
linux_only() { [[ "$(uname -s)" == Linux ]] || fail '安装与运维需在 Linux 服务器执行；--help / --check 可在本机使用。'; }
ensure_git() {
  if ! command -v git >/dev/null; then
    ((INSTALL_SYSTEM)) || fail '缺少 Git，请先安装。'
    [[ -r /etc/os-release ]] || fail '请先安装 Git。'
    local ID
    . /etc/os-release
    [[ "$ID" == ubuntu || "$ID" == debian ]] || fail '请先安装 Git，自动安装支持 Ubuntu/Debian。'
    run_root apt-get update
    run_root apt-get install -y git ca-certificates
  fi
}
require_checkout() {
  [[ -n "$INSTALL_DIR" && -f "$INSTALL_DIR/compose.yaml" && -f "$INSTALL_DIR/scripts/deploy_stack.sh" ]] || fail '未找到 TACO 部署目录，请先安装，或使用 --directory 指定已有目录。'
}
verify_repository() {
  local top remote
  top="$(git -C "$INSTALL_DIR" rev-parse --show-toplevel)"
  [[ "$top" == "$(cd "$INSTALL_DIR" && pwd -P)" ]] || fail '指定目录不是独立仓库根目录。'
  remote="$(git -C "$INSTALL_DIR" config --get remote.origin.url)"
  case "$remote" in
    https://github.com/tztmr/video_web_play|https://github.com/tztmr/video_web_play.git|git@github.com:tztmr/video_web_play.git) ;;
    *) fail '目录内的 origin 不是 tztmr/video_web_play，已停止操作。' ;;
  esac
}
stack() { bash "$INSTALL_DIR/scripts/deploy_stack.sh" "${STACK_ARGS[@]:1}" "$@"; }

do_install() {
  linux_only
  ensure_git
  local selected default_dir
  default_dir="${INSTALL_DIR:-/opt/taco-cinema}"
  if [[ -z "$ACTION" ]]; then
    printf '部署目录 [%s]：' "$default_dir" >&2
    IFS= read -r selected || return 0
    INSTALL_DIR="${selected:-$default_dir}"
  else
    INSTALL_DIR="$default_dir"
  fi
  INSTALL_DIR="${INSTALL_DIR%/}"
  valid_path "$INSTALL_DIR" || fail '请使用独立的绝对目录，例如 /opt/taco-cinema。'
  if [[ ! -e "$INSTALL_DIR/.git" ]]; then
    if [[ -d "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR")" ]]; then fail '目标目录不是空目录；请选择其他目录，避免覆盖已有文件。'; fi
    mkdir -p -- "$(dirname "$INSTALL_DIR")"
    info "正在从 $REPO_URL 下载源码…"
    git clone --single-branch --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
  verify_repository
  require_checkout
  save_directory
  stack
  success "安装完成；日常管理可运行 bash $INSTALL_DIR/deploy.sh"
}
do_update() {
  linux_only
  require_checkout
  ensure_git
  verify_repository
  [[ "$(git -C "$INSTALL_DIR" branch --show-current)" == "$BRANCH" ]] || fail '当前不是 main 分支，请先自行处理分支。'
  [[ -z "$(git -C "$INSTALL_DIR" status --porcelain)" ]] || fail '部署目录有未提交修改，已停止更新，请先保存这些改动。'
  info '正在获取 GitHub main 更新…'
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" merge --ff-only FETCH_HEAD
  stack
  success "服务更新完成，源码提交 $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
}
prepare_compose() {
  linux_only
  require_checkout
  [[ -f "$INSTALL_DIR/.env" ]] || fail '还没有部署配置，请先完成安装。'
  command -v docker >/dev/null || fail '请先安装 Docker。'
  docker compose version >/dev/null 2>&1 || fail '缺少 Docker Compose 插件。'
  DOCKER=(docker)
  if ! docker info >/dev/null 2>&1; then
    if ((EUID != 0)) && command -v sudo >/dev/null; then DOCKER=(sudo docker); fi
    "${DOCKER[@]}" info >/dev/null 2>&1 || fail 'Docker 未启动或当前用户没有访问权限。'
  fi
}
compose() {
  # Avoid empty-array expansion under macOS Bash 3.2 nounset.
  if [[ -f "$INSTALL_DIR/deploy/runtime.env" ]]; then
    (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME TACO_HTTP_PUBLISH TACO_HTTPS_PUBLISH TACO_HTTPS_UDP_PUBLISH TACO_PUBLIC_PORT TACO_CADDYFILE TACO_CERT_DIR HONGGUO_PUBLIC_URL
     "${DOCKER[@]}" compose --project-directory "$INSTALL_DIR" --env-file "$INSTALL_DIR/.env" --env-file "$INSTALL_DIR/deploy/runtime.env" -f "$INSTALL_DIR/compose.yaml" "$@")
  else
    (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME TACO_HTTP_PUBLISH TACO_HTTPS_PUBLISH TACO_HTTPS_UDP_PUBLISH TACO_PUBLIC_PORT TACO_CADDYFILE TACO_CERT_DIR HONGGUO_PUBLIC_URL
     "${DOCKER[@]}" compose --project-directory "$INSTALL_DIR" --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" "$@")
  fi
}
do_status() { prepare_compose; compose ps; }
do_logs() { prepare_compose; compose logs --tail=100 web caddy; }
do_start() { prepare_compose; compose up -d --wait --wait-timeout 180; compose ps; }
do_stop() { prepare_compose; compose stop; success '服务已停止，配置与数据保留。'; }
do_restart() { prepare_compose; compose restart; compose up -d --wait --wait-timeout 180; success '服务已重启并通过容器健康检查。'; }
do_configure() { linux_only; require_checkout; stack --reconfigure; }
do_setup_link() { prepare_compose; compose exec -T web python scripts/setup_link.py --if-needed; }
do_version() {
  command -v git >/dev/null || fail '请先安装 Git。'
  local remote_sha
  remote_sha="$(git ls-remote --exit-code "$REPO_URL" "refs/heads/$BRANCH")"
  remote_sha="${remote_sha%%[[:space:]]*}"
  [[ "$remote_sha" =~ ^[0-9a-f]{40,64}$ ]] || fail '无法读取 GitHub main 提交。'
  printf 'GitHub main：%s\n' "$remote_sha"
  if [[ -n "$INSTALL_DIR" && -e "$INSTALL_DIR/.git" ]]; then
    printf '本地源码   ：%s\n' "$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  fi
}
do_backup() {
  prepare_compose
  local directory archive
  directory="$INSTALL_DIR/backups"
  mkdir -p -- "$directory"
  chmod 700 "$directory"
  BACKUP_STAGE="$(mktemp -d "$directory/.backup.XXXXXX")"
  archive="$directory/taco-$(date -u +%Y%m%d-%H%M%S)-${BACKUP_STAGE##*.}.tar.gz"
  BACKUP_PART="$archive.part"
  trap 'rm -rf -- "$BACKUP_STAGE"; rm -f -- "$BACKUP_PART"' EXIT
  info '正在备份账号、分享与配置（不含视频缓存）…'
  compose exec -T web python scripts/backup_data.py > "$BACKUP_STAGE/data.tar"
  cp -- "$INSTALL_DIR/.env" "$BACKUP_STAGE/site.env"
  git -C "$INSTALL_DIR" rev-parse HEAD > "$BACKUP_STAGE/REVISION"
  tar -czf "$archive.part" -C "$BACKUP_STAGE" data.tar site.env REVISION
  chmod 600 "$archive.part"
  mv -- "$archive.part" "$archive"
  rm -rf -- "$BACKUP_STAGE"
  trap - EXIT
  success "备份完成：$archive"
  info '备份含账号和代理配置，请保存在私有位置。'
}
do_uninstall() {
  prepare_compose
  local answer
  printf '将移除本项目容器与网络，保留代码、账号数据卷及证书。输入 UNINSTALL 确认：' >&2
  IFS= read -r answer || return 0
  [[ "$answer" == UNINSTALL ]] || { info '已取消。'; return 0; }
  compose down --remove-orphans
  success '服务已卸载；重新安装可继续使用原数据。'
}
do_check() { require_checkout; stack --check; }

run_action() {
  # An action gets a fresh shell so a failed command cannot fall through to success.
  local result
  set +e
  (set -Eeuo pipefail; "$@")
  result=$?
  set -e
  if ((result)); then printf '%s[未完成]%s 操作失败，请按上方错误处理后重试。\n' "$RED" "$RESET" >&2; fi
  if [[ "$1" == do_install ]]; then INSTALL_DIR=''; resolve_directory; fi
}
menu() {
  local choice
  while true; do
    printf '\n%s╭────────────────────────────────────────────╮\n' "$ACCENT"
    printf '│         TACO小剧场 · 安装与运维             │\n'
    printf '╰────────────────────────────────────────────╯%s\n' "$RESET"
    printf '仓库：%s\n部署目录：%s\n' "$REPO_URL" "${INSTALL_DIR:-尚未安装}"
    printf '  1) 一键安装 / 继续部署\n  2) 一键更新\n  3) 修改域名、HTTPS 与出口线路\n  4) 查看服务状态\n  5) 查看最近日志\n  6) 重启服务\n  7) 停止服务\n  8) 启动服务\n  9) 备份账号与配置\n 10) 查看管理员初始化链接\n 11) 检查源码版本\n 12) 卸载服务（保留数据）\n 13) 更新大陆 IP 地址库\n  0) 退出\n'
    printf '请选择 [0-13]：' >&2
    IFS= read -r choice || return 0
    case "$choice" in
      1) run_action do_install ;; 2) run_action do_update ;; 3) run_action do_configure ;;
      4) run_action do_status ;; 5) run_action do_logs ;; 6) run_action do_restart ;;
      7) run_action do_stop ;; 8) run_action do_start ;; 9) run_action do_backup ;;
      10) run_action do_setup_link ;; 11) run_action do_version ;; 12) run_action do_uninstall ;;
      13) run_action do_refresh_geoip ;; 0) return 0 ;; *) info '请输入菜单中的数字。' ;;
    esac
  done
}
do_refresh_geoip() { linux_only; require_checkout; stack; }
main() {
  resolve_directory
  if [[ -z "$ACTION" ]]; then menu; else "do_${ACTION//-/_}"; fi
}
# Parse the exit with main before an update can replace this running file.
main "$@"; exit $?
