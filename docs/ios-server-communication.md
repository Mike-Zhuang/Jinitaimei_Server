# iOS 与邮件推送后端通信说明

## 目标

邮件推送服务运行在 `https://tjpush.mikezhuang.cn`，负责保存用户邮件通知偏好，并在用户显式同意后加密保存同济统一身份凭据，用于服务端低频轮询教务通知与校园卡余额。卓越星新活动与报名提醒优先使用公开活动接口，不需要用户 STAR Token。

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
  "campus_card_low_balance_enabled": true,
  "campus_card_low_balance_threshold": 50,
  "selected_star_module_codes": ["hongwen", "mingde", "shizhi", "qiusuo", "lixing"],
  "followed_star_activity_ids": [3748, 3763]
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
| 校园卡余额 | 90-180 分钟随机 | 240-480 分钟随机 |

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
2. 按用户选择的星星类别过滤，并同步 App 内点铃铛关注的活动 ID。
3. 首次运行把当前活动 ID 写入 `last_seen_star_activity_ids`，把当前已报名中的活动 ID 写入 `last_seen_star_registration_open_ids`。
4. “新活动提醒”只针对首次看到且当前状态仍为“未开始报名”的活动。
5. “开始报名提醒”只针对用户关注过的活动，并且要求该活动此前已被系统见过、之前不是报名中、这次轮询变成报名进行中。
6. 同一活动的同一次“开始报名”转换只会通知一次。

当前不把用户 STAR Bearer Token 上传给后端；个人星值仍由 App 本地同步。

### 校园卡余额

1. 用户在 App 设置里开启“校园卡低余额提醒”，并填写阈值，默认 `50` 元，可改为 `9`、`20` 等。
2. App 把 `campus_card_low_balance_enabled` 与 `campus_card_low_balance_threshold` 同步给后端。
3. 轮询任务到达允许时间后，后端使用同一套统一身份账号密码访问 `pay-yikatong.tongji.edu.cn` 的统一身份登录入口。
4. 后端提取校园卡访问所需的 Cookie 与 `synjones-auth` 令牌；如果回跳停在 `loginTransit`，则使用 URL 中的 `ticket` 调用校园卡 H5 同款 `/berserker-auth/oauth/token` 接口换取 `access_token`。
5. 后端请求校园卡余额接口，只取当前余额快照，不抓历史消费流水。
6. 首次运行只建立基线，不补发历史低余额提醒。
7. 后续只有在“上一轮高于阈值、这一轮低于或等于阈值”时才发送一封低余额提醒邮件；余额若一直处于低位，不会反复发。
8. 余额恢复到阈值以上后，如果未来再次跌破，才会再次提醒。

## 当前实现状态

- 已完成：订阅接口、凭据加密保存、删除订阅、轮询状态表、随机低频调度、iOS 设置页同步接口。
- 已完成：教务通知摘要低频轮询、首次基线、后续新通知邮件、登录失败邮件提示。
- 已完成：卓越星公开活动低频轮询、类别过滤、首次基线、新活动邮件、报名开始状态迁移邮件。
- 已完成：校园卡低余额阈值同步、服务端校园卡低余额基线与状态迁移邮件逻辑。
- 已完成：校园卡 `loginTransit ticket → access_token` 交换、脱敏诊断任务、低余额跨阈值去重邮件。
- 未接入：STAR 个人星值私有接口离线邮件提醒、APNs 远程推送。
