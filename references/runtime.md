# v0.10 对话与多轮运行

方法版本为0.11.0-rc.2；运行格式仍为 compact-0.10、schema_version=0.10.0，插件接口1.0、插件0.9.0。用已有字段表达方法，不新增字段或替换接口版本。

在本次Skill根使用 `python3 scripts/scene_cli.py`。下文RUN、PROJECT、IMAGE均替换为绝对路径。文档权限0644，保持模板绑定，不手改账本。CLI不调用图像API；模型理解对话、编写计划、实际调用可用图像工具并看图，CLI记录文件、预算与下一动作。

## 新case：分析与已有答复

先看原图并主动分析，只问缺失目标、范围和保持项。init尚未授权生图：

```bash
python3 scripts/scene_cli.py init --project-root PROJECT --run RUN --input IMAGE --case-id CASE --label LABEL --plugin generic
python3 scripts/scene_cli.py brief-template --run RUN --out RUN/brief.json
python3 scripts/scene_cli.py brief --run RUN --file RUN/brief.json
```

选插件按入口路由；指定色表加 `--palette PATH`。仅规划/诊断用 `--purpose plan` 或 `--purpose diagnose`，不会授权生图。`--count 5|10`只记录已知数量，不单独授予生成许可。测试用 `--run-mode test`，只能使用人工夹具，不能调用真实图像工具。

brief填写summary、goals、supporting_changes、preserve、priorities：重点及改善结果进入goals/priorities，协同调整进入supporting_changes，保持对象及属性进入preserve。保留文字内容或物品身份不自动保留其颜色；具体权限以本任务为准。用户已明确这些需求时直接保存已有答复，无需再问“确认吗”。用brief回执的brief_sha256编写确认文件：

```json
{"brief_sha256":"替换为brief回执的SHA256","source":"conversation","user_reply":"替换为真实用户答复","message_reference":"替换为真实对话定位"}
```

```bash
python3 scripts/scene_cli.py confirm --run RUN --file RUN/confirmation.json
```

目标确认不等于本轮数量授权。缺数量时问5张还是10张；回答数量即足够，不另问“开始生图”。同条消息可以同时形成confirmation与下面的dialogue事件，message_reference可相同。

## 本轮授权与自动推进

```bash
python3 scripts/scene_cli.py dialogue-template --run RUN --out RUN/start.json
python3 scripts/scene_cli.py dialogue --run RUN --file RUN/start.json
```

事件最小结构如下。首轮无反馈可删掉模板的feedback字段；模板的空白内容不能当作答复提交。

```json
{"action":"start","source":"conversation","user_reply":"替换为真实目标/数量答复","message_reference":"替换为真实对话定位","target_count":5}
```

模型把用户含义转成结构化action，不让程序用关键词猜授权。需求与数量齐全时返回 `phase=planning / next_action.action=author_plan`，立即继续：

```bash
python3 scripts/scene_cli.py plan-template --run RUN --out RUN/plan-r001.json
python3 scripts/scene_cli.py plan --run RUN --file RUN/plan-r001.json
```

模板按已授权数量给出A–E或A–J，保留brief_binding、round_binding、target_count。按[设计方法](design-method.md)填写关系假设、各款行动和区域色号；feedback_application区分用户反馈与暂定解释，落实保留项和变化项。design_logic及颜色角色/区域承载实际改法，不能只填purpose。没有反馈按目标设计。

选色用 `search --run RUN --query 关键词`。固定原色、程序检查、采样与搜索证据按需读[字段](record-fields.md)。分类更正可在首次调用前 `route --run RUN --plugin ID`，然后重做计划。

提交plan后，live返回 `reserve_and_call_image_tool`；test返回 `fixture_call`。这只是下一步，不是已生成：

```bash
python3 scripts/scene_cli.py call --run RUN --solution A
```

只在这次call成功的回执允许时，模型立即按source_image和prompt_path调用图像工具一次。回执 `call_image_tool` 仅用于本次派发，不可重放。test回执为 `register_fixture_only` 且 image_tool_allowed=false。

```bash
python3 scripts/scene_cli.py register --run RUN --call CALL_ID --image REAL_OUTPUT_PATH
python3 scripts/scene_cli.py review-template --run RUN --solution A --out RUN/review-r001-A.json
python3 scripts/scene_cli.py review --run RUN --solution A --file RUN/review-r001-A.json
python3 scripts/scene_cli.py status --run RUN
```

先真实打开图再填review。comparison用better/similar/worse；status用pass/fail/unknown，failure_stage用none/goal/diagnosis/execution。按next_action推进到本轮所有候选；A–J仅在10张轮次有效。

调用失败用 `call-failed --run RUN --call CALL_ID --reason REASON`。技术修复仍在当前轮 `call --run RUN --solution A --reason REASON`，全轮共用最多2次，失败也计数。诊断/计划错先改plan；目标错先修正brief并记录有效用户答复。次数不重置。

## 交付与下一轮

```bash
python3 scripts/scene_cli.py export --run RUN
```

export封存本轮，包括partial；不再接受本轮新调用。第一轮写RUN/result.json，之后写RUN/rounds/round-NNN/result.json；始终使用回执的result_path，绝不覆盖旧交付。重复export返回同一文件。export只封存结果，不证明已经展示给用户；模型接着构建页面并交付，主动询问继续还是结束。

对比manifest的cases至少含id/label/input_copy/output_dir/target_count/category。output_dir用case的RUN，result_path用export回执，round_number写轮号，target_count填5或10。一个页面放同case多轮时每项id唯一（如case-r001、case-r002），额外case_id填真实CASE。input_copy用冻结原图路径；输出site必须与RUN、原图目录分开。例：

```json
{"cases":[{"id":"case-r002","case_id":"CASE","label":"第二轮","input_copy":"RUN/inputs/source.png","output_dir":"RUN","result_path":"RUN/rounds/round-002/result.json","round_number":2,"target_count":10,"category":"generic"}]}
```

以上路径需替换成真实绝对路径，源图扩展名使用init回执。`python3 scripts/compare_report.py --manifest MANIFEST --project-root PROJECT --site SITE` 构建HTML。推荐字段沿用cases[].summary：source=model_visual_review、calibrated=false、text，以及recommendation{solution_id,image_sha256,result_sha256,reason}绑定该轮结果。HTML选择只存偏好，导出附轮次、结果SHA并声明generation_authorized=false。

继续时提交新dialogue事件，不重新init。例如模型已从同一条真实答复解析到“继续10张、沿用第1轮B的温暖但压低背景”：

```json
{"action":"continue","source":"conversation","user_reply":"替换为真实答复","message_reference":"替换为新的对话定位","target_count":10,"feedback":{"likes":[{"round_number":1,"solution_id":"B","reason":"延续主体温暖的感觉"}],"dislikes":[],"focus":["降低背景存在感"],"no_new_feedback":false}}
```

反馈引用由CLI绑定旧图路径、调用ID和SHA；缺图引用拒绝。它不会把旧图当成新原图。继续且无新意见可用feedback={likes:[],dislikes:[],focus:[],no_new_feedback:true}。

只说“喜欢B”时，reason可如实写“用户喜欢B，未说明原因”；模型推测放在feedback_application并标为暂定，不补造用户理由，也不因reason必填而强求解释。

仅“喜欢B”提交action=feedback，不能生成。只说继续可先提交action=continue且不填数量/反馈，返回具体缺项问题；补答用action=feedback和缺失字段，沿用已记录的继续许可。无需再审批。每轮都需本轮的新反馈或明确无新意见，旧偏好仍累积保留。

`dialogue --run RUN --file RUN/continue-r002.json` 满足条件后自动开新轮，生成新plan模板再执行。编号A/B等每轮重用，引用必须带轮号。每轮的计划、提示和图像均另存，旧结果保持不变。

结束事件只有action=stop、source、user_reply、message_reference；清除许可与待启动请求。仅看图、记录偏好、重新确认目标都不会解除结束状态。再次生成必须有新的明确轮次授权。

## 恢复与边界

status/verify/resume只读并返回phase与next_action。`recovering / resolve_pending` 要核对外部工具回执，然后登记已产出图或记录失败，不能重发pending调用。即使用户已结束也可登记迟到回执，不恢复自动生成。未知外部结果不要猜失败并重发。

`oldread --run RUN --round 1`只读历史交付；未交付而终止的旧轮返回其state_path。v0.9/v0.8/v0.7仍用status/verify/resume读取，不改账本、补确认或覆盖旧导出；更早结果沿历史展示路径。冻结JSON合同见scene-run-v09.schema.json与scene-run-v08.schema.json，新版见scene-run.schema.json、scene-plan.schema.json、dialogue-event.schema.json、dialogue-state.schema.json。

PROJECT/.palette-cases注册相同case及live原图，换RUN会要求回到原账本；丢失账本不能以新目录恢复额度。注册只约束同工程，CLI无法发现工程外删账本或未登记的外部工具调用，模型仍须按真实历史守预算。测试回执、测试像素与真实生图证据分开。
