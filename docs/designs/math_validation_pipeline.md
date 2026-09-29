# 数学验证流水线设计（分阶段方案）

> 依据工作单：[MATH-VALIDATION-001](../work_orders/MATH-VALIDATION-001.md)（2026-08-02 只读审计）
> 状态：设计稿；阶段 A（P0/P1 确定性验证器）已于 2026-08-02 由 [MATH-VALIDATION-002](../work_orders/MATH-VALIDATION-002.md) 实施并完成开发库副本干跑（实测数字见 §2.1）；阶段 B 已试点两轮（[MATH-VALIDATION-003](../completion_reports/MATH-VALIDATION-003.md) 100 题、[MATH-VALIDATION-004](../completion_reports/MATH-VALIDATION-004.md) 多模型+打包，实测见 §3.3.1/§3.2.1/§3.3.2），模型与打包选型待主会话批准后执行全量。
> 关联完成报告：[MATH-VALIDATION-001](../completion_reports/MATH-VALIDATION-001.md)

## 0. 现状基线（只读审计摘要）

- 关联结构化题 3,585；数学验证证据：`pass=8`、`unsupported=3,487`、无记录 `90`。
- 证据表：`content_item_math_validation_evidence`（3,495 行，契约 `content-question-math-contract` v1.0.0，验证器全为 `content-math-dispatch-v1`，reason 全部为 `no_exact_supported_mathematical_form`）。
- 目标集（3,577 = unsupported 3,487 + 无记录 90）：
  - 答案字段可用 2,547（71.2%）；答案或解析齐备 3,376（94.4%）；无答案且无解析 201（5.6%）。
  - 图依赖 1,766（49.4%）；选择题含图 46.6%、填空题 44.5%、解答题 60.4%、计算题 70.0%。
  - 短答案（字母/纯数字/分数/单位/短文字等）1,746（48.8%）。
- 关键事实：`questions.answer` 对全部关联题为空；答案文本在 `question_internal_evidence`（answer/analysis）与证据表 `source_answer` 列；现有契约只实现 2 个精确题型验证器（三角形三边选择、等腰周长），故 3,487 全部 unsupported。

## 1. 铁律（所有阶段必须遵守）

1. `unsupported` 永远不等于 `pass`；`unsupported`/无记录/`fail` 状态的题不得进入讲义选题。
2. 缺数学验证的题不得放行；状态只能在证据链完整时升级（pass 证据必须可复核）。
3. 验证器不得把 stored answer 作为推导输入；stored answer 只用于最终比对。
4. 证据 append-only：新结论写入新契约/验证器版本的新证据行，不修改历史行。
5. fail-closed：无法识别、条件缺失、图未读、无法确定 → `unsupported`，绝不猜测或默认通过。

## 2. 阶段 A：确定性验证器扩展清单（按题型频次排序）

扩展优先级由题量驱动（目标集 3,577 内）：

| 优先级 | 验证器 | 输入要求 | fail-closed 规则 | 预计覆盖 |
| --- | --- | --- | --- | --- |
| P0 | `choice-letter-v1` 选择题字母答案 | 选择题、结构化选项（label+text 均非空）、答案规范化后为单字母 | 选项非结构化/含 `asset_only` 图片选项/题干含图/匹配字母不唯一 → unsupported | 1,131（结构化+字母答案） |
| P1 | `fill-numeric-v1` 填空题数值答案 | 题干文字完整、答案为纯数字/带单位、无图依赖 | 含变量/条件缺失/多解/题干引图 → unsupported | 407（填空数值） |
| P1 | `fill-expr-v1` 填空题表达式答案 | 题干文字完整、答案为代数式（化简/解方程类） | 依赖图/公式缺失/等价性无法判定 → unsupported | 449（填空表达式/多值，部分） |
| P2 | `choice-fact-rule-v1` 事实性选择题 | 题干文字完整、判定可用明确数学事实（性质/定理/定义） | 图依赖/事实表未收录 → unsupported | 选择题可文字判定的子集 |
| P3 | `fill-fraction-v1` / `fill-unit-v1` | 分数/比例/带单位答案 | 单位歧义、非最简无法等价判定 → unsupported | 55 |
| P3 | `multi-blank-v1` 多空答案 | 空数与答案项数一致、每空独立可判 | 空数不一致/任一空不可判 → unsupported | 填空题多空子集 |
| P4 | `calc-short-v1` 计算题短答案 | 步骤短、可数值验证 | 需长步骤/含图 → unsupported | 计算题子集（40 内） |

每条验证器规则：

- 返回 `pass` / `fail` / `unsupported` 三态；`pass` 必须附推导摘要（evidence）与计算值。
- 比对前规范化：NFKC、全角→半角、去空白；分数/根式/小数等价性需显式判定器，无法判定即 `unsupported`。
- `fail` 记录计算值、stored answer 与差异；`unsupported` 记录具体 reason（如 `figure_required`、`option_asset_only`、`formula_missing`）。
- 验证器在 `verification_registry.py` 注册并版本化（`-v1`、`-v2`…），每个验证器配最小回归样本。
- 含图题（49.4%）默认不进阶段 A，直接保持 unsupported。

阶段 A 预期：可尝试覆盖约 1,500–2,000 题（42%–56%），其中能稳定 `pass` 的数量取决于源文本规范化程度；这是实施工作单的验收指标，不是本设计承诺。

### 2.1 阶段 A 实测（MATH-VALIDATION-002，2026-08-02，开发库副本干跑）

- 目标集 3,577 道（3,487 `unsupported` + 90 无记录）全部在副本上重跑 `automatic_verification_runner.py`；真实库字节未改写（SHA-256 前后一致）。
- **`unsupported`→`pass` 总增量：64 道**（全部来自目标集；基线在 `question_verifications` 中为 0 条数学记录）。
- 分验证器 pass 数（证据 `evidence_json.validator_id` 口径）：
  - `choice-letter-v1`：22（数轴整点、`|x−a|+(y−b)²=0` 等腰周长、内角比定型、反证法第一步、命题真假事实表等子规则）；
  - `fill-numeric-v1`：25（数轴移动、中线面积、气球高度、新运算☆、分裂最大数、幂运算、单解周长、直角锐角、顶角比底角大、钟表夹角、整除 9、四点射线、数组第 n 组、握手、平均速度、n 边形、多边形锐角、圆与直线交点、外接圆直径、追及、顺逆风等子规则）；
  - `fill-expr-v1`：11（反证法假设、数轴距离集合、等腰一角求顶角、两角平行、第三边奇数、等腰周长两解、完全平方式、多项式化简等价判定等子规则）；
  - 既有 golden 验证器在目标集内新增 pass：6（`triangle-angle-ratio-v1` 2、`triangle-stability-fact-v1` 2、`triangle-altitudes-median-fact-v1` 1、`pythagorean-reed-pool-v1` 1）。
- 仍 `unsupported`：3,503；Top reason：`figure_required` 1,051、`form_unsupported` 879、解答题不在阶段 A 范围 852、`option_asset_only` 278、`answer_missing` 276、`options_unstructured` 71、`multi_blank_answer` 42、计算题不在阶段 A 范围 40、`answer_not_single_letter` 10、`option_match_missing` 4。
- `fail`：10，全部为既有 `triangle-side-inequality-v1` 按题号分发对非三角形题产生的历史误判（本子项目未改变其行为，仅记录；见完成报告 §5）。
- 40 道抽样（MATH-VALIDATION-001 sample40）在新链下仍为 0 pass，无劣化；既有 8 道 pass 的 golden 路径在回归中仍通过。
- 结论：确定性验证器稳定 pass 数量受源文本规范化程度限制（与 §2 预期一致）；剩余 3,503 道需阶段 B（LLM 批量）/ 阶段 C（人工）覆盖。


## 3. 阶段 B：批量 LLM 验证

### 3.1 适用题量估计

- 候选池（答案或解析齐备）：3,376（94.4%）；其中无图可纯文本验证 1,687，含图需多模态 1,689。
- 阻断：无答案且无解析 201（5.6%）——LLM 也无从比对，必须先补源答案或转人工，禁止强行放行。
- 优先队列：短答案 1,746（字母 1,209/纯数字 400/分数 36/单位 19/短文字 75/字母缩写 7）→ 表达式/多值 493 → 长解答 17。
- 推荐先试点 100 题（覆盖三类答案形态）校准阈值与成本，再全量。

### 3.2 模型选择

- 标准：中文数学符号与单位解析正确、能输出结构化 JSON 证据、推理可复现（低温度或自洽抽样）、成本可控；含图题需要多模态模型。
- 方案内不做具体型号绑定（API 可用性与价格随市场变化），实施工作单确定后记录 `model` + `prompt_version` 于每条证据。
- 约束：不调用外部 API 写入正式库之外的任何数据；每次调用完整记录 input_sha256 与 token 用量。

### 3.2.1 实测（MATH-VALIDATION-004 多模型对比，2026-08-02）

20 题（10 道 003 复核题 + 固定种子新抽 10 道），同一 prompt `llm-math-proof-v1-pilot-20260803`，单题逐题调用：

| 模型 | pass | fail | unsupported | 10 道一致率 | 每千题成本（¥，按工作单所列计费口径） |
| --- | --- | --- | --- | --- | --- |
| `agnes-1.5-flash` | 跳过（models.list 存在但调用恒返 HTTP 503，无上游通道） | — | — | — | — |
| `deepseek-v4-flash` | 10 | 1 | 9 | 10/10 | ≈6.15（按量 9/9，价格带 `*` 待确认） |
| `claude-haiku-4-5` | 7 | 2 | 11 | 8/10 | ≈21.07（按量 3.6/18，价格带 `*` 待确认） |
| `gpt-5.4-mini` | 9 | 3 | 8 | 8/10 | ≈32.09（按量 5.4/32.4） |
| `deepseek-v4-pro-thinking` | 7 | 2 | 11 | 9/10 | ≈540（按次 ¥0.54） |

- 结论：按量口径下 `deepseek-v4-flash` 质量/成本最优（10/10 一致、每千题 ≈¥6.15），但价格带 `*` 需渠道确认；若其实际按次 ¥0.54，每千题成本回到 ≈¥540，与 pro-thinking 同档。
- 通过率高伴随过度推断风险：`gpt-5.4-mini` 对表格结构缺失题 `09ACACC5E1FA` 判 pass（恰合源答案 B），人工期望 unsupported；全量放行仍须 §3.5 抽样复核与交叉验证，`pass` 不等于可放行。

### 3.3 每千题成本估算

假设：每题输入 800–1,200 tokens（题干+选项+参考答案+prompt），输出 200–400 tokens → 每千题约 1.0–1.6M tokens。

| 档位 | 模型类 | 每千题估算 | 3,000 题估算 |
| --- | --- | --- | --- |
| 低端 | 轻量模型（约 $0.15–1.1/M 输入、$0.6–4.4/M 输出） | 约 $0.3–3.5 | 约 $1–10 |
| 高端 | 强推理模型（约 $2.5–15/M 输入、$10–75/M 输出） | 约 $4–45 | 约 $12–135 |


- 区间跨度为数量级估计，实际以选型与试点实测为准；试点 100 题后按实测 token 重估。

### 3.3.1 实测（MATH-VALIDATION-003 试点，2026-08-02）

- 模型：`deepseek-v4-pro-thinking`（仅授权模型；通道映射上游 `astron-code-latest`）；prompt_version：`llm-math-proof-v1-pilot-20260803`；成本估算基准：输入 ¥4/1M、输出 ¥16/1M（保守上限，渠道实际计费待核）。
- 100 题实测：总 token 185,263（prompt 164,831 + completion 20,432）；成本估算约 ¥0.99 / US$0.14（远低于设计估算上限）。
- 每千题：约 185.3 万 token，成本估算约 ¥9.86 / US$1.37（文本题、无图、单轮评估）。
- 试点质量：pass 41 / fail 17 / unsupported 42；独立复核 9/10 一致；fail 中约 9/17 为语义等价/规范化差异（严格字符串比对虚高），约 5/17 为源答案错位/题干碎片，约 3/17 为边界/漏解争议；unsupported 42 中 35 为题干条件碎片缺失（模型正确 fail-closed）。
- 全量推荐（待主会话批准）：限定无图文本池（约 1,026–1,522），预估成本约 ¥10–15 / US$1.4–2.1，预算内；但需先修复语义等价判定、严格 JSON 输出与预筛题干碎片，且复核一致率需达设计门槛。

- 若使用含图多模态输入，按图像 token 另计（每图约数百–1k tokens），建议试点时单独测量。

### 3.3.2 实测（MATH-VALIDATION-004 打包可行性，2026-08-02）

`deepseek-v4-pro-thinking`（按次 ¥0.54/调用）对同样 20 题打包，prompt `llm-math-proof-v1-batch-20260803`（输入不含参考答案，输出多题 JSON）：

| 批大小 | 调用次数 | 格式成功率 | 与单题一致率 | 每题成本 | 每千题成本（¥） |
| --- | --- | --- | --- | --- | --- |
| 5 | 4 | 3/4 = 75% | 16/20 = 80% | ¥0.108 | ≈108 |
| 10 | 2 | 2/2 = 100% | 17/20 = 85% | ¥0.054 | ≈54 |

- 组合格式成功率 5/6 = 83.3%（未超 30% 失败阈值），打包可行；推荐批大小 10（≈¥54/千题，为单题按次方案的 1/10）。
- 唯一格式失败在批 5 第 3 次调用（5 题 fail-closed 记 `batch_format_failed`→unsupported）；打包与单题不一致均落在 unsupported 边界题，无 pass↔fail 翻转。
- 正式打包需新证据契约（§8 更正后立项），含格式失败单题降级重试。

### 3.4 证据契约字段（新契约版本，append-only）

复用 `content_item_math_validation_evidence` 结构并扩展证据内容（实施时以 schema 迁移工作单为准）：

| 字段 | 说明 |
| --- | --- |
| `question_id` / `content_item_id` | 关联题与内容项 |
| `validation_contract_id` / `validator_id` / `validator_version` | 如 `content-question-math-contract` / `llm-math-proof-v1` / `1.1.0` |
| `validation_status` | `pass` / `fail` / `unsupported`（LLM 不确定 → unsupported） |
| `computed_answer` / `source_answer` | 计算值 / 源答案（只读引用，不做输入） |
| `content_sha256` / `input_sha256` | 内容与输入（题干+选项+答案证据+prompt 版本）哈希 |
| `evidence_json` | `model`、`prompt_version`、`reasoning_text`（独立推导）、`answer_derivation`、`confidence`、`figure_used`、`tokens_used`、`warnings` |
| `evidence_sha256` / `created_change_event_id` / `created_at` | 证据完整性、变更事件、时间 |

### 3.5 unsupported→pass 严格放行规则（全部满足才可放行）

1. `input_sha256` 与源证据绑定且可复核。
2. 推导过程独立：prompt 明确禁止引用 stored answer，`reasoning_text` 不包含 stored answer 文本。
3. 规范化比对通过：NFKC、全角/半角、单位统一、数学等价性显式判定。
4. `confidence` ≥ 阈值（试点标定，默认 ≥0.9）。
5. 每批 ≥10% 随机样本人工复核通过。
6. 与确定性验证器交叉验证子集（两链一致才放行）。
7. 图依赖题必须有图像输入且 `figure_used=true`。
8. 比对不一致、低置信、长解答未细分 → `fail` 或 `unsupported`，不放行。
9. 每批记录 token 消耗与失败分布，纳入成本与质量报表。
10. 放行清单经人工复核队列抽样终审后才可计入"已验证"集合。

### 3.6 人工复核触发条件

- 模型自评低置信或返回 `unsupported` 但存在可用答案；
- 计算值与 stored answer 不一致（`fail`）；
- 含图但未读图（`figure_used=false` 且题干引图）；
- 长解答题（解答题/计算题）；
- 某题型首次出现；
- 抽查不一致率 > 5% 时暂停该批次放行。

## 4. 阶段 C：人工复核队列

- 队列对象：阶段 A 无法覆盖的 `unsupported`、阶段 B 的 `fail`/低置信、无答案阻断题（201）。
- 与现有审核流程的关系：本队列是**验证层**，不替代现有 `question_reviews`/质量门禁（`quality_status`、`review_status`）。复核通过只产生"数学验证通过"证据，题目仍需走现有审核与交付流程；同班已交付题禁止重复使用的规则不变。
- 流转：按题型/来源分组，每批 ≤50；复核员对照源块（`source_content_blocks`/`question_internal_evidence`）与题干判定答案正确性；结果 append-only 记录（复用 `question_reviews` 或新增验证复核表，实施时以 schema 为准）。
- 抽样：阶段 B 放行批 ≥10% 全量复核；其余按 10%–20% 抽样；阶段 A 每验证器上线首批 ≥20%。

## 5. 每阶段验收标准、风险与回滚

| 阶段 | 验收标准 | 主要风险 | 回滚方式 |
| --- | --- | --- | --- |
| A | 8 道既有 pass 题在新验证器下仍 pass；40 道抽样判定不劣化；无新增误放行；`run_offline_regression.py` 通过；每验证器覆盖量实测记录 | 正则过拟合单一源格式；规范化等价性误判 | 验证器版本化回退；证据表 append-only，历史不受影响；不放行即无损 |
| B | 试点 100 题人工复核一致率 ≥95%；放行规则 10 条全部实现；成本实测在预算内；无任何 unsupported 未经规则升级为 pass | 幻觉、等价性误判、图像不可用、API 不可用/费用失控、隐私 | 未达阈值整批保持 unsupported；关闭批量开关；已写证据保留但状态不升级 |
| C | 队列流转可审计、无放行遗漏、每条复核记录可追溯 | 人力成本、积压、复核标准漂移 | 暂停放行；存量证据保留；标准版本化 |

## 6. 明确声明

- **`unsupported` 永远不等于 `pass`。**
- **缺数学验证的题不得放行**——包括：LLM 结果未满足全部放行规则、答案缺失无法比对、图依赖题未读图。
- 本设计仅基于只读审计；任何实施（写代码、改 schema、调 API、写库）都需要新工作单授权。

## 7. 下一步（建议）

- 由主会话批准阶段 A 实施工作单（先做 P0/P1 验证器 + 最小回归），并在试点阶段 B 前先完成答案证据绑定与含图题图像映射审计。

## 8. 计费更正（2026-08-02 主会话补录）

- §3.3 的成本估算基于"按 token 计费"假设，与实际不符。
- 实际渠道对 `deepseek-v4-pro-thinking` 为**按次计费 ¥0.540/次**（openai `/v1/chat/completions`、anthropic `/v1/messages`）。
- 更正后：每千题 ≈ 1,000 × ¥0.540 = **¥540（约 $75）**，与 token 用量无关；降低成本的关键杠杆是**一次调用打包多题**（需新证据契约与 prompt 设计）。
- 试点实测：100 次调用 ≈ ¥54（按次口径）；成本上限应按调用次数核算。
- 此更正不改变阶段 B 的抽样、复核与放行规则，只改变成本模型。

### 8.1 补充确认（2026-08-02 晚）

- 用户确认 `deepseek-v4-flash` 为按量计费：输入 ¥3.6/1M、补全 ¥3.6/1M（此前 OCR 误读为 9/9）。
- 按 004 实测 token 用量重算：每千题 ≈¥2.5；全量无图文本池（约 1,026–1,522 题）≈ ¥2.5–3.8。
- `claude-haiku-4-5` 同步确认：输入 ¥3.6/1M、补全 ¥18/1M。
