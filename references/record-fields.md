# 按需接口字段

仅在添加下列记录时读对应节；普通配色、视觉阅读核查与 HTML 导出不需加载本文件。基础字段由实际 plan-template/review-template 给出，不再复述。网页有必需程序检查，需读第一节；关系搜索和采样各自按需。

## 程序字底检查与固定原色

diagnosis.objective_checks 每项共用 id/kind/reason/required/applicable：

- contrast 另填 foreground/background/medium/contrast_class。字底引用每款相同意义的 area ID，或 fixed_colors 的引用名，不能笼统引用多色目标。digital 类别为 normal_text/large_text/non_text；physical 用 contextual。可选 minimum 不降低数字门槛，具体阈值见[评审](evaluation.md)。
- external 另填 description，明确尚缺的输入或外部测量；当前程序保持 unknown，不接收作者自报“通过”。

网页至少有一项 applicable=true、required=true 的真实检查；输入不足用 external 明记未知，不能借它跳过本可执行的检查。首次调用时，objective_checks（含阈值）与 fixed_colors 一同冻结。

可选 diagnosis.fixed_colors 是 `{引用名:{rgb:{R,G,B},provenance}}`，RGB 为 0–255 整数，provenance 说明部位、采样和保留依据。固定色引用不与 target/area ID 重名；保留色不强制吸附色表，也不赋予新增表外色权限。

沿用 [W3C 文字对比](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)和[非文字对比](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)的数字功能依据；本次凝练未新增外部验证。

## 成图测量

review.measurements 没有实测就空。采样项含 check_id、method:"image_pixels"、foreground_points、background_points、reason。点位为当前图内的整数 [x,y]，每组 1–100 点，需依据真实部位选取；程序计算点对最低对比值。

只引用适用 contrast 检查。透明采样缺实际合成底时仍未知；截图采样和计划基色分别记录，不能推出实物或整站合规。

## 可选搜索证据

先按[候选搜索](candidate-search.md)写 spec，保存 `groups --spec FILE` 返回的实际结果。本款可填：

```text
search_evidence:
  spec: 完整请求对象，不是文件路径
  selection: 搜索角色到最终色号的映射
  mode: adopted 或 adapted
  reason: 采用或调整的理由
```

adopted 须对应实际候选；调整用 adapted。固定原色角色可省略或为 null，其余选择必须匹配本款最终用色。程序重搜并调用 evaluate_selection；硬关系未满足或无法计算时抛出 GROUP_SELECTION_HARD_FAILED，主控再核验一次。关系通过不代表美观，计划与截图检查仍各自验证；不可自动放宽用户条件。

接受候选的导出含 candidate_evidence（search 与 final）；拒绝或未完成方案也保留此前 spec 和结果，不依赖接受后的导出才留证据。自动搜索不设计五款，也不判断美观。
