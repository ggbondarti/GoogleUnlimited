#!/usr/bin/env bash
# User-run ONLY on the physical Mac terminal; never execute through a remote agent.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/engine/bin/gotohp-cli-owned-arm64"
STATE="$ROOT/webui/state"
CONFIG="$STATE/gotohp.config"

if [[ ! -t 0 ]]; then
  echo "请直接在 Mac M2 Terminal 中运行，禁止通过远程命令传递敏感 Token。" >&2
  exit 2
fi
if [[ ! -x "$CLI" ]] || ! python3 "$ROOT/engine/verify_local_build.py"; then
  echo "自行编译上传引擎尚未验证成功；请先运行 bash scripts/build_local_uploader.sh" >&2
  exit 1
fi
mkdir -p "$STATE"
chmod 700 "$STATE"
echo "本机独立账号授权：请先在你的 Chrome 8pndk8ba 配置文件中访问"
echo "https://accounts.google.com/EmbeddedSetup"
echo "完成 Google 登录并同意。打开 Chrome 开发者工具 > Application > Cookies >"
echo "https://accounts.google.com，只复制 oauth_token 的 Value。"
echo "不要复制其它 Cookie，不要把值发送到 ChatGPT、NexusDock、GitHub 或任何网站。"
echo "粘贴后终端不会显示，也不会将 Token 放在命令行参数里。"
echo ""
IFS= read -r -s -p "粘贴 oauth_token，然后按 Return: " AUTH_TOKEN
printf '\n'
if [[ -z "$AUTH_TOKEN" ]]; then
  echo "没有输入 token；未修改配置。" >&2
  exit 2
fi
if printf '%s\n' "$AUTH_TOKEN" | "$CLI" --config "$CONFIG" creds add - >/dev/null 2>&1; then
  unset AUTH_TOKEN
  chmod 600 "$CONFIG"
  echo "授权请求已完成，凭据仅保存在本机 webui/state/gotohp.config (0600)。"
  echo "请使用以下命令核对账号邮箱（不要显示文件内容）："
  printf '%q --config %q creds list\n' "$CLI" "$CONFIG"
else
  unset AUTH_TOKEN
  echo "授权失败或 Token 已过期；请重新完成 Embedded Setup 后再试。" >&2
  exit 1
fi
