---
name: traditional-color-palette
description: Design, review and iteratively compare traditional-palette image variants in user-authorized rounds of five or ten.
metadata:
  short-description: Analyse, design and compare five or ten colour options
---

# 中国传统色配色设计 · 方法 v0.11.0-rc.2

让主改对象更好看，每轮有选择价值。当前模型独立完成；诊断、开发、仅计划任务不出图。

## 看图 → 补缺项 → 5/10张 → 自动执行 → 继续或结束

收到图片就主动分析主对象、气质和影响目标的主要色彩关系；将可改部分分清重点修改、协同调整与保留，并说明具体可改属性。把重点和边界放入已有确认，不增加审批。针对缺项提问，不让用户重新描述模型已经看见的内容。区分观察、设计假设和用户要求；用户已经说清的目标、保持项与数量直接记录，不重复审批。

目标确认与本轮数量授权分开：目标回答齐后，询问本轮5张还是10张。在明确的设计任务中，用户回答数量就完成本轮授权；同条消息可说齐目标、数量及继续意愿。随后自动计划、调用图像工具、看图、交付，不再等“开始生图”。CLI 只记录模型解释后的证据并返回下一步，不解析自然语言、不执行图像工具，不能把状态写入说成已经生成。

每轮交付包括 partial 都主动问“继续还是结束”。继续时收集喜欢、不喜欢或修改重点（可说无新意见），以及新一轮5/10张；缺什么只问什么。仅说“喜欢B”、沉默或HTML本地选中都不是继续授权。只说继续而缺数量、反馈时补问。结束立即清除启动许可；已有 pending 只核对回执，不重发。

## 目标 → 关系 → 多路径 → 看图择优

1. 读对应插件和[设计方法](references/design-method.md)，按“基调—主次—色面—联系”提出可验证的关系假设。诊断不清、候选趋同、首次使用某方法或用户追问依据时，读[方法选择](references/method-selection.md)。
2. 本轮5或10款覆盖保守、明显改造、大胆且合情境；以主导关系、色面和强调位置区分实际方案，关键行动写入意图与区域配色，先排重复再选色。10款增加有效选择。关系搜索可选。
3. 按[runtime](references/runtime.md)保存目标和本轮授权。需求与数量齐全就推进 next_action；出图时读[图像交付](references/image-output.md)及 imagegen Skill，按[评审](references/evaluation.md)真实看图。
4. 比较页先放原图、本轮大图、一句说明、可见目标色卡（色块、官方色名及用途）和推荐标记，详细记录折叠。色卡说明设计目标，不冒充成图实测；失败与缺图标明，未全接受为 partial；批次完成不等于会话结束。
5. 下一轮把反馈实质转成当前设计意图、色面决定和短提示。引用“上一轮B”时解析到轮号、方案和图像来源；历史只作偏好参照，原图始终是形态基准，旧意见保留，新意见有冲突时优先。

原图、主对象、目标或权限实质变化时补确认；同范围修订不重复询问。已有有效答复可来自同一条消息，批量确认可覆盖逐图目标。

## 粗分类：只读一个插件

网页 [web](plugins/web.md)；建筑、街景和实体招牌 [built_environment](plugins/built_environment.md)；印刷、包装和插画 [print_packaging](plugins/print_packaging.md)；服饰与道具 [apparel](plugins/apparel.md)；玩具和产品 [toy_product](plugins/toy_product.md)。无匹配或不可用则回退 [generic](plugins/generic.md)，记录原因。商品图带字不自动归网页。

[索引](plugins/index.json)接口1.0、插件0.9.0原样沿用。儿童、群体等按需读[场景提醒](references/scene-context.md)，使用者偏好见[范围](references/project-preferences.md)。

## 固定边界

色表取指定路径或 `palettes/current.palette.json`，冻结原图与色表；主动改色用启用ID，数值程序派生。保留原色、材料、照片和光影不伪造色号。

一张原图一个case，可有多轮。每轮5或10次初稿，共用最多两次修复；失败计入调用。技术重试不新增轮次或额度，换目录、改计划不重置。只有明确新轮授权才增加额度。旧轮的原图、快照、计划、调用、评审、结果和偏好全部保留，不迁移覆盖；v0.9及更早只读。

pending恢复不自动重发，测试确认只运行夹具，禁止真实图像服务。计划或图像变化使相关旧评审失效。确认、判断、计算分清来源，对比度与哈希不证明美观。职责见[models](references/models.md)。
