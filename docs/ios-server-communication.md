# iOS 与邮件推送后端通信说明

## 目标

邮件推送服务运行在 `https://tjpush.mikezhuang.cn`，负责保存用户邮件通知偏好，并在用户显式同意后加密保存同济统一身份凭据，用于服务端低频轮询教务通知。卓越星新活动与报名提醒优先使用公开活动接口，不需要用户 STAR Token。

## 安全边界

- App 不保存 SMTP 密码、不保存服务端加密主密钥。
- 后端不保存明文同济密码；密码使用 `CREDENTIAL_ENCRYPTION_KEY` 加密后写入 SQLite。
- `CREDENTIAL_ENCRYPTION_KEY`、SMTP 密码、管理令牌只允许放在服务器 `/opt/jinitaimei-push/.env`。
- 日志不得输出密码、Cookie、Token、完整邮箱。
- 用户关闭邮件推送时，App 调用删除接口，服务端删除订阅、凭据和通知事件。
- 遇到验证码、MFA 或登录失败时，后端停止该订阅的自动处理，并邮件提示用户重新确认凭据。
- 首次轮询只建立基线，不发送历史通知或历史活动，避免用户刚开启时收到一串旧邮件。

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

## 业务数据流

### 教务通知

1. App 保存订阅偏好。
2. 用户打开“离线邮件推送”并提交统一身份账号密码。
3. 后端加密保存密码。
4. 轮询任务到达允许时间后，用普通用户名密码链路登录一系统。
5. 登录成功后只请求通知摘要列表，不抓详情正文。
6. 后端按 `publishTime` 找绝对最新通知。
7. 首次运行写入 `last_seen_teaching_notice_id` 和 `last_seen_teaching_notice_time`；后续发现更新后发送邮件。
8. `notification_events` 用 `(subscription_id, event_type, external_id)` 去重。

如果登录时出现验证码、MFA、页面结构变化或凭据错误，后端记录 `credential_vault.last_failure_reason`，并通过邮件提示用户回到 App 重新确认。

### 卓越星公开活动

1. 轮询任务到达允许时间后，请求 STAR 公开活动列表。
2. 按用户选择的星星类别过滤。
3. 首次运行把当前活动 ID 写入 `last_seen_star_activity_ids`。
4. 后续出现新活动时发送“卓越星新活动”邮件。
5. 若活动状态包含“报名进行中”，并且用户开启报名提醒，则发送“卓越星活动报名中”邮件。

当前不把用户 STAR Bearer Token 上传给后端；个人星值仍由 App 本地同步。

## 当前实现状态

- 已完成：订阅接口、凭据加密保存、删除订阅、轮询状态表、随机低频调度、iOS 设置页同步接口。
- 已完成：教务通知摘要低频轮询、首次基线、后续新通知邮件、登录失败邮件提示。
- 已完成：卓越星公开活动低频轮询、类别过滤、首次基线、新活动邮件、报名进行中邮件。
- 未接入：STAR 个人星值私有接口离线邮件提醒、APNs 远程推送。
