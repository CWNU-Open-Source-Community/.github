# CWNU Community Profile

社区组织首页位于 [`profile/README.md`](profile/README.md)。

## 社区统计

[`CWNU Community Metrics`](.github/workflows/community-metrics.yml) 使用
[Metrics](https://github.com/lowlighter/metrics) 的 classic 模板生成
`profile/community-metrics.svg`，展示社区仓库、语言分布、Stars 增长及 Issue / PR 状态。

### 首次配置

1. 创建仅启用 `read:org` 权限的 Personal Access Token (classic)，用于读取组织统计。
2. 在仓库的 **Settings → Secrets and variables → Actions** 中添加 Repository Secret，名称为 `METRICS_TOKEN`。
3. 在 **Actions → CWNU Community Metrics → Run workflow** 中选择 `main` 并运行，成功后生成的 SVG 会自动提交到仓库。

工作流每天北京时间 11:00 定时更新，也支持手动运行。定时任务可能延迟启动。
令牌过期时更新同名 Secret，然后重新运行工作流。

统计令牌不需要 `repo` 写权限；生成图片使用工作流自带的 `GITHUB_TOKEN` 提交，权限在该任务内设为 `contents: write`。
