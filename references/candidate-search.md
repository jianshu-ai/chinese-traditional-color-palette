# 角色关系驱动的有限候选检索

程序辅助比较一组颜色是否符合已声明的关系。模型先确定对象、用途、权限和设计假设，再把可计算的部分交给检索。结果仍须放回真实对象上看；颜色计算不证明美观，也不证明印刷、涂料、织物或照明下的还原。

## 调用与最小示例

公共接口：`search_groups(features, spec) -> dict` 与 `evaluate_selection(features, spec, selection) -> dict`，位于 [relationship_search.py](../scripts/relationship_search.py)。`features` 必须是 [palette_core.py](../scripts/palette_core.py) 的 `build_features(snapshot)` 结果列表；主流程负责冻结快照、校验绑定，并在返回对象上注入 `palette_binding`。两个函数均无文件读写、缓存或图像调用。

CLI 集成形式为 `scene_cli.py groups --run RUN --spec SPEC.json`。读取 spec 时沿用 `palette_core.parse_json_bytes`，以拒绝重复 JSON 键和非有限常量；Python 字典已无法保留被解析器覆盖的重复键。

下面仅为接口示例。范围、偏好及保留依据是示例设计假设，不代表真实用户批准，也不是全场景推荐值。色号须存在于本次快照且已启用。

```json
{
  "roles": {
    "main": {
      "candidates": ["002001", "002002"],
      "filter": {"lightness": [0.65, 0.95]}
    },
    "support": {"candidates": ["002003", "002008"]},
    "original_background": {
      "fixed": {
        "rgb": {"R": 255, "G": 255, "B": 255},
        "provenance": "测试示例：原图背景采样；假设本例有保留背景的授权"
      }
    }
  },
  "relations": [
    {
      "a": "main", "b": "support",
      "metric": "lightness_delta", "kind": "hard", "min": 0,
      "reason": "示例任务条件：主对象的色卡明度不低于承托层"
    },
    {
      "a": "main", "b": "support",
      "metric": "chroma_delta", "kind": "preference", "min": 0.02,
      "reason": "示例设计假设：尝试让主对象彩度略高，仍需看图验证"
    }
  ],
  "limits": {"per_role": 8, "max_nodes": 10000, "max_results": 5}
}
```

## Spec 契约

顶层只允许 `roles`、`relations`、`limits`、`hue_chroma_min`。仅 `roles` 必填。未知字段、拼错的指标、布尔代数值、NaN、Infinity、颠倒的非色相区间均拒绝，抛出 `palette_core.PaletteError`。关系不合法不会被忽略或自动修复。

`roles` 可以是上例映射，也可以是 `[{"id":"main","candidates":["002001"]}, ...]`。角色 ID 沿用色库稳定 ID 的字符规则；1–8 个，不可重复。映射和列表使用同一字典序搜索顺序。

每个角色选一种来源：

- `candidates`：非空、无重复的启用色号列表。未知或停用色号报错。
- `filter`：至少一个 `lightness`、`chroma`、`hue` 的闭区间 `[lower,upper]`，在启用颜色中筛选。可与 `candidates` 同时提供，取交集。
- `fixed`：恰好包含 `rgb` 与非空 `provenance`。RGB 恰好是整数 R/G/B，范围 0–255。不得再提供候选或筛选。来源应说明原图部位、采样/已知颜色依据和获准保留的依据；程序只能保存声明，不能认证授权或图像采样准确性。

固定原色保持原 RGB，不吸附到传统色色表。输出 `source: "fixed_original"`、`color_id: null`，保留 provenance。新增/主动改色仍必须使用色表来源；固定参考不赋予新增色的权限。

筛选单位：`lightness` 是原始 OKLCh L（0–1），`chroma` 是原始 C（非负），`hue` 是角度（0–360）。不是 HSL，也没有把 L/C 乘 100。色相下界大于上界表示跨零度区间，例如 `[350,15]`；360 与 0 同向，`[0,360]` 是完整色相圈，`[360,360]` 仅零度。所有色相区间均排除色相未定义的颜色。

使用色相筛选或色相距离时，必须显式填写 `hue_chroma_min`。C 小于或等于该值时，关系计算的色相为 `null`。例如 `0.02` 可作一次设计试验的近中性排除范围，但不是科学通用界限，需说明选择依据。值不得低于 `0.000001`，后者仅是抑制当前 RGB→OKLab 转换中灰色数值残差的工程保护值。

未使用色相的请求无需填此参数；此时只应用灰色数值保护值，不声称做了知觉中性色分类。输出 `oklch` 保留 palette_core 原始特征，另有 `hue_for_relations` 明示实际用于关系判断的色相。

`relations` 默认为空列表，最多 64 项。每项仅允许 `a`、`b`、`metric`、`kind`、`reason`、`min`、`max`。前五项必填，至少提供一个上下界；两个角色必须存在且不同。`reason` 写明用户条件、适用技术条件或设计假设的依据。

| metric | value 的精确定义 | 边界含义 |
| --- | --- | --- |
| `lightness_delta` | L(a) − L(b) | 有方向；正值表示 a 的色卡明度较高 |
| `chroma_delta` | C(a) − C(b) | 有方向；正值表示 a 的色卡彩度较高 |
| `hue_distance` | 两个已定义色相的较短圆弧，0–180° | 跨 0/360 正确计算；任一色相未定义则结果未定义 |
| `oklab_distance` | `palette_core.d100`，即 100 × OKLab 欧氏距离 | 非负；不是 CIEDE2000，也不是可见性或喜欢程度阈值 |
| `contrast` | `palette_core.contrast` 的 sRGB 相对亮度比，1–21 | 只计算声明的字底/颜色对；没有默认达标值 |

`min`、`max` 均包含端点。上下界内 `gap=0`，区间外 gap 是到最近边界的距离，单位与本项指标相同。硬条件 `kind: "hard"` 未满足或未定义时排除组合；偏好 `kind: "preference"` 保留结果和取舍。偏好没有隐含权重，没有自动推断的对比阈值，也不因为无解转成更宽松的硬条件。

## 搜索、排序与返回结果

`limits` 的默认值为 `per_role=8`、`max_nodes=10000`、`max_results=5`；可设上限分别是 32、100000、20，下限均为 1。特征和候选 ID 列表最多 100000 项。这些都是成本限制，不是对象色数或美学处方。

先取启用色号与角色筛选范围的交集。超过 `per_role` 时，递归选 OKLab 跨度最大的分区，沿其最宽坐标轴的范围中点二分，每区选最接近均值的实际色表颜色。这样兼顾稀疏区域，不按颜色数量均分。完全重合或浮点端点无法分割时，按色号均分。

分区数不超过 `per_role`；色号用于稳定平局，不再截取色号前缀。范围外颜色不参与补位，固定原色不参与取样。

短名单按色号排列后，先同步循环各角色候选，尝试至多 `per_role` 组，使有限预算有机会触及多个角色的不同用色；再深度优先补查组合。每次角色赋值均计入同一节点预算，包括补查时的重复赋值；已检查的可行种子组只入档、计数一次。硬条件在两端均赋值后立即检查，不物化完整笛卡尔积。

这是有限覆盖策略，不能保证覆盖所有色系、全部可行关系或每种角色变化。前置尝试也会消耗预算。输入列表的排列不表示优先级；无解时不自动扩大范围或放宽硬条件。

搜索维护最多 `max_results` 个组合的有限档案。对每项已声明偏好单独比较 gap：一组在所有可比较偏好上都不差且至少一项更好，才支配另一组。缺失色相与有值测量之间不判支配。没有跨指标加权或综合分。

被当前档案支配的组合不入档，支配档案成员的新组合会替换它们。超限时先尽量保留不同的偏好 gap 向量（包括未知），再保留各角色较均衡的 RGB 种类覆盖，并减少角色用色重复；仍相同时按色号元组决胜。相同 RGB 的不同色号不增加覆盖量。不以最大色差决定美观，也不强制一组内的角色用不同颜色。

结果按角色顺序的色号元组展示，不是质量排名。有限档案可能丢失取舍，甚至忘记早先的支配者；不保证相对所有已访问组合均非支配，不宣称完整或全局 Pareto 前沿。角色组合差异仍须放回实际对象评审。

返回对象可用 `json.dumps(result, allow_nan=False)` 序列化。顶层为：

- `status`：有组合为 `ok`；完整搜索声明领域后无解或某角色领域为空为 `infeasible`；被候选/节点上限截断且未找到为 `not_found_within_limits`。
- `candidates`：最多 `max_results` 组，每组有 `roles`、`relations`、`checks`、`tradeoffs`。角色含来源及计算事实；关系含原声明、零起始 `index`、`value`、`unit`、`outcome`（`met/unmet/undefined`）、`gap`。未定义值和 gap 均为 JSON null。
- `checks`：`hard_relations_met`、`unmet_hard_relations`、`undefined_hard_relations`；后两项为关系索引列表。搜索候选的硬条件均已满足，不代表美观或任何未声明检查通过。
- `tradeoffs`：`unmet_preferences`、`undefined_preferences` 给出对应的零起始关系索引，结合每项 reason 解释，不给总分。
- `search`：有效 limits、role_order、role_pools、nodes_visited、feasible_groups_seen、candidate_pools_truncated、tree_exhausted、archive_truncated 等。role_pools 说明每个角色匹配数、保留数、保留色号和来源。

`search.domain_combinations` 是完整声明领域组合数的十进制字符串，避免大整数在下游 JavaScript 中失真。`shortlist_combinations` 是截断后池子的组合数；没有实际枚举全量的含义。

`hard_rejections` 是每项硬关系实际执行的失败/未定义检查次数，不是全体失败组合数，也不是最小冲突集合。`empty_roles` 给出空领域角色。`hard_constraints_relaxed` 恒为 false；`infeasibility_proven` 只有确已证明声明领域无解时才为 true。

`tree_exhausted` 仅表示遍历完已保留候选池（包括硬剪枝），须结合 `candidate_pools_truncated` 判断整体覆盖。`archive_truncated` 单独表示结果容量损失；有解和遍历完成也不等于所有取舍都已交付。结果不包含耗时或随机种子，相同冻结特征与 spec 得到相同结果，且不会改动输入。

## 最终用色复验与主控衔接

`evaluate_selection(features, spec, selection)` 接收 `role -> color_id` 映射。所有色表角色必填；固定原色角色可省略或填 `null`，不能替换成色号。改选可以使用未入本次短名单的颜色，但必须启用且仍在原声明的候选与筛选交集中。

成功返回与搜索候选一致的 `roles/relations/checks/tradeoffs`，复用同一套数值、色相未知与区间判断，不重新搜索。硬条件失败或未定义均抛出 `PaletteError`，错误码为 `GROUP_SELECTION_HARD_FAILED`，消息列出失败与未知的关系索引。未满足或未知的偏好保留明细，不阻断计划。

主控将本款实际用色映射传入复验，保存结果及采用/改选记录：采用时保存可核查的候选引用；改选时保存最终映射与一句原因。实际色号是否属于本款 `colors`、引用是否真实、快照绑定和授权由主控检查；本函数不推断采用来源，也不记录文件。

## 方法依据与适用边界

数值方法直接复用 [palette_core.py 的 feature、d100、contrast](../scripts/palette_core.py)。L/C/色相是描述变量，关系范围由本次任务提出。特别是对比值，只有在已知真实字底、使用媒介和适用条件时才能接上相应要求；不能把网页可读性标准当作实体产品美观标准。

实现中的逐项偏好比较和有限档案是可复查的工程选择，不是经过本项目用户校准的审美模型。面积、形状、相邻区域、材质、环境照明与观看尺度均未建模。RGB/OKLab 距离不保证实物色差、印刷结果、色觉差异条件下的可区分性或用户喜欢程度。

无可行候选时，先检查输入领域、保留原色和具体冲突。只在有依据时修改设计假设并保留说明；用户硬条件不得自动放宽。候选结果应回到 [设计方法](design-method.md) 与 [实际评审](evaluation.md)，不替代目标确认或成图比较。
