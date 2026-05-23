# iOS 与邮件推送后端通信说明

## 目标

邮件推送服务运行在 `https://tjpush.mikezhuang.cn`，负责保存用户邮件通知偏好，并在用户显式同意后加密保存同济统一身份凭据，用于服务端低频轮询教务通知和卓越星提醒。

## 安全边界

- App 不保存 SMTP 密码、不保存服务端加密主密钥。
- 后端不保存明文同济密码；密码使用 `CREDENTIAL_ENCRYPTION_KEY` 加密后写入 SQLite。
- `CREDENTIAL_ENCRYPTION_KEY`、SMTP 密码、管理令牌只允许放在服务器 `/opt/jinitaimei-push/.env`。
- 日志不得输出密码、Cookie、Token、完整邮箱。
- 用户关闭邮件推送时，App 调用删除接口，服务端删除订阅、凭据和通知事件。
- 遇到验证码、MFA 或登录失败时，后端停止该订阅的自动处理，并邮件提示用户重新确认凭据。

## 接口

### 保存订阅

`POST /api/v1/subscriptions`

```json
{
  "email": "user@example.com",
  "mail_push_enabled": true,
  "teaching_notice_enabled": true,
  "star_new_activity_enabled": true,
  "star_registration_enabled": true,
  "selected_star_module_codes": ["hongwen", "mingde", "shizhi", "qiusuo", "lixing"]
}
```

### 保存离线轮询凭据

`PUT /api/v1/subscriptions/credentials`

```json
{
  "email": "user@example.com",
  "tongji_username": "2250000",
  "tongji_password": "password"
}
```

### 删除订阅

`DELETE /api/v1/subscriptions`

```json
{
  "email": "user@example.com"
}
```

## 轮询策略

宝塔 cron 可以每 10 分钟触发 `python -m app.jobs.poll_notifications`，但任务内部必须先检查 `polling_state.next_allowed_at`。未到时间直接退出。

默认间隔：

| 任务 | 白天 | 夜间 23:30-07:00 |
| --- | --- | --- |
| 教务通知 | 45-90 分钟随机 | 180-360 分钟随机 |
| 卓越星公开活动 | 60-120 分钟随机 | 240-480 分钟随机 |
| STAR 私有接口 | 180-360 分钟随机 | 默认不主动刷新 |

失败后指数退避，最长 12 小时。每个订阅之间加入短随机等待，避免集中请求。

## 当前实现状态

- 已完成：订阅接口、凭据加密保存、删除订阅、轮询状态表、随机低频调度、iOS 设置页同步接口。
- 待接入：真实的一系统登录抓取、STAR 私有接口登录抓取、教务通知摘要比较、卓越星活动状态比较。
