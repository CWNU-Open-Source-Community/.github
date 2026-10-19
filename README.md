# CWNU Community Profile

社区组织首页位于 [`profile/README.md`](profile/README.md)。

## 社区统计

[`CWNU Community Metrics`](.github/workflows/community-metrics.yml) 运行
[`scripts/community_metrics.py`](scripts/community_metrics.py)，生成
`profile/community-activity.svg`，展示社区活动、仓库统计、公开成员外部贡献、3D 提交日历、关注者、社区成员，以及 Qiyuanqiii 的个人 WakaTime 数据。

### 数据口径

- Activity 和 3D 日历统计当前公开、非 fork 仓库的默认分支；包含仓库迁入组织前的历史。日历展示最近 182 天，按 UTC+8 分日。
- PR 审阅数指至少收到一次 review 的 PR 数；Issue comments 包含 Issue 与 PR 会话评论。
- Notable contributions 汇总公开成员过去一年对其他组织的公开 Commit / PR 贡献。GitHub 对每位成员、每种贡献类型最多返回 100 个仓库。
- Followers 头像行使用组织的实际关注者列表；Members 头像行使用组织的公开成员列表。两行各显示最多 12 个头像。
- Community stats 中的 Following、Starred、Watching 和组织成员关系使用公开成员的公开列表去重汇总。
- WakaTime 明确标注为 Qiyuanqiii 的个人数据，包含年度编码时长、日均时长、语言、编辑器和操作系统，不归入社区总时长。

### 首次配置

1. 创建仅启用 `read:org` 权限的 Personal Access Token (classic)，用于读取组织统计。
2. 在仓库的 **Settings → Secrets and variables → Actions** 中添加 Repository Secret，名称为 `METRICS_TOKEN`。
3. 在 **Actions → CWNU Community Metrics → Run workflow** 中选择 `main` 并运行，成功后生成的 SVG 会自动提交到仓库。

接入 WakaTime：在 [WakaTime OAuth](https://wakatime.com/oauth) 获取有 `read_stats` 权限的个人令牌，添加为仓库 Secret `WAKATIME_TOKEN`。在编辑器安装 WakaTime 插件后开始积累数据；刚注册时可能没有编码记录。尚未接入或服务正在计算统计时，卡片会显示相应状态。

新账号需要在 WakaTime 设置的“偏好”中保存时区，例如 `Asia/Hong_Kong` 或 `Asia/Shanghai`；缺少时区时，统计接口会返回 `User is missing a timezone`。

工作流每天北京时间 11:00 定时更新，也支持手动运行。定时任务可能延迟启动。
每次更新后，主页图片链接会指向生成图片的固定提交版本，避免缓存继续展示旧卡片。
令牌过期时更新同名 Secret，然后重新运行工作流。

统计令牌不需要 `repo` 写权限；生成图片使用工作流自带的 `GITHUB_TOKEN` 提交，权限在该任务内设为 `contents: write`。
