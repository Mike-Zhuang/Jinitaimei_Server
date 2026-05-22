# Jinitaimei Server

济你太美的远程通知后端。当前阶段先实现邮件通知基础设施，后续再接入教务通知、卓越星活动轮询和 APNs。

## 当前能力

- `GET /health`：健康检查。
- `POST /api/v1/subscriptions`：保存用户接收邮箱和通知偏好。
- `POST /api/v1/test-email`：发送测试邮件。
- SQLite 本地存储，默认路径 `./data/jinitaimei-push.sqlite3`。
- SMTP 配置全部来自 `.env`，不写入仓库。

## 本地运行

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 31080 --reload
```

验证：

```bash
curl https://tjpush.mikezhuang.cn/health
```

本地未配置 Nginx 时：

```bash
curl http://127.0.0.1:31080/health
```

发送测试邮件需要带管理令牌，避免公开接口被滥用：

```bash
curl -X POST http://127.0.0.1:31080/api/v1/test-email \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{"email":"你的接收邮箱"}'
```

## 服务器部署

目标部署形态：

- 公网域名：`https://tjpush.mikezhuang.cn`
- 后端监听：`127.0.0.1:31080`
- 公网 HTTPS 由宝塔 / Nginx 反向代理提供。

建议目录：

```bash
/opt/jinitaimei-push
```

`.env` 只放服务器，不提交 Git：

```env
APP_ENV=production
APP_BASE_URL=https://tjpush.mikezhuang.cn
HOST=127.0.0.1
PORT=31080
DATABASE_PATH=/opt/jinitaimei-push/data/jinitaimei-push.sqlite3
SMTP_HOST=smtp.exmail.qq.com
SMTP_PORT=465
SMTP_USERNAME=tjpush_admin@mikezhuang.cn
SMTP_PASSWORD=服务器上填写
SMTP_FROM=济你太美通知 <tjpush_admin@mikezhuang.cn>
ADMIN_TOKEN=生成一个长随机字符串
```

宝塔 / Nginx 反代目标：

```text
http://127.0.0.1:31080
```

不要在阿里云安全组放行 `31080`，只让 Nginx 访问本机端口。

一个可用的 `systemd` 服务模板：

```ini
[Unit]
Description=Jinitaimei Push Server
After=network.target

[Service]
WorkingDirectory=/opt/jinitaimei-push
EnvironmentFile=/opt/jinitaimei-push/.env
ExecStart=/opt/jinitaimei-push/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 31080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## 后续计划

- 接入 App 端订阅接口。
- 增加服务端加密保存必要凭证。
- 定时检测教务通知最新标题。
- 定时检测卓越星新活动和关注活动报名状态。
- 邮件通知稳定后，再接 APNs 远程推送。
