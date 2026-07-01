#!/bin/bash
# 把一台"仍跟踪老独立仓库 KevinJH82/geo-downloader"的部署机,迁移到跟踪 monorepo
# KevinJH82/DeepExplor-OS(geo-downloader 为其子目录),并切到 gunicorn+gevent + launchd 托管。
#
# 针对 192.168.112.57(macOS, 部署路径 /opt/deproject/geo-downloader, app 端口 8086,
# 8090 反向代理 → 8086, 无 venv 用系统 python3)。换机改下方变量即可。
#
# 安全设计:
#   - 前 4 步(克隆/迁运行态/装依赖/校验)全程不碰正在跑的老服务;校验不过直接退出。
#   - 第 5 步才停老服务+备份老目录(mv 成 .old.<时间戳>,不删)+软链;可秒回退。
# 回退: rm $OLD && mv $OLD.old.* $OLD, 再用 `python3 web/app.py` 起老服务。
set -euo pipefail

OLD=/opt/deproject/geo-downloader
MONO=/opt/deproject/DeepExplor-OS
NEW="$MONO/geo-downloader"
PORT=8086

echo "[1/6] 克隆/更新 monorepo → $MONO"
if [ -d "$MONO/.git" ]; then git -C "$MONO" pull --ff-only || true
else git clone git@github.com:KevinJH82/DeepExplor-OS.git "$MONO"; fi

echo "[2/6] 迁移运行态(不在 git 里的: 密钥/任务状态/uploads)"
cp "$OLD/config/credentials.yaml"            "$NEW/config/"
cp "$OLD/config/download_stats.json"         "$NEW/config/"            2>/dev/null || true
cp "$OLD/.geo_tasks_persist.json"            "$NEW/"                   2>/dev/null || true
cp "$OLD/.geo_notifications_persist.json"    "$NEW/"                   2>/dev/null || true
[ -d "$OLD/uploads" ] && cp -R "$OLD/uploads" "$NEW/" || true

echo "[3/6] 装 gunicorn+gevent(只装这俩,别整装 requirements —— 会触发 rasterio 源码编译)"
python3 -m pip install gunicorn gevent 2>/dev/null \
  || python3 -m pip install --break-system-packages gunicorn gevent

echo "[4/6] 校验(缺任一则中止,绝不动老服务)"
for f in run_web.sh web/wsgi.py deploy/com.deepexplor.geodownloader.web.plist config/credentials.yaml; do
  [ -e "$NEW/$f" ] || { echo "  缺 $NEW/$f, 中止"; exit 1; }
done
python3 -c "import gunicorn, gevent" || { echo "  gunicorn/gevent 未装成功, 中止"; exit 1; }
echo "  校验通过 ✓"

echo "[5/6] 停老服务(:$PORT) + 备份老目录 + 切软链到 monorepo"
# 杀占用 $PORT 的老进程: lsof 可能不在 PATH(用全路径), 再用进程名兜底(注意实际是 python3.14 web/app.py)
{ /usr/sbin/lsof -ti tcp:$PORT 2>/dev/null || lsof -ti tcp:$PORT 2>/dev/null || true; } | xargs kill 2>/dev/null || true
pkill -f "web/app.py" 2>/dev/null || true
sleep 3
[ -L "$OLD" ] || mv "$OLD" "$OLD.old.$(date +%s)"
ln -sfn "$NEW" "$OLD"

echo "[6/6] 安装并加载 launchd(开机自启 + 崩溃自愈)"
mkdir -p ~/Library/LaunchAgents
cp "$OLD/deploy/com.deepexplor.geodownloader.web.plist" ~/Library/LaunchAgents/
launchctl bootout   gui/$(id -u)/com.deepexplor.geodownloader.web 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deepexplor.geodownloader.web.plist 2>/dev/null \
  || launchctl load -w ~/Library/LaunchAgents/com.deepexplor.geodownloader.web.plist

sleep 6
echo "== 验证 =="
curl -s "localhost:$PORT/api/version"; echo
launchctl list | grep geodownloader || echo "  (launchctl list 无 geodownloader —— 看下方日志)"
tail -15 /tmp/geodl_web.err.log 2>/dev/null || true
echo "== 完成。回退: rm $OLD && mv ${OLD}.old.* $OLD =="
