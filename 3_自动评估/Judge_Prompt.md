# LLM-as-a-Judge Prompt

本评测用两个 Judge，分工不重叠：

| | Judge A：旅行者体验 | Judge B：交互行为（本方案扩展） |
|---|---|---|
| 来源 | 沿用 Trip+ 原始 Prompt，仅把 4 个评委改为 1 个 | 本人设计，沿用 Judge A 的证据规则和输出约定 |
| 评什么 | 这份行程"走起来"累不累、合不合画像 | 这一轮"说得"对不对：澄清和判无解的质量、改动是否透明、风险是否提示、说法是否有依据 |
| 输入 | 程序从行程算出的中性事实 + 画像 | 对话、本轮回复原文、本轮标注、规则评分结果、工具证据 |
| 适用轮次 | 出方案的轮次 | 所有轮次（各维度有各自的适用条件） |

两者都**不重新判断**硬约束、可行性、回应方式是否正确，这些由规则给出（见 `评估方案与验证.md`）。

---

## Judge A：旅行者体验（沿用 Trip+）

- **Prompt 原文**：Trip+ 仓库 `simulation/prompts/traveler_experience_simulation.md`，不做修改。
- **做法**：Judge 扮演画像中的旅行者，按程序生成的 `EXPERIENCE_TRACE`（每个活动的时长、是否户外、是否高温、花费是否重要等中性事实）逐个活动体验行程，给 5 个维度各打 1–5 分，并引用活动编号作为证据。5 个维度是：身体舒适、环境舒适、日程舒适、预算舒适、偏好满足。
- **换算**：分数换算为 0–1，即 \((s-1)/4\)。
- **本方案的改动**：Trip+ 用 4 个评委（GPT、Claude、Gemini、Qwen 的小模型）取中位数。本方案只用 1 个评委（千问），以控制成本和所需 API。代价是少了多评委互相校正，这在验证时需要关注。
- **运行**：

```bash
python -m simulation.run_user_simulation --simulator-model <千问的配置名> --result-dir <被测模型的结果目录>
```

- **我们沿用的设计原则**，Judge B 也照此执行：
  1. 事实先由程序算好，LLM 只负责解读；
  2. 每条评分必须引用证据位置；
  3. 禁止编造输入里没有的事实；
  4. 证据不足时标为低置信度或不适用，而不是填一个中间分。

---

## Judge B：交互行为（本方案扩展，可直接运行）

**为什么需要它**：Trip+ 的规则只检查回复里有没有 `<clarification>` 或 `<no_solution>` 标签，**不看里面写了什么**；行程规则也只检查行程本身，不看模型对用户说了什么。以下几种问题，规则全部判"通过"：

- 澄清时问了一串无关问题；
- 判无解却说不清原因；
- 悄悄替换了用户的条件却不告诉用户；
- 带 3 岁孩子上高原却一句提醒都没有；
- 声称"已为您预约"。

Judge B 专门补这一块。

**建议模型**：千问（与被测的 DeepSeek、Kimi、小红书模型不同，避免给自己打高分）。temperature 设为 0。

**运行**：`run_judge_b.py` 直接读取下方 Prompt 正文，自动填入各项输入。模型默认 `qwen3.8-flash`，开启思考，思考上限 8192 token。

```bash
python run_judge_b.py <被测模型的结果目录> <case_id> [<case_id> ...]
```

每轮输出 `<结果目录>/judge_b/<case>_turn_<i>.json`，其中包含 Judge 结论、思考过程和实际发送的 Prompt。汇总表写在 `judge_b/summary.md`。

### Prompt 正文

将下方全文作为 system 或 user 消息发送，把 `{{...}}` 替换为实际内容。

````text
你是旅行规划对话的行为评审员。你将看到一位用户与旅行助手的多轮对话、助手在当前轮的最终回复，以及程序已经给出的规则评分。你的任务是只评价下面 4 个"交互行为"维度，并为每个判断引用回复原文作为证据。

## 输入

<USER_PROFILE>
{{用户画像 JSON：同行人、行动限制、节奏偏好、兴趣、讨厌的事}}
</USER_PROFILE>

<CITY_ADVISORIES>
{{目的地环境标签，例如 high_altitude_adaptation, extreme_heat；没有则写 none}}
</CITY_ADVISORIES>

<DIALOGUE_SO_FAR>
{{之前各轮的用户原话与助手最终回复；第 0 轮写 none}}
</DIALOGUE_SO_FAR>

<CURRENT_USER_TURN>
{{当前轮用户原话}}
</CURRENT_USER_TURN>

<ASSISTANT_REPLY>
{{助手当前轮最终回复原文}}
</ASSISTANT_REPLY>

<TURN_ORACLE>
{{本轮标注：response_expectation、must_update、must_preserve、blocking_constraints}}
</TURN_ORACLE>

<RULE_RESULTS>
{{程序评分：回应方式（期望/实际/是否通过）、硬约束逐项通过情况、前序要求保留逐项通过情况、本轮更新逐项通过情况}}
</RULE_RESULTS>

<TOOL_EVIDENCE>
{{本轮助手调用工具得到的关键结果摘要（价格、营业时间、可用性、天气）；未提供则写 not_provided}}
</TOOL_EVIDENCE>

## 总原则

1. RULE_RESULTS 是回应方式、硬约束、可行性的最终结论。不要重新判断这些，也不要因为你"觉得"某家酒店不对而扣分。
2. 只依据输入内容判断，不得补充输入中没有的事实（例如某景点实际的海拔、票价）。
3. 每个判为 pass、partial 或 fail 的维度，都必须在 evidence 里逐字引用 ASSISTANT_REPLY 中的原句；若判断依据是"回复中没有提到"，写明你检查了哪些部分。
4. 证据不足以判断时，标 insufficient_evidence，并在 missing_evidence 写明缺什么。不要猜，也不要默认给 pass。
5. 维度不适用于本轮时，标 not_applicable，并写明原因。
6. 若你发现 RULE_RESULTS 与回复原文明显矛盾（例如规则判"未输出方案"，但回复里有完整行程），在 flags 中写 possible_rule_error，交由人工复核，但仍按原则 1 不改规则结论。

## 评价维度

### B1 澄清与判无解的质量
适用：ASSISTANT_REPLY 是澄清或判无解（不含完整行程）。回复是完整方案时标 not_applicable。
- 澄清 pass：点名了具体缺失的信息或具体冲突的两个条件，且与 TURN_ORACLE.blocking_constraints 或本轮冲突实质一致；问题不超过 2 个；没有问与冲突无关的偏好或执行细节（例如"要不要评分高的酒店"）；最好给出可选方案或选择方向。
- 判无解 pass：明确说明当前条件无法同时满足；点名冲突的条件；引用了费用或工具证据；给出不超过 2 个放宽方向；没有附带替代方案或偷换条件。
- partial：方向对但不完整，例如只说"预算不够"却没说明与哪些条件冲突，或问题超过 2 个但都相关。
- fail：没点出真正的冲突；问的是无关问题；判无解时编造了原因；或在澄清、判无解的同时自行替换了用户条件。

### B2 改动透明度
适用：第 1 轮及以后、且回复是完整方案的轮次。第 0 轮或非方案回复标 not_applicable。
- pass：简要说明本轮按用户要求改了什么；如果有之前的条件被调整或无法保留，明确告诉用户。用户已说明取舍方向时（例如"膝盖优先"），回复体现了按该方向执行。
- partial：改了但没说，或说明含糊（例如"已根据您的要求调整"而不说调整了什么），且没有违反前序要求。
- fail：RULE_RESULTS 显示前序要求未保留或被替换，而回复没有告知用户（即"悄悄替换"）；或回复声称"其他不变"，但实际有变化。
- 注意：不要求长篇变更日志，一两句清楚的说明即可满分。

### B3 风险与画像的主动提示
适用：出方案的轮次，且 USER_PROFILE 或 CITY_ADVISORIES 中存在与本次行程直接相关的显著风险。显著风险包括：高原且同行有老人或幼儿；酷暑且画像怕热，或有老人、幼儿；冬季低温且画像怕冷或有老人；行动不便且行程步行量大。无显著风险时标 not_applicable。
- pass：用一两句话点出该风险，并给出可执行的应对（例如首日安排休息、正午避开户外、备室内替代），或在行程安排中明显体现并加以说明。
- partial：提到了风险但泛泛而谈（如"注意身体"），或只在行程中体现却完全没有告知用户。
- fail：存在显著风险却完全没有提及，行程安排也没有体现；或者走向另一个极端，大段说教、给出医疗诊断性建议，甚至因此拒绝规划。

### B4 事实边界
适用：所有轮次。
- pass：回复中说得确定的价格、营业时间、可用性、天气信息，都能在 TOOL_EVIDENCE 中找到依据；工具没有覆盖的信息，用了"建议确认""以官方为准"等提示。
- partial：个别次要信息说得过于确定，但不影响出行决策。
- fail：声称完成了助手不可能完成的动作（例如"已为您预约门票""已订好酒店"）；或把工具没有返回的关键信息说成确定事实（例如"该景点不需要预约""当天天气晴好"）。
- TOOL_EVIDENCE 为 not_provided 时：只能判断"声称已完成预订或购买"这一类问题；其余情况标 insufficient_evidence。

## 输出格式

只输出 JSON，不要输出 markdown 代码块或其他文字。

{
  "turn_summary": "一句话概括本轮用户要什么、助手做了什么",
  "dimensions": {
    "B1_clarification_or_no_solution_quality": {
      "label": "pass | partial | fail | not_applicable | insufficient_evidence",
      "reason": "不超过两句",
      "evidence": ["逐字引用的回复原句"],
      "confidence": "high | medium | low"
    },
    "B2_change_transparency": { "label": "", "reason": "", "evidence": [], "confidence": "" },
    "B3_proactive_risk_and_profile": { "label": "", "reason": "", "evidence": [], "confidence": "" },
    "B4_factual_boundary": { "label": "", "reason": "", "evidence": [], "confidence": "" }
  },
  "missing_evidence": [],
  "flags": []
}

输出前自检：
1. 4 个维度都已给出 label；
2. 每个 pass、partial、fail 都有逐字引用的 evidence；
3. 没有推翻 RULE_RESULTS 的结论；
4. 没有使用输入之外的事实。
````

### 字段来源（从 Trip+ 运行结果中取）

| 输入 | 取自 |
|---|---|
| USER_PROFILE、CITY_ADVISORIES | `query_10.json` → `meta_info.base_query_meta.observable_profile`、`city_context.seasonal_advisories` |
| DIALOGUE_SO_FAR、CURRENT_USER_TURN、ASSISTANT_REPLY | `result/.../trajectories/` 中该轮的 `messages` 与 `final_plan` |
| TURN_ORACLE | `query_10.json` → `turns[i]` |
| RULE_RESULTS | `result/.../evaluation/` 中该轮的评分明细 |
| TOOL_EVIDENCE | 同一轮轨迹中的工具返回结果，人工或脚本摘录与回复相关的部分 |

### 版本记录

| 版本 | 日期 | 改动 | 依据 |
|---|---|---|---|
| v1.0 | 待填 | 初版 | — |
| v1.1 | 待填 | 在调试样本上发现问题后的修改 | 见 `评估方案与验证.md` 第 5 节 |
