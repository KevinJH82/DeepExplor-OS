# geo-downloader Web UI — launchd 托管（开机自启 + 崩溃自愈）

针对线上 macOS 机 **192.168.112.57**（Mac Mini，部署路径 `/opt/deproject/geo-downloader`，
app 端口 **8086**，前面有 8090 反向代理 → 8086）。本目录的 `com.deepexplor.geodownloader.web.plist`
用 macOS launchd（LaunchAgent）常驻 gunicorn+gevent Web 服务，解决"进程停了没人拉起来 → 502"。

## 一次性部署步骤（在该 Mac 上，用户 jiahao）

```bash
cd /opt/deproject/geo-downloader
git pull                              # 拉到 run_web.sh / wsgi.py / gunicorn_conf.py / deploy/

# 1) 只装新增的 gunicorn + gevent(其余依赖该机已装;别跑整个 requirements.txt —— 会触发
#    rasterio 从源码重编译、报 gdal-config not found)。装进系统 python3(该机无 venv)。
python3 -m pip install gunicorn gevent
#   若报 "externally-managed-environment"(Homebrew python 的 PEP668 限制):
#     python3 -m pip install --break-system-packages gunicorn gevent
#   若 gevent 在很新的 python(如 3.14)上无 wheel 而源码编译失败: 改用该机的 python3.11
#     (8090 代理即 3.11 跑)装并起: python3.11 -m pip install gunicorn gevent,
#      并把 run_web.sh 里的 python3 换成 python3.11(或建 3.11 venv,run_web.sh 会自动优先用 venv)。
#   —— run_web.sh 会自动优先用 venv/bin/python,没有才用系统 python3。

# 2) 停掉临时手动起的旧进程(之前 nohup python3 web/app.py)
pkill -f "python3 web/app.py" 2>/dev/null || true

# 3) 装 LaunchAgent 并加载
mkdir -p ~/Library/LaunchAgents
cp deploy/com.deepexplor.geodownloader.web.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deepexplor.geodownloader.web.plist \
  || launchctl load -w ~/Library/LaunchAgents/com.deepexplor.geodownloader.web.plist   # 旧系统回退

# 4) 验证
launchctl list | grep geodownloader           # 有一行,第2列是退出码(0/正在跑)
sleep 4
curl -s localhost:8086/api/version            # 期望 {"version":"1.0.2"}
curl -s localhost:8090/api/version            # 代理也应 200
tail -20 /tmp/geodl_web.err.log               # 启动日志(gunicorn "Using worker: gevent")
```

## 常用运维

```bash
# 重启
launchctl kickstart -k gui/$(id -u)/com.deepexplor.geodownloader.web
# 停止/卸载(不再自启)
launchctl bootout gui/$(id -u)/com.deepexplor.geodownloader.web
# 看日志
tail -f /tmp/geodl_web.err.log   /tmp/geodl_web.out.log
```

## 注意
- **开机自启前提**：LaunchAgent 只在 jiahao 用户登录后运行。服务器 Mac 请在
  `系统设置 → 用户与群组 → 自动登录` 开启 jiahao 自动登录，否则重启后需登录才拉起。
- **端口是 8086**（匹配 8090 代理的转发目标），不是 8080/8090。换机部署改 plist 里的
  `PORT` 与两处路径。
- **外接盘**：下载写到 `/Volumes/大硬盘可劲用/...`，故用 LaunchAgent(用户会话内,能见挂载盘)
  而非 LaunchDaemon。
- 本方案同时根治了 SSE 抖动/重连刷屏（gunicorn+gevent 单 worker 多协程承载长连接）。
