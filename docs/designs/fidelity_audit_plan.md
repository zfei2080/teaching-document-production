# 保真度审计检查规范

## 目的与边界

本规范验证可信 Word 源文件进入数据库及后续学生载荷的内容保真度。它不重新解题、不调用 API、不把 LLM 结果作为题目资格或门禁，也不修改数据库、源文件或 `questions.stage`。结论只能是`一致`、`可接受差异`、`疑似损耗`、`无法比较`；“无法比较”不能视为通过。

执行入口：`python fidelity_sample_audit.py`。入口固定使用 SQLite `mode=ro` 和 `PRAGMA query_only=ON`，拒绝源目录逃逸，开始和结束均计算数据库 SHA-256。

## 可执行检查

| # | 检查 | 数据来源 | 判定方法 | 自动化/人工 | 一致、损耗、无法比较定义 | 严重级别 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 数量、编号、顺序 | Word COM blocks；`questions.source_document_id/source_question_no` | 对同一原件运行确定性分段，逐个比较源题号序列、数量与数据库序列 | 自动；分段失败时人工复核 | 序列相同为一致；数量/顺序不同为疑似损耗；Word/分段不可读为无法比较 | 阻断 |
| 2 | 题干完整性与截断 | Word COM blocks；`content_item_evidence(field_name=stem)`；`questions.stem` | 先用定位器和块哈希确认原件未漂移，再作 NFKC/空白规范化包含比较；双逗号、悬挂标点仅作提示，不自行认定 | 自动加人工抽样 | 可完整定位且文本匹配为一致；原件中存在而库中缺失/不同为疑似损耗；原件不可读或无证据为无法比较 | 阻断 |
| 3 | 公式、特殊符号、上下标、分式、根式 | Word COM `OMaths` 定位；`source_content_assets(asset_kind=formula)`；源块/题干 | 比较每个公式范围、相邻源块、OMML 可读文本和存储字段的规范化哈希；本轮只记录公式定位，未实现 OMML 结构序列化 | 当前半自动，必须人工抽样 | 哈希及结构序列相同为一致；丢失或顺序不一致为疑似损耗；公式无法抽取/无序列化为无法比较 | 阻断 |
| 4 | 图片、图形、表格绑定 | Word COM assets、`docx_media_forensics`、`question_source_asset_evidence`、`question_assets` | 比较源资产定位/哈希与题目绑定数、位置、类型和最终资产记录；不能用视觉接近推断绑定 | 自动定位加人工视觉抽样 | 一一绑定且哈希匹配为一致；有源绑定证据但无 `question_assets` 或状态 missing 为疑似损耗；无资产或不可读为无法比较 | 阻断（题目依赖资产）/警告 |
| 5 | 选项文本与顺序 | `content_item_evidence(field_name=options)`；`questions.options_json`；原件块 | 按 JSON 数组顺序解析，逐项对源证据和原件切片进行规范化比较，选项图片另交第 4 项 | 自动加人工抽样 | 每项和序列相同为一致；文字、标签、顺序或资产占位改变为疑似损耗；无选择题或无可读证据为无法比较 | 阻断 |
| 6 | 答案与解析完整性 | `question_internal_evidence`；`content_item_evidence(visibility=internal)`；Word blocks | 优先读取受控内部答案/解析证据，按字段和范围与原件比对；`questions.answer/analysis` 为空不作为缺失结论 | 自动加人工抽样 | 有源证据且字段相同为一致；源存在而内部证据缺失/不同为疑似损耗；源/证据不可比为无法比较 | 阻断（教师答案）/警告 |
| 7 | 可追溯性 | `content_source_versions`、`content_extraction_runs`、`source_content_blocks`、`content_item_evidence`、`question_source_asset_evidence` | 验证题目 -> `content_item` -> source version -> source block/asset 的外键存在，且实时 Word block locator 与原哈希匹配 | 自动 | 链完整且定位器哈希一致为一致；链断裂或哈希漂移为疑似损耗；原件不可读时仅数据库链可查、最终为无法比较 | 阻断 |
| 8 | 数据库到中间输出/讲义回读 | `questions`、`content_items.student_payload_json`；未来实际输出 DOCX 与 `artifact_validation` | 当前比较 `questions` 到学生载荷；后续将以实际选题文档的题号、文本、资产哈希和 DOCX 结构回读 | 当前自动；最终渲染须人工抽样 | 载荷字段和题干/选项一致为一致；JSON 无效或字段变形为疑似损耗；没有实际讲义成品时“讲义渲染”部分无法比较 | 阻断（交付前） |

## 本轮基线的已知限制

- 当前 20 个可信 `.doc` 样本的 SHA-256 均与数据库记录一致。Word COM 曾有一次成功读取，但最终带两次重试的可复现运行仍返回 `0x80070520`；因此提交结果将原件内容维度保留为无法比较。COM 会话可用性必须作为后续审计的执行前置条件。
- `question_assets` 现有记录为 0；带 `question_source_asset_evidence` 的题只能证明“源对象有绑定证据、最终资产表未物化”，不能证明对象的视觉语义。
- `source_content_assets` 当前 989 条均为 `ole_object/referenced`；这不是可渲染图片/表格哈希清单，OMML 和媒体字节级比对仍需实现。
- 本轮只验证到 `content_items.student_payload_json`，没有生成真实讲义 DOCX；完整第 8 项需要单独的交付链工作单。

## 后续拆分建议

1. FIDELITY-002：在已知可用 Word COM 桌面会话中建立只读批量重现与数量/编号/题干/选项比较，输出逐字段哈希。
2. FIDELITY-003：实现 OMML、特殊符号和 OLE/公式对象的可追溯序列化及比对。
3. FIDELITY-004：将受控源资产证据物化为题目资产清单，做媒体/表格绑定与哈希审计，不写正式库前先在只读报告验证。
4. FIDELITY-005：以实际选题生成的 DOCX 做题号、文本、资产及版式回渲染审计。
