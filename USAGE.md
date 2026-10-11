# 使用说明

## 需要哪些能力

Skill 为 Agent 提供配色方法和记录流程。宿主需具备读图、执行 Python 脚本、编辑或生成图片，以及读写任务文件的能力。包内脚本不包含 API 密钥，也不直接调用图像生成服务。

已在 Codex 编写，并有 Coze 第三方 Agent 的实际使用案例。其他宿主应适配自己的图像工具，不假定导入后自动具备所有能力。

## 安装

下载源码或 Release 中的 ZIP，保留目录结构。如果平台按目录加载 Skill，可以将 Skill 文件夹命名为 `traditional-color-palette`。入口是 `SKILL.md`。

使用 Python 3.10+，在 Skill 目录内运行：

```bash
python3 -m pip install -r requirements.txt
python3 scripts/scene_cli.py --help
```

## 给 Agent 的任务示例

> 使用中国传统色配色 Skill，为这张包装图片设计五个方案。保留盒型、文字内容和产品身份，允许调整底色与文字颜色。整体希望更明亮、亲和。请展示原图和结果，并说明主要变化。

可以同一条消息说明主要对象、目标、保留项和五张或十张的数量。缺少关键内容时，Agent 应先补问。看完结果后，可以继续提出喜欢、不喜欢或修改重点，并选择下一轮数量；也可以结束。

## 初始化路径

如果运行工程与 Skill 目录不同，初始化时指定包内色表的完整路径：

```bash
python3 /SKILL_DIR/scripts/scene_cli.py init --project-root /PROJECT --run /PROJECT/runs/case-001 --input /IMAGE.png --case-id case-001 --label "配色任务" --plugin generic --palette /SKILL_DIR/palettes/current.palette.json
```

大写路径均为占位符，需替换为自己的路径。分类插件、确认文件、计划、图像登记、评审和对比页流程见 [runtime.md](references/runtime.md)。

## 结果怎么看

每轮应包含原图、不同方案、主要变化与目标色卡。目标色卡说明设计选色，不等同于生成图像的像素实测结果。未达标、缺图或生成失败应明确标出，不把部分成功说成全部完成。

效果图用于比较配色方向。文字、标识、结构和工艺仍需检查，包装或印刷项目还需专业人员制作生产文件。

## 分享记录时保护隐私

运行记录可能包含图片路径、用户答复、图像工具回执和任务信息。它们属于使用者自己的任务数据，默认不属于公开发布包。分享前请清理姓名、联系方式、个人图片、路径、密码、令牌和工具凭证。
