# 三项上游改动交接包

此分支是账号 `1932819331hy-debug` 的代码交接入口，供其他执行者读取、验证和继续发布。材料已完成本地验证；截至 2026-10-23 UTC，本批上游 PR 尚未创建，奖励未确认。

先阅读 [manifest.json](manifest.json) 和 [发布命令](PUBLISH.md)。可以直接浏览下列目录，也可以下载 [完整交付包](three-new-changes-public.zip)；压缩包的校验值见 [three-new-changes-public.zip.sha256](three-new-changes-public.zip.sha256)。

| 改动 | 上游 | 测试提交 | 当前状态 |
| --- | --- | --- | --- |
| [CSV 排序保留逻辑记录](precommit-csv-records) | Chain-Love/chain-love | `61183af412d1edd0e4f657c68a02e316272d9fad` | 用户 fork 分支已推送；12 回归测试通过，959 份现有 CSV 不变，1,458 个边界组合通过 |
| [Somnia Multicall3 条目](somnia-multicall3) | Chain-Love/chain-love | `67820f9f2a89f3edd28bd8c40319135d19fdb957` | 用户 fork 分支已推送；三项上游校验通过，138 份生成 JSON 已比较；发布为草稿 |
| [mil2mm 十进制字符串换算](mm-mil2mm-decimal) | tscircuit/mm | `fa997a5cdfabd854c4007e80a14a2869a7d0152c` | bundle 已保存；28 测试、类型、格式、构建通过，108 十进制及 53 兼容性检查通过；尚需 fork 和推送 |

每个目录包含完整 `change.patch`、保留提交身份的 `change.bundle`、已准备的 `proposed-body.md`、最终提交对应的 `final-verification.json` 和相关测试日志。Git bundle 是增量包：两个 Chain-Love 改动需要基础提交 `7900812d45993a3eac5055c231b690dc26920b89`，mm 需要 `10ca85f3b39f993d88c2be35c2cc4ed814a2cd43`。先 clone 上游并取得基础提交，再执行 `git bundle verify` 和 `git fetch`。

继续发布时，先确认账号及已有 PR，避免重复创建；使用上述已测试提交，保留原作者信息。Somnia 的账号持有人来源核验声明仍未确认，必须先创建草稿。上游或提交发生变化后，应重新评估冲突并补做相应验证。发布命令默认使用账号 `1932819331hy-debug`，不要在其他账号下直接执行。

来源会话可以向已有仓库推送代码，但创建上游 PR、新建仓库和 fork 的操作被集成权限拒绝。此分支解决交付材料的读取问题；继续发布仍需要具有相应权限的正常授权会话。创建实际 PR 后记录 URL、head SHA 和 CI 状态；等待维护者批准运行不等于检查通过。已有 #4775、#4776 不属于本批。

日志中机器特定的工作目录已替换为 `<verification-workspace>`；Somnia validator 结果中的路径也已归一化。退出码、版本、提交与检查结果保持原值。`rebase-verification.json` 记录初次复核提交与最终提交之间的文件一致性；发布以 `final-verification.json` 的提交为准。未包含登录凭据、授权诊断或原始内部工作记录。

实现和复核使用了 AI 辅助。补丁修改的是上述开源项目，适用对应上游项目的许可；此交接包不重新许可上游代码。
