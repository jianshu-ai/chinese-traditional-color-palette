# 中国传统色配色 Skill · v0.11.0-rc.2 公开测试版

本包包含 Skill 说明、分类插件、运行脚本、数据合同、HTML 模板及 384 色运行色表。方法版本为 0.11.0-rc.2，运行数据接口仍为 0.10.0。

## 安装与迁移

让 Agent 加载根目录的 `SKILL.md`。平台如果支持 Skill ZIP 导入，可尝试导入发布包；其他平台需要按其技能加载方式配置。仓库名为 `chinese-traditional-color-palette`，技能内部名称仍为 `traditional-color-palette`。

运行脚本需要 Python 3.10+ 和 Pillow。安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

Agent 还须能读图、执行本地脚本并调用真实的图片编辑工具。Python 脚本管理计划和记录，不提供图片生成服务。其他 Agent 应把原说明中的图像工具步骤接到其实际可用的工具；缺少能力时应如实说明。

## 色表与路径

色表位于 `palettes/current.palette.json`。公开版保留色号、色名、RGB、CMYK、书名和书中页码。384 色的数值与原运行包相同。

如果运行工程不是 Skill 解压目录，初始化时应明确指定色表路径：

```bash
python3 /SKILL_DIR/scripts/scene_cli.py init --project-root /PROJECT --run /PROJECT/runs/case-001 --input /IMAGE.png --case-id case-001 --label "配色任务" --plugin generic --palette /SKILL_DIR/palettes/current.palette.json
```

将大写占位路径替换为自己的实际路径。其他操作见 `references/runtime.md`。

## 公开版整理

本次整理不改变运行脚本、数据接口或配色数值，只清理私人案例偏好和来源中的私人文件信息，补充中文介绍与使用说明。未包含开发日志、聊天记录、图片、历史结果、快照、账号信息或凭证。

Codex 与 Coze 已有实际使用案例，其他平台仍需验证。文字重绘、评审误触发重新确认及并行调度等已知问题仍需改进；实际生成结果需要真实看图判断。效果图适合比较设计方向，不直接作为生产稿。
