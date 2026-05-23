# Jinitaimei Server

济你太美的远程通知后端。当前阶段实现邮件通知基础设施、教务通知低频轮询、卓越星公开活动低频轮询；APNs 远程推送等注册 Apple Developer Program 后再接入。

## 当前能力

- `GET /health`：健康检查。
- `POST /api/v1/subscriptions`：保存用户接收邮箱和通知偏好。
- `PUT /api/v1/subscriptions/credentials`：保存用户显式提交的同济统一身份凭据，服务端加密后用于离线邮件提醒。
- `DELETE /api/v1/subscriptions`：删除订阅、凭据和通知状态。
- `POST /api/v1/test-email`：发送测试邮件。
- SQLite 本地存储，默认路径 `./data/jinitaimei-push.sqlite3`。
- SMTP 配置全部来自 `.env`，不写入仓库。
- 轮询任务入口：`python -m app.jobs.poll_notifications`，内部按随机低频和夜间降频执行。
- 教务通知：用用户显式提交的统一身份账号密码登录一系统，只拉摘要列表，发现绝对最新通知后邮件提醒。
- 卓越星：优先使用公开活动列表，不依赖 STAR 登录；按用户选择的星星类别检测新活动和“报名进行中”状态。

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
CREDENTIAL_ENCRYPTION_KEY=使用 Fernet 生成的密钥
POLL_DAY_START=07:00
POLL_NIGHT_START=23:30
```

生成 `CREDENTIAL_ENCRYPTION_KEY`：

```bash
python - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
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

## 轮询业务行为

### 教务通知

后端使用保存的统一身份账号密码尝试普通用户名密码登录。一旦学校要求验证码、短信码、MFA 或页面结构变化，后端不绕过验证，会记录失败并给用户发送“需要重新确认凭据”的邮件。

登录成功后调用：

```text
POST https://1.tongji.edu.cn/api/commonservice/commonMsgPublish/findMyCommonMsgPublish
```

每次只拉前几页摘要字段，按 `publishTime` 找绝对最新通知，不批量抓详情正文、图片或附件。首次轮询只写入基线，不把历史通知全部发送给用户；后续发现新通知才发送邮件，并通过 `notification_events` 去重。

### 卓越星

后端调用公开活动列表：

```text
GET https://star.tongji.edu.cn/api/app-api/activity/index/list?pageNo=1&pageSize=10&recommend=1
```

按用户选择的 `hongwen`、`mingde`、`shizhi`、`qiusuo`、`lixing` 过滤，并同步 App 内点铃铛关注的活动 ID。首次轮询只建立活动与报名状态基线；后续“新活动提醒”只针对首次看到且仍处于“未开始报名”的活动。报名提醒只对已关注活动生效，并且只有在“系统之前已经见过这条活动、之前不是报名进行中、这次轮询变成报名进行中”时才会发送，同一活动只发一次。活动详情链接使用：

```text
https://star.tongji.edu.cn/app/pages-home/detail/huodong?id=<activityId>
```

### 后续计划

- STAR 个人星值私有接口的离线邮件提醒。
- APNs 远程推送。

## iOS 通信与轮询策略

详见 [docs/ios-server-communication.md](docs/ios-server-communication.md)。核心约束：

- 用户必须显式同意后，才会把统一身份账号密码提交到服务端。
- 服务端只保存加密后的凭据。
- 宝塔 cron 每 10 分钟触发轮询入口即可；任务内部会用随机间隔和夜间降频决定是否真正访问学校系统。
