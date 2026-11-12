# Agent 切图工作流

助手只准备材料；跑这个 skill 的 **agent**（大模型）阅读材料后编写 manifest（组件、部件、状态、规范名）。用户改 JSON 确认后，再由 `.psjs` 按 `layer_id` 导出 1x PNG，写出引擎无关的布局 JSON，并校验默认状态拼图。启动、守卫、`silent` 等规则见 [SKILL.md](../SKILL.md)。模板：[examples/export_slices.psjs](../examples/export_slices.psjs)、[examples/slicing_helper.py](../examples/slicing_helper.py)。

## 决策树

按这个顺序做，**确认前不要导出**。

1. **打开材料。** 先看 `full_render_preview.png`（若已有 Photoshop 导出的 `full_render.png`，以它为准），再对照 `crops_sheet.jpg`、`crops/<layer_id>.png` 和 `group_effects.json`。用 `crops_index.json` 把 id 对回 `tree.json`（可见性、路径、文本、效果）。不要猜测 id，也不要去读不存在的裁切图。选导出目标时，检查该元素最近的父组（或代表该元素的组）上有没有图层效果（如投影）：效果在组上、内层叶子没有时，按下文「组效果：烘焙还是引擎绘制」给该部件选 `effect_mode`——`baked` 导出该组（默认），`engine` 导出子层并把组的 `effect_params` 写进 `engine_effects`。不要因为 `crops/` 里没有该组缩略图就只退回叶子而不记录效果。
2. **识别组件。** 把页签组、底栏、按钮、列表行等交互控件收成组件，按槽位拆开，不要按 PSD 分组原样切开。
3. **分清共用零件和随状态变化的零件。** 图标、始终存在的底等跨状态不变的，放进该组件的 `shared`，文件名不加状态后缀。槽/底的明暗、选中高亮、只在某一态出现的标签等，放进 `states.<state>.parts`。
4. **区分状态变体和旧稿。** 主体/内容不变、只改明度、描边、发光、配色或缩放的，是状态变体；同一槽位但换了内容或徽记的，当旧稿忽略（不写决定、不导出）。**要把打开时隐藏的层当作某一态的资源导出（普通、未选中、锁定、交替背景等），先写一条覆盖它的 `hidden_layer_state` 决定，等用户经 `confirm --decision` 选了「作为状态导出」**（一条可以列多个 `layer_ids`；只有下面「选中槽上的隐藏层」那种叠在可见底上的高亮例外）。吃不准是状态还是旧稿时，同样**必须**写 `hidden_layer_state`（`id`、`type`、`question`、`options`、`layer_ids`，建议附两侧 `crops`）；部件或审核清单里的 `uncertain_hidden` 只是给人看的索引，不能代替决定条目（见第 5、9 节）。决定未答时导出器跳过这些切图（`skipped_pending_decision`）；用户回答后重新导出（第 7 节）。
5. **只给会随状态变化的零件记 missing。** 以同组里状态最完整的那一项为模板（例如某选中项有 base + highlight + label），每个缺少其中一件的兄弟项都要列出，**包括 highlight**。不要因为共用图标没有另一态就标 missing。单个特殊样式的列表行（例如 self 高亮行）只是这一行的一个实例：不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都有成套变体。
6. **判断重复元素是否复用（见下文「重复元素：复用还是分开」）。** 先按 UI 用途和常识（游戏 UI 先验）加上 `reuse_candidates.json` 的证据，从逻辑上判断是否该共用一张图；该复用就用 `reuse_of` 指向同一张共享图。再对照效果图：复用后与设计对不上，先怀疑分析错了，只把对不上的实例拆出去单独导出；逻辑证据很强却对不上，写进 `reuse_decisions` 交用户决定。头像图与头像框必须分开：PSD 里合在一起时，**先自己按下文「头像与框合在一起：先拆，拆不开再问」尝试拆开**，拆完用框 + 占位头像重新拼合并和效果图对比；对得上就按拆分导出；对不上时先按「拆开后拼回去对不上：先定位效果，再决定问不问」逐层关效果定位、按两张修法重做并验证（换内容后约 35 dB，见「阈值」；历史实测见 [slicing-history.md](slicing-history.md) 10.11），只有拆不开、修不好或属于适用边界之外才写 `reuse_decisions`（`type: split_avatar`）交用户，附上哪一层、为什么、修法和 dB 以及对比图。面板、弹窗、卡片、按钮底等 frame 类元素同理：**默认按视觉层次拆成可九宫 / 三宫 / 复用的部件**（见下文「frame 类元素：按视觉层次拆，九宫与装饰」），整张烘焙只在拆不开或拆后对不上时使用，并写 `bake_reason`。
7. **写好审核清单后停下。** 跑 `flatten` + `preview`，`status` 保持 `pending_review`，等用户改 JSON 确认后再导出。用户确认后跑 `confirm` 锁定清单（见第 6 节）；**锁定后不要再改 manifest**，要改就写 `manifest.proposal.json` 并停下等用户重新确认。
8. **确认后一口气跑完，不要停在导出。** 导出 → `build-layout` → `validate` 是一个整体；`export_log.json` 写出不算完成，只有读过 `validate/validate_report.json`（`ok`、`issues`、遮罩 PSNR / ΔE）才算完成。`validate` 的最后一步用**实际导出的 PNG** 拼出默认态并画上标注，写出 `validate/preview_exported_annotated.png` 与 `validate/preview_exported_vs_ps.png`（左 PS 整图、右导出拼图+标注）；把这两张图作为最终成果交给用户。第 7 步导出前的审核预览不变。若 `issues` 里有 `reuse_mismatch`，按第 6 步处理（拆出单独导出，或交用户决定）后重跑。每条长命令都设超时并把输出写进日志；某步超时或报错时，读日志、修正后重跑这一步，不要静默结束。 `issues` 里的问题不能靠 agent 自己写个「接受」标记消掉（`reuse_accepted`、`split_best_effort`、`effects_checked` 之类的说明都不算）：按对应规则修，修不好就停下，把对比图和数字交给用户，等用户在 `reuse_decisions` 里填 `decision`。凡是要用户回答的问题（含「这个可见图层是当前设计还是旧稿」「这个隐藏层是交替状态还是残留」），都写成 `reuse_decisions` 里的结构化条目，不能只写在 notes / checklist；未答前按安全默认导出：不隐藏任何当前可见的图层（见第 5、6、9 节）。用户经 `confirm --decision` 回答（或改答）之后，把导出 → `build-layout` → `validate` 整套重跑：回答之前导出的 PNG 不因事后的回答变成已授权（`exported_before_decision`）。

**完成标准（与 [SKILL.md](../SKILL.md) 第 9 节一致，可检查）：**

- **准备材料：** 材料目录里有 `tree.json`、`crops_index.json`、整图预览（`full_render_preview.png`；若已有 Photoshop 导出的 `full_render.png`，以它为准）；选导出目标前先查 `group_effects.json`。
- **写清单：** 可见设计有条目覆盖；每个要问用户的问题都已写成 `reuse_decisions` 条目（只写在 notes / `uncertain_hidden` / checklist 里不算）。
- **审核门闩：** 已跑 `flatten` + `preview`，`status` 仍为 `pending_review`，并把待问项交给用户。用户确认后：`confirm` 已锁定，`manifest.confirmed.json` 存在；已展示 `not_user_decided` / `blocking_not_user_decided`。一句「清单确认」（`confirm --note`）只锁清单，不是回答。
- **导出：** 用自带 `export_slices.psjs` **按字节**复制，只改 `CONFIG_URL`；`export_log.json` 已写出，exporter sha 与自带脚本一致；这次导出晚于最后一次 `confirm --decision`（之后又记了回答就重导）。
- **布局：** `layout.json` 已写出；各节点的 `kind` 和九宫边距与 `manifest.confirmed.json` 一致（自动检测只补清单里空着的边距，并记 `nine_slice_source`）。
- **校验与收尾：** 导出、`build-layout`、`validate` 连续跑完（每次 `confirm --decision` 之后整套重跑）；写出 `export_log.json` 不算完成，**读过** `validate/validate_report.json`（`ok`、`issues`、遮罩 PSNR / ΔE）才算；两张最终图 `preview_exported_annotated.png` 和 `preview_exported_vs_ps.png` 已交给用户。

选中槽上的隐藏层：若它落在**当前选中项**的槽里，并且是同一主体、只是加了发光或高亮样式，应作为该态的 `highlight`。若默认态就是这一项被选中，即使该高亮层在 PSD 里仍是隐藏的，也要纳入默认选中态，否则拼图会缺一层。只有内容或徽记对不上时才算吃不准，按第 4 步写 `hidden_layer_state`（可以另在 `uncertain_hidden` 里留索引）。这条高亮例外不要扩大成「隐藏层看起来像状态就可以自己导出」：把当前可见的层解释成「选中态」、再把隐藏层解释成该控件的「普通态」，属于采用隐藏层作为状态，必须走决定。

**叠加还是替换：** 判断选中槽上的隐藏层时，先问它是叠在已有资源上面，还是要取代某个资源。一层发光/气泡/光晕盖在当前选中槽之上（与可见的选中底同时存在、范围相近、颜色同系，图层名或所在组像「当前 / 选中 / current / selected / glow / bg」之类），就是**叠加的高亮**，即使形状和底不同也算该态的 `highlight`，不要因为「可能被可见的选中底取代」就挂起提问。只有它与某个可见资源占同一角色、内容或徽记不同（同一位置另一枚图标、另一段文案）时，才是替换，按旧稿处理。认作高亮后，按第 5 步给缺高亮的兄弟项记 `missing`（`role: highlight`）。

### 组效果：烘焙还是引擎绘制

效果（投影、外发光、描边等）在父组上、内层叶子没有时，每个受影响的部件选一种 `effect_mode`，写一句 `effect_reason`（会显示在审核清单和预览标签里，用户可以在审核时改）：

| 方式 | 导出 | 图里 | 边界 |
|------|------|------|------|
| `baked`（默认） | `layer_id` / `select_ids` = 组 id | 效果烘焙进 PNG | `visible_bounds` 含效果，`logical_bounds` 不含 |
| `engine` | `select_ids` = 组的可见子层（`group_effects.json` 的 `engine_select_ids`），`layer_id` 可留组 id 供对照 | 无效果像素 | `logical_bounds` = 图片框；`build-layout` 另写估计的 `effect_bounds` |

`engine` 时把组的 `effect_params` 抄进部件的 `engine_effects`（每条：`type`、`blend_mode`、`color`、`opacity` 0–1、`angle`、`distance`、`size`、外部效果的 `spread` / 内部效果的 `choke`（PS 百分比 0–100）；描边另有 `size`、`position`、`color`），不要凭肉眼猜。

怎么选：

- **项目规则文件或用户指令指定了方式，照做。**
- 倾向 `engine`：部件是 `nine_slice` / 需要拉伸；效果随状态变化而主体共用；很多实例共用一张图；目标引擎/用户约定由引擎画效果；效果是引擎能复现的简单投影或描边。
- 倾向 `baked`：效果复杂（多层叠加、渐变/图案叠加、斜面浮雕、带纹理的发光）；部件尺寸固定；像素还原最重要。
- 组还有不透明度、混合模式或蒙版时，`engine` 也会丢掉这些，要么 `baked`，要么在 `effect_reason` / notes 里写明交给引擎。

**唯一禁止的结果：** 只选子层合并、既没烘焙也没记录 `engine_effects`，组效果悄悄丢失。`flatten` 的 `effect_issues`、`build-layout` 的 `effect_issues` 和 `validate` 的 `issues` 都会把这种部件标成 `group_effect_lost`。

「带效果导出组但临时关掉组效果」需要 batchPlay 切换效果可见性，描述符**未核实**，模板没有实现；`engine` 一律用合并子层。

### 重复元素：复用还是分开

规则不是定死的，按三步走：

1. **先从逻辑上判断。** 结合 UI 用途、游戏开发常识和结构证据（同一智能对象/链接源、同尺寸、同九宫格、重复组件里的同一角色、同名模式），判断这些实例是否本该共用一张资源（例如由代码实例化的列表行底、多处同一图标）。该复用就只导出**一张**：每个实例的 part 写 `reuse_of: <共享名>`，布局 JSON 里每个实例仍有自己的节点、位置和边界，只是 `image` 指向同一张图。共享名去掉实例序号（如 `<module>_bg_row`，不是 `<module>_bg_row1`）。
2. **再对照效果图。** 复用后拼图在某实例区域与设计不一致（颜色、尺寸、效果、内容等逐实例不同，超出容差），先怀疑分析错了：只把不一致的实例改成单独导出（去掉 `reuse_of`），一致的仍可共用。`prepare` 的 `reuse_candidates.json` 和 `validate` 的 `reuse_mismatch` 都给出逐实例差异。
3. **有逻辑证据却对不上时，不要默默决定。** 写进清单顶层 `reuse_decisions`，附对比图（每个实例：设计裁切 | 共享图 | 差异）、问题描述和证据，交用户决定。决定前默认单独导出，并标明待决定。只有该条 `reuse_decisions` 记着用户本人的回答（`decision` 为 `reuse` / `accept` 等，且 `decided_by: "user"`，由 `confirm --decision ID=VALUE` 写入）的实例，校验时差异才改记在 `reuse_mismatch_accepted`；agent 把 `default` 抄进 `decision`、或用户只回了一句「清单确认」都不算用户回答了这一条；agent 自己给 part 写 `reuse_accepted: true` 不算，超过上限照样是 `reuse_mismatch`（报告里带 `reuse_accepted_by_agent_only`）。

**复用误差上限（1.4.9）：** 在共享图的不透明区域（alpha ≥ 128，去掉属性文字、无法模拟的效果区，以及被它上面绘制的节点盖住的像素——那里的误差算上层节点的）内比较设计与拼图：MAE（RGB，0–255）不超过 `reuse_max_mae`（默认 4），且通道最大差 > 16 的像素占比不超过 `reuse_max_pct_gt16`（默认 1%）。默认偏保守；项目规则文件的 `thresholds` 可以改，`validate --reuse-tol-mae / --reuse-tol-pct` 可临时覆盖。超过上限按上面第 2、3 步走：先怀疑复用判断，把这个实例拆出去单独导出；逻辑证据很强才交用户决定。

**游戏 UI 常见先验**（默认判断，像素对照可以推翻，冲突交用户）：

- 列表 / 排行 / 背包的行底：很可能是一张复用的模板，另有少量特殊变体（自己那一行、高亮行等）。
- 头像框：很可能复用。**头像图本身是运行时动态内容**，必须和框分开：单独一个 part，`role: avatar`、`placeholder: true`，导出 PSD 里的示意图作占位，绝不和框烘焙在一起。PSD 里头像和框在同一层或同一个智能对象里时，agent 先按下文的手段拆；拆不开或拆后与效果图对不上，才写 `reuse_decisions`（`type: split_avatar`）交用户，此时默认值不能是合并导出。
- 面板 / 弹窗 / 卡片 / 按钮底：通常是外框（可九宫）+ 标题栏条（横向三宫或并入外框）+ 内嵌底块（可九宫）+ 不可拉伸的装饰（纹理、半调圆点、光斑），按这些层次分别导出，装饰单独成图叠放，不落在九宫可拉伸区。
- 道具/图标格、同款按钮、按名次的奖牌/徽章（少量固定集合）、货币图标、进度条的底和填充：典型的可复用资源。
- 玩家名、分数、数字：`dynamic_text`（只出属性）。固定标签只带纯色填充和描边/投影时用 `static_text`（只出属性）；仅当有引擎画不了的效果（渐变、斜面浮雕、变形、图案叠加、绸缎、内发光等）或已栅格化时才用 `art_text` 出图。文字条目都不得带九宫边距。

`reuse_candidates.json` 里每组带 `evidence.ui_role_prior`（`type: ui_role_prior`，按图层名/路径关键词猜的角色和先验：`likely_reuse`、`small_fixed_set`、`dynamic_placeholder`），以及 `avatar_merge_suspects`（疑似头像与框合并的层）。这些只是证据，由 agent 判断。

### 头像与框合在一起：先拆，拆不开再问

头像图是运行时内容，必须与框分开。`avatar_merge_suspects` 里的层（或 agent 自己看出来的合并层），按下面的顺序尝试，**从最可靠的开始**：

1. **智能对象内部有独立图层（`split_method: smart_object_contents`）。** 跑 `split-avatar`（见下）：它用 psd-tools 打开内嵌的 PSB，按「被剪贴的位图层 + 剪贴底 = 头像，其余可见层 = 框」分类，写出 `hide_inner_for_avatar` / `hide_inner_for_frame`、对比图和建议的部件。导出时 `export_slices.psjs` 对带 `split` 的条目：在**临时导出文档**里对智能对象做「编辑内容」（`placedLayerEditContents`），隐藏 `split.hide_inner` 列出的内层，存回这个临时副本再导出，源文档不动。链接型或非 PSD 的智能对象读不到内层，换下一种方法。不要用「转换为图层」（`placedLayerConvertToLayers`）来拆：它不保留智能对象的缩放，内层按原尺寸展开，再手动缩放回去也对不齐。
2. **文档里头像和框本来就是分开的层（`mask_or_clip_layers`）。** 头像是剪贴到形状上的位图、或带蒙版的层，框是另一层：头像层（连同剪贴底）和框层分别导出，不要选它们的父组一起导出。
3. **框从同类未合并的实例复用（`frame_from_instance`）。** 别处（同画板或其它画板、规范页）有只含框的同款层时，框用它导出并写 `reuse_of`；头像区域用 `split-avatar --frame-instance 合并层id:框层id` 按像素差估出（`approximate: true`），占位头像可以导出合并层本身（叠在框下，运行时被替换），并记下估计的头像框 `avatar_rect_estimate`。
4. **按像素 / 形状分析生成遮罩（`pixel_mask_estimate`）。** 只剩这一种时才用，结果必须标 `split_approximate: true`（或 `split_evidence.approximate: true`）。

**效果归属的默认判断（常识，先于任何修法）。** 框（头像框、标题框、徽章框……）和它框住的动态内容（头像、标题文字、徽章）叠在一起时，落在内容外侧或四周、不依赖内容的外部效果（投影、外发光、外描边等）默认**属于框**：烘进框这一张图，不要挂在内容部件上，也不要漏进框的洞里（拆开后最常见的错误是投影/外发光沿洞的内缘被固化进框，盖到内容上变成暗圈）。这只是默认判断，具体怎么做到用手头的工具（例如 `split-fix` 的两张近似，或用设计给出的单独框资源）；判断依据和做法记在框 part 的 `split_fix` 或 `split.outer_effects: "frame"`。

**内容边缘的框色边、落在内容上的内阴影也归框（1.4.10）。** 只与框的形状有关、不随内容变化的效果——框洞内缘落到内容上的内阴影 / 内发光、包住两侧的组上的内阴影 / 内发光 / 描边 / 斜面浮雕——属于框。注意组效果按组的合成 alpha 画：隐藏框层只留内容导出时，组的 alpha 变成内容的形状，这些效果会沿内容边缘重画，在内容图里留下一圈框色的边（设计里它们画在整组外缘、落在框上）。所以导出内容时要把这类组效果关掉（逐项 `enabled: false`，读回确认，见 [batchplay.md](batchplay.md)「关掉效果」），框一侧保留。如果框色像素是画在内容图层本身里的，就把内容裁 / 遮到洞内，那圈像素归框。

**拆完必须验证。** 用框 + 占位头像按绘制顺序重新拼合，与效果图（合并层的 Photoshop 导出或 `full_render.png` 的同一区域）对比：

- 一致：直接按拆分导出。头像 part `role: avatar`、`placeholder: true`；框 part 可复用（`reuse_of`）。两者都写 `split_method`、`split`（智能对象时）和 `split_evidence`（对比结果、对比图路径、所用工具）。`validate` 最后还会按实际导出的 PNG 拼合再比一次。
- 拆后对不上（低于 `split_match_psnr`，默认 35 dB）：先套用上面的效果归属默认判断；还对不上，再按下文「拆开后拼回去对不上」逐层关效果定位、选修法（默认只出框 + 内容两张，框侧效果近似进 `frame.png`），占位内容和换过的内容都达标就按两张导出。实测 dB 写进 `split_evidence.verified_psnr`（或附 `split_fix` 的结果），不要只写在文字说明里。
- 还低于阈值：**停下交用户**，写 `split_avatar` 决定（附对比图、`attempts`、各步的 dB）。**禁止**自己把低于阈值的拆分标成 best-effort / 已接受后继续导出：`validate` 会按实际导出的 PNG 在框和内容的合并框内重新量 dB（`split_checked`），低于阈值记 `split_below_threshold`，直到该决定记着用户本人的回答（`decided_by: "user"`，用 `confirm --decision ID=VALUE` 记录；只有一句「清单确认」不算回答了这一条，要单独问）。`split-avatar` 给出 `candidate_unverified` 而没有 Photoshop 实测 dB 的，记 `split_unverified`。
- 拆不开、或定位后仍对不上 / 属于适用边界之外：才写 `reuse_decisions`（`type: split_avatar`），附对比图（`compare_image`）、已尝试的方法与失败原因（`attempts: [{method, result, reason}]`），交用户决定。**默认值不能是 `accept_placeholder` 等合并导出**，用最好的拆分候选（如 `split_best_effort`）或 `wait_for_user`。

`split-avatar` 的状态：`candidate`（拆分自洽，且 psd-tools 渲染与该层的 Photoshop 像素一致）；`candidate_unverified`（拆分结构成立，但智能对象图层本身有投影 / 外发光 / 描边等边缘效果而框是环形（`outer_effect_risks`），或框侧有混合模式 / 非正常混合的效果 / 填充 0 的效果层叠在头像上，或 psd-tools 复现不了内层效果；必须用 Photoshop 按拆分导出后和合并导出对比再决定）；`mismatch` / `no_separable_layers` / `not_embedded_psd`（换下一种方法）。psd-tools 只是离线证据，最终以 Photoshop 导出的拼合对比为准。

`flatten` 与 `validate` 会检查（`avatar_split_issues` / `issues`）：`split_avatar_merged_default`（决定默认合并导出）、`split_avatar_not_attempted`（没有 `attempts` 或 `compare_image`）、`avatar_merged_unsplit`（疑似合并层被整张导出，既没拆也没有 split_avatar 决定；会顺着图层树找到导出其父组的条目）、`split_method_unknown`、`split_evidence_missing`、`split_avatar_not_placeholder`、`split_spec_missing`（智能对象拆分缺 `split.hide_inner`）、`split_approximate_unmarked`。

### 拆开后拼回去对不上：先定位效果，再决定问不问

适用于头像、图标、道具等动态内容与带效果的框拆开的情况，也适用于其他拆分（按钮底与图标、卡片与插图等）。拆完用「框 + 占位内容」拼回去，和合并导出（同一临时副本、同一画布）比 PSNR。低于精确拆分的参考阈值（约 40 dB）或两张近似的阈值（约 35 dB，见「阈值」；历史实测见 [slicing-history.md](slicing-history.md) 10.10、10.11）时，**agent 先自己定位**，再决定要不要交用户。

**0. 先套用效果归属默认判断**（见上文「效果归属的默认判断」）：外侧的投影 / 外发光 / 描边归框，烘进框、不进洞。多数内缘发暗的问题到这一步就解决了。

**1. 定位实验（只在临时副本里做）。** 逐个关掉可能的效果，每种组合都导出 合并 / 框 / 内容 三张，同一组合内拼回去和合并导出比 PSNR：

- 外层：智能对象图层或包住两侧的组本身的效果（投影、外发光、描边、斜面浮雕）。在临时导出文档里对该层关效果。
- 内层（「编辑内容」里）：组内阴影（各项分开关，正片叠底和变亮的作用不同）、非 normal 混合的层（线性加深等）、填充为 0 的纯效果层（内发光、内阴影）、描边。
- 再加一组「全关」。记下每项的 dB 变化。
- 用像素证据说明机制：例如内容区内紧贴框内缘的一圈（几个像素宽）的平均亮度，合并导出 vs 拼回去，分上缘 / 下缘；以及框单独导出在这一圈里的平均 alpha（本应为 0）。

**2. 已知机制。**

- 效果沿所属层（或组）的 alpha 边缘绘制。拆开后形状从实心变成环，投影、外发光、描边、内阴影就多出一道内缘：
  - 外投影 / 外发光投进洞里，被固化进框；再用 normal 盖到内容上，内缘变暗（或被染色）。投影角度朝下时上缘最重。
  - 组内阴影按新的环形 alpha 重画：例如变亮项在环的内缘多画一道亮边（落在框的像素上）。
- 非 normal 混合的层（线性加深、正片叠底……）单独导出时和透明底混合，退化成 normal，作用不到内容上。

**3. 修法：默认只出两张（内容 + `frame.png`，普通混合）。**

引擎从下往上只画：

1. 内容（运行时替换，占位导出）。
2. 一张 `frame.png`，normal / src-over。做法：
   - 内容透不出来的区域（内容外侧、框完全不透明处）直接从合并导出裁。投影、组效果落在这些区域上的部分都已算对，和具体内容无关。
   - 框内缘的抗锯齿那一圈，用「单独导出的框」（外层和组的边缘效果关掉）作为底。
   - 叠在内容上的框侧效果（线性加深、内发光、填充 0 的纯效果层等）**不要默认拆成 overlay 部件**。把它们近似成一层与内容无关的 normal 颜色和 alpha：对每个像素，用占位内容（真实的合并导出当目标）再加上一组代表性内容（变亮 / 变暗 / 反相、纯色、渐变，以及能拿到的另一份真实内容），按最小二乘拟合 `src-over(C, a)`，使「框盖到内容上」接近真实叠法。代表性内容上的「真实」结果用混合公式模拟（线性加深、normal 等，见下表），须在证据里标明是模拟。把这层烘进同一张 `frame.png`。
   - 不要把占位照片的内缘一圈直接裁进框来凑 dB：换内容后会露出旧照片（历史实测见 [slicing-history.md](slicing-history.md) 10.11）。

   - **首选：用纯色探针的真实渲染拟合（1.4.10，`split-fix --probe`）。** 在同一临时副本里，把内容像素隐藏、内容形状（剪贴底或内容图层的形状）填成几种纯色，各导出一次合并图（与 merged 同画布）：至少深色、亮色、中灰三种，再加一两种彩色更稳。框侧所有效果（内阴影、内发光、线性加深 / 正片叠底层、组效果）都由 Photoshop 真正画在这些纯色上，不需要公式模拟，也不需要单独导出 overlay。在内容透出来的像素上（各探针渲染不同之处）按最小二乘解 `src-over(C, a)`；深浅不同的探针让方程条件良好，低 alpha 的抗锯齿边缘也稳定。换内容用留一法验证：去掉一种探针重新拟合，预测它的真实渲染。
   - **不要在框洞内缘的抗锯齿带上逐像素硬解模拟目标。** 模拟只是近似，低 alpha 像素的解会在深色和亮色之间来回跳，拼出来是阶梯和暗圈。没有探针渲染时，模拟路径在这条带上把方程按邻域合并求解（`split_fix.json` 里 `aa_band_smoothed: true`），但模拟的换内容 dB 可能偏乐观，证据里照实写「模拟」。

框 part 写 `split_fix: two_part_approx`、`blend_mode: normal`。`split_evidence` 写近似方法（`probe_fit_src_over` 或 `least_squares_src_over`）、占位内容上的 dB、换内容后的 dB（注明是真实渲染留一法还是模拟）和对比图。

**验证：**

```bash
python slicing_helper.py split-fix --merged M.png --content C.png --frame F.png \
  --overlay burn.png --overlay-blend linear_burn --overlay glow.png --overlay-blend normal \
  --swap-content other.png --out-dir out/
# 默认把 overlay 烘进 frame.png，只产出两张（frame.png + 内容）。输入不同尺寸时用 PATH#X,Y 和 --canvas W,H。
# 阈值默认 35 dB（--threshold）：占位内容 vs 合并导出，以及换内容 vs 公式模拟，都要达到才算通过。
```

`split_fix.json` 含 `mode: two_part_approx`、`match`、`swap_min_psnr`（变亮 / 变暗 / 反相和 `--swap-content`）、`swap_all_min_psnr`（含纯色诊断）、`simulated_swap: true`、`approx: least_squares_src_over`。通过才按两张导出。

用探针时：

```bash
python slicing_helper.py split-fix --merged M.png --content C.png --frame F.png \
  --probe 0,0,0=probe_black.png --probe 255,255,255=probe_white.png --probe 128,128,128=probe_gray.png \
  --probe 220,40,40=probe_red.png --out-dir out/
```

`split_fix.json` 含 `approx: probe_fit_src_over`、`swap_loo`（每种探针留一法的 dB）、`swap_min_psnr`、`swap_is_real_render: true`、`simulated_swap: false`、`probe_luma_span`、`zone_partial_content_px`。少于 3 种探针时没有留一法，`swap_min_psnr` 为 null，不算通过。两种路径都报 `aa_band_noise`（抗锯齿带上预乘颜色与 3×3 中值之差，孤立跳变才会抬高它；只作诊断，同一输入改前改后对比用）和 `content_check`：框完全透明、内容不透明的像素上，内容导出应与合并图一致；不一致（低于两张拆法阈值）记 `rim_suspect: true`，不算通过——多半是组效果在内容 alpha 上重画了。框完全盖住的那圈边从合并图上看不出来，仍要靠上面的归属规则在导出时关掉。

**只有两张明显对不上时**（换内容后低于约 35 dB，或占位内容也明显偏低），才把 overlay 部件或保留合并作为选项，带对比图交用户，而不是默认拆成 3–4 张。此时用 `split-fix --emit-overlays`（精确按混合公式叠，阈值 40 dB）。对引擎的要求（`ENGINE_BLEND_NOTES`）：

| blend_mode | 公式 | 引擎实现 |
|---|---|---|
| normal | src-over | 默认（两张拆法只用这个） |
| multiply | dst × src | 预乘：`ONE_MINUS_SRC_ALPHA + DST_COLOR` |
| screen | 1 − (1 − dst)(1 − src) | `ONE, ONE_MINUS_SRC_COLOR` |
| linear_burn | max(0, dst + src − 1)，按 alpha 混合 = dst − a·(1 − src) | **reverse-subtract** 混合方程：着色器输出 a·(1 − src)，因子 `ONE, ONE`，结果截到 0 |
| lighten / darken | max / min，按 alpha 混合 | MAX / MIN 方程需预乘处理或着色器 |
| linear_dodge | min(1, dst + src) | 加法 `ONE, ONE` |

引擎不支持某个模式、只能用 normal 近似时，优先走上面的两张最小二乘近似；仍对不上再交用户。`validate` 拼图时若部件带了 `blend_mode` 就按对应公式画（不可重放的模式按 normal 并在 `placed` 里记 `blend_mode_approximated_normal`）。

**4. 适用边界：以下情况交用户决定（或接受近似）。**

- 效果依赖下层内容：颜色减淡 / 加深在不同内容上差异大，明度 / 色相 / 饱和度 / 颜色类混合，叠加 / 柔光等对比类混合，调整图层，模糊，蒙版引用内容像素。
- 引擎不支持对应混合模式，只能用 normal 近似。
- 链接型智能对象、带变形 / 透视的智能对象（读不到内层或离线映射不准）。
- 内缘误差只有一两个像素宽时，可以接受近似，写明。
- 把占位内容烘进框（例如内缘一圈连同占位照片烘进 `frame.png`）只适用于内容固定不换的情况，必须写 `bake_reason`；内容会换时，换上的内容外会露出一圈旧的占位内容。只烘内容底色也不行（实测 31–34 dB）。

交用户时带上：**哪一层、为什么（机制）、修法和每种修法的 dB**，以及对比图（合并导出 | 拼回去 | 差异×3，每行标 PSNR）。

**helper 的自动化：**

- `split-avatar` 对每个智能对象额外给出：`frame_hole_fraction`（框单独渲染的 alpha 里被围住的透明区占比，≥ 0.05 视为环）、`outer_effect_risks`（智能对象图层上的投影 / 外发光 / 描边 / 斜面浮雕 / 内阴影等边缘效果，框是环时报出）、`edge_effect_risks`（包住两侧的组的边缘效果 → `frame_from_merged`；叠在内容上的非 normal 混合层或填充 0 的效果层 → `overlay_part`；依赖内容的混合或调整图层 → `needs_user`）和 `effect_fix`（默认建议只有框一张，`split_fix: two_part_approx`；overlay 列在 `optional_overlays` 里，仅当两张近似低于约 35 dB 时才作为选项）。有外层边缘效果时状态为 `candidate_unverified`。`split-fix` 默认输出 `frame.png`（两张拆法）；`--emit-overlays` 才写出独立 overlay。
- `flatten` / `validate` 的 issues：`outer_effect_into_hole`（拆分的框 part 所属的智能对象，或与内容 part 共同的父组，在图层树里带边缘效果，而 part 没写 `split_fix` 或 `split_evidence.effects_checked`）、`blend_mode_content_dependent`、`blend_mode_not_replayable`。
- **没有自动化：** 定位实验本身（在 Photoshop 里逐项关效果并导出 合并 / 框 / 内容）、overlay 的单独导出（`export_slices.psjs` 目前只支持 `split.hide_inner`，不会关外层或组的效果，也不会只保留一层；这几张要按 `effect_fix.photoshop_exports` 另写临时脚本导出）、内缘亮度带的像素证据（要自己算）。`outer_effect_into_hole` 只看图层树里的效果名，不检查框是否真成环。

### 阈值（项目规则文件可覆盖）

阈值写在项目规则文件（`--rules` 用的同一个 JSON，`manifest.naming_rules_file`）的 `thresholds` 里，`build-layout` 带进布局，`validate --rules` 或单项参数可再覆盖（优先级：命令行 > `--rules` > 布局 > 默认）：

```json
"thresholds": { "split_match_psnr": 35, "reuse_max_mae": 4, "reuse_max_pct_gt16": 1 }
```

报告的 `thresholds` 记实际用的值。阈值只能由用户或项目规则改，agent 不要为了过校验去调。

### frame 类元素：按视觉层次拆，九宫与装饰

面板、弹窗、卡片、按钮底等 frame 类元素，**默认先按视觉层次拆成可九宫 / 三宫 / 复用的部件**，不要整张烘焙：

| 部件 | 典型图层 | 类型 |
|------|----------|------|
| 外框（边、底色、外发光/投影） | 覆盖整个外框范围的几层形状 | `nine_slice` |
| 标题栏条 | 贴着顶部、横向占满的扁条 | 只横向拉伸时可并入外框；有纵向渐变、纵向拉伸会破坏渐变时，单独导出成**横向三宫**的头部条，外框主体另做九宫 |
| 内嵌底块（圆角块、内容区） | 落在外框里面的圆角形状 | `nine_slice` |
| 装饰（纹理、半调圆点、光斑、花纹） | 混合模式非正常的层、名字像图案的层 | `image`，**不拉伸**，按原位置（常为居中）叠放；绝不放进九宫可拉伸区 |

做法按可靠性：**优先用 PSD 图层结构**（各层或各层组分别导出，`select_ids` 只选该部件的层）；图层结构不够时再做像素分析（像素级估算要标近似）。装饰层是非正常混合模式（如颜色加深）时，单独导出会丢掉它对底下的作用：要么在部件上写 `blend_mode` 交引擎按同样方式混合，要么把它与底下那一小块一起烘焙成不拉伸的装饰图；不要因此把整个面板烘成一张。拆完同样用各部件拼合与效果图对比。整张烘焙只在拆不开或拆后对不上时使用，在该 part 上写 `bake_reason`（一句话：试了什么、为什么不行）。

九宫的 `kind` 和边距由清单决定，用户确认后就是交付值：`build-layout` 原样写进布局，不会把 `image` 改成 `nine_slice`，也不会改已确认的边距（例如只横向拉伸的标题栏「上 + 下 = 高度」）。清单里 `kind: nine_slice` 却没填（或只填了一部分）边距时，`build-layout` 用导出 PNG 检测补上空着的那几项，并在节点上记 `nine_slice_source: auto_detected`（部分补齐为 `manifest+auto_detected`）和 `nine_slice_auto_filled`；其它节点的检测结果只作为建议写在 `nine_slice_detected`。

助手证据：`prepare` 写出 `frame_split_candidates.json`（名字像 frame、尺寸 ≥ 160 px、至少 3 个可见层的组，以及按外框范围的启发式层角色：外框 ≥ 85 %、顶部扁条为标题栏、非正常混合或图案名为装饰、其余为内嵌块，并给出建议部件）。`flatten` 的 `frame_split_issues` 与 `validate` 的 `issues` 报：`frame_baked_whole`（frame 类、≥ 3 层、最短边 ≥ 160 px、没设九宫也没写 `bake_reason` 的整张 image，附层角色和建议部件），`decoration_in_stretch_zone`（九宫部件里的装饰层落在可拉伸带上）。这些是启发式，agent 对照裁切图确认。

助手子命令：

```text
python slicing_helper.py prepare --psd design.psb --out-dir out [--artboard <画板名或id>]
python slicing_helper.py split-avatar --psd design.psb --candidates out/reuse_candidates.json --out-dir out/avatar_split [--layer-id <id>] [--frame-instance <合并层id>:<框层id>]
python slicing_helper.py split-fix --merged M.png --content C.png --frame F.png [--probe R,G,B=P.png ...] [--overlay O.png --overlay-blend linear_burn ...] [--swap-content other.png] [--emit-overlays] --out-dir out/split_fix   # 默认烘成两张；--probe 用真实纯色渲染拟合（首选）；--emit-overlays 才拆 overlay
python slicing_helper.py flatten --manifest out/manifest.json
python slicing_helper.py preview --manifest out/manifest.json --out out/preview_annotated.png [--ref out/full_render.png]
python slicing_helper.py build-layout --manifest out/manifest.json --export-log out/export_log.json --slices-dir out/slices --out out/layout.json
python slicing_helper.py validate --layout out/layout.json --slices-dir out/slices --full-render out/full_render.png --out-dir out/validate [--fonts-dir <字体目录>] [--text-renderer auto|freetype|skia]
#   validate 末尾写出 preview_exported_annotated.png / preview_exported_vs_ps.png（最终交付图）
# flatten / build-layout / validate 自动读取 manifest 旁的 group_effects.json，也可用 --group-effects 指定
# 可选：关键词草稿，只写 draft_manifest.json，不能代替 agent 编写的 manifest
python slicing_helper.py infer --psd design.psb --out-dir out [--artboard <画板名或id>] [--rules naming_rules.json]
```

`--artboard` 可选；省略则处理所有可见顶层画板，没有画板时处理整份画布。`--rules` 只是给 agent 看的项目命名约定，不再作为自动分类的主路径。

### Python 环境

优先使用**项目虚拟环境**，不要把包装进系统 Python。系统解释器里没有依赖时，不要对系统 Python 执行 `pip install`。

```text
python3 -m venv .venv
# Windows：.venv\Scripts\activate    POSIX：source .venv/bin/activate
pip install "psd-tools[composite]" pillow numpy
# 必装（布局里有 dynamic_text / static_text 时 validate 需要）：HarfBuzz 排版 + FreeType 填充轮廓；scipy 用于描边/扩展
pip install uharfbuzz freetype-py scipy
# 只在 --text-renderer skia 时需要
pip install uharfbuzz skia-python
# 之后一律用该环境的解释器调用助手，例如：
#   .venv\Scripts\python.exe examples/slicing_helper.py prepare ...
#   .venv/bin/python examples/slicing_helper.py prepare ...
```

若工作区附近已有装好这些包的 venv，直接用它的 `python`，不必再装一份。需要的包：`psd-tools`、Pillow、numpy；预览合成与九宫格分析还要 `scipy`（已包含在 `psd-tools[composite]` 里）。布局里有 `dynamic_text` 或 `static_text` 时，`validate` **必须**有 `uharfbuzz` + `freetype-py`（或 `uharfbuzz` + `skia-python`）。缺了就在画文字前失败：退出码 2，stderr 和 `validate_report.json` 的 `error`（`type: text_renderer_unavailable`、`message`、`install`）给出原因和安装命令，不写拼图和指标。没有回退渲染器。

打开 PSD/PSB 时传入 `ParseLimits`。已在 **psd-tools 1.26.1** 核实的签名是：

```python
from psd_tools import PSDImage
from psd_tools.psd.parse_limits import ParseLimits  # 也可 from psd_tools import ParseLimits

# ParseLimits(max_read_bytes=None, max_total_bytes=None, max_objects=1_000_000, max_nesting_depth=64)
# max_objects 默认为 1_000_000；大文档会触发 ParseLimitError。传入 None 可关闭这一上限。
# 不要写 ParseLimits.max_objects = None（那不是正确用法）。
PSDImage.open(path, parse_limits=ParseLimits(max_objects=None))
```

助手的 `open_psd()` 已经这样调用。不要猜参数名，也不要对系统 Python 安装依赖。

---

## 1. 分工

| 谁 | 做什么 |
|----|--------|
| 助手 `prepare` | 准备材料：默认状态整图预览、带 id 的逐层裁切缩略图、含隐藏层的图层树 JSON、基于像素的九宫格候选。不做命名，不组组件，不判定状态。 |
| **Agent** | 同时看整图、裁切图和图层树，编写 `manifest.json`：组件、部件、规范名、类型、状态和语义层级。 |
| 用户 | 看标注预览，改 JSON 确认；`status` 在确认前保持 `pending_review`。真正缺失的状态列在 `missing` 里，由用户决定要不要补图。 |
| 助手 `flatten` + `.psjs` 导出 | 把组件树展平为导出条目；按 `layer_id` 出 1x PNG。属于已识别交互组件某状态的隐藏层可临时显示后导出，然后恢复可见性，文档不保存。 |
| 助手 `build-layout` / `validate` | 写出按组件语义组织的布局 JSON；把**默认状态**拼回去与 PS 整图对比；再为每个交替状态单独出预览图。 |

隐藏层里与交互状态无关的内容忽略，不要导出。只有按设计会随状态变化的部件，在 PSD 里又找不到合格变体时，才写入 `missing`，**禁止捏造**。跨状态不变的零件（例如两个状态下都用同一枚图标）放在组件的 `shared` 里，不要因为「没有另一态的图标」就把图标标成 missing。

`infer` 仍可用，但只写出 `draft_manifest.json`（`status: draft_for_agent_review`）。那是关键词草稿，agent 必须审过或重写，不能直接当确认清单导出。

---

## 2. 准备材料：要看什么

`prepare` 在 `--out-dir` 写出：

| 文件 | 用途 |
|------|------|
| `full_render_preview.png` | 画板**当前可见/默认状态**的合成（psd-tools，部分图层效果可能缺失）。若已有 Photoshop 导出的 `full_render.png`，审核和校验以 PS 整图为准。 |
| `tree.json` | 图层树：`name`、`path`、`kind`、文本内容、效果摘要、`visible`（含隐藏层）、`bbox`、`layer_id`。 |
| `crops/<layer_id>.png` | 主要是可导出叶子（及剪贴单元）的裁切图，文件名就是 `layer_id`。隐藏层只要与画板相交且有像素也会出图。带图层效果的组在能合成时也会出一张组裁切图（psd-tools 缩略图可能画不出烘焙效果，仍请用组 id 导出）。空层、不相交或合成后无像素的层不会出图。 |
| `crops_index.json` | 已写出的裁切图，以及带图层效果的组（即使暂无缩略图）：id、可见性、路径、效果及 `effect_params`、`select_ids` / `suggested_select_ids`、九宫格候选。叶子若挂在带效果的父组下，会标注 `parent_group_id`、`parent_effects` 和 `parent_effect_params`。查 id 用这份索引，不要猜文件名。 |
| `crops_sheet.jpg` | 所有裁切图的拼图，隐藏项标 `HID`，带效果的组标 `FX`，便于快速扫。 |
| `group_effects.json` | 带图层效果的组：效果列表、`effect_params`（psd-tools 读出的参数；读不懂的效果保留原类名并标 `unknown`）、子层 id（`child_leaf_ids` / `child_ids`）、`ancestor_ids`、`baked` 用的 `select_ids`（组 id）、`engine` 用的 `engine_select_ids`（可见子层）、可选的组裁切路径。选导出目标和 `effect_mode` 时先看这份。 |
| `reuse_candidates.json` | 重复元素的证据：每组的 `layer_ids`、`pixel_clusters`（像素一致的子集，预乘 RGBA 平均差 ≤ `tolerance_mad`）、逐实例差异、`evidence`（`ui_role_prior`、同智能对象 id、同名/同路径模式、同尺寸、同九宫格）和 `hint`（`reuse` / `split` / `needs_decision` / `dynamic_placeholder`）；逻辑相似但像素不同的组附 `reuse_compare/<组id>.png`（设计 \| 共享图 \| 差异）。另有 `avatar_merge_suspects`。只是证据，不替 agent 决定。 |
| `nine_slice_candidates.json` | 像素启发式给出的九宫格候选（最长的均匀相邻行/列；两边边距都小于 `min_run` 则拒绝）。agent 决定采不采纳。 |
| `frame_split_candidates.json` | frame 类组（面板、弹窗、卡片、按钮底）的层角色启发式和建议部件（外框 / 标题栏 / 内嵌块 / 装饰），见「frame 类元素」。 |
| `split-avatar` 输出（另跑） | `<out>/split_candidates.json`：每个疑似合并层的尝试结果、内层分类、`hide_inner_*`、对比数据和 `suggested_parts`；`<out>/<layer_id>/` 下有 avatar / frame / recomposite / compare 图。 |
| `split-fix` 输出（另跑） | 默认两张：`<out>/frame.png`、`recomposite.png`、`residual.png`、`frame_from_merged.png`、`compare.png`、`split_fix.json`（`mode: two_part_approx`、`match`、`swap_min_psnr`、`simulated_swap`、`approx`、`aa_band_noise`、`content_check`、`pass`；用 `--probe` 时另有 `swap_loo`、`swap_is_real_render`）。`--emit-overlays` 时另写 `overlay<N>_<mode>.png`，阈值 40 dB。未通过时退出码 2。 |
| `materials.json` | 上述路径和数量统计。 |

阅读顺序建议：先看整图，理解屏幕结构；再对照 `crops_sheet.jpg` / 单张裁切图看每个零件长什么样；用 `crops_index.json` 和 `group_effects.json` 把 id 对回 `tree.json` 的路径、可见性、文本和效果。不要只根据图层名做决定，名称经常不准。

`crops/` 里不是树上每一个 `layer_id` 都有文件。查某个候选 id 时，先在 `crops_index.json`、`group_effects.json`（或 `crops_sheet.jpg` 上的 id）里找，不要直接拼 `crops/<id>.png` 去猜。若某个组 id 没有裁切图，或打开 `crops/<group_id>.png` 得到 404：**不要**因此只改用内层叶子。先在 `tree.json` / `group_effects.json` 看该组有没有图层效果（如 DropShadow、外发光）。若效果在组上、内层叶子（智能对象、形状等）没有，按「组效果：烘焙还是引擎绘制」选：`baked` 导出该组（`layer_id` 和 `select_ids` 用组 id，可见框包含烘焙后的效果，不要裁到叶子的逻辑框 `logical_bounds` / `boundsNoEffects`）；`engine` 导出子层，并把组的 `effect_params` 写进 `engine_effects`。没有裁切图不是不记录效果的理由。助手会尽量写出组裁切图，并在叶子条目上标注 `parent_group_id` 与 `suggested_select_ids`；psd-tools 缩略图可能仍画不出投影，Photoshop 按组导出时才会把效果烘焙进去。

若索引里没有这个 id，也不要反复尝试打开不存在的文件，更不要把它当成「缺了一态」。回到 `tree.json`：无效果的分组可以继续用其中有裁切图的叶子；空层、在画板外、或合成后没有像素的，跳过并在 notes 里说明。没有裁切图本身不能证明存在或缺失某个状态。

---

## 3. 识别组件与状态

把「同一控件的不同零件、不同状态」收成组件，而不是按 PSD 分组原样切开。

常见交互组件：

- **页签 / 分段开关**（`tab_group` → 若干 `tab`）：每个页签拆成独立部件——底/背景、选中高亮、图标、文案；每个页签有自己的状态（如 `normal` / `selected`）。两个页签的开关：一组 `tab_group`，两个 `tab`，各有 normal 与 selected；选中高亮是单独资源，文案也是单独资源。
- **底栏导航**（`navbar` → 若干 `nav_item`）：同样按项拆图标、文案、选中态，不要把整条导航烘焙成一张图。
- **按钮、列表行、开关**：底、高亮、图标、文字分开；状态（normal / pressed / selected / disabled）分开。

先分清哪些零件是**跨状态共用**的，哪些是**随状态变化**的。判断依据是设计本身：看已经识别出的成对状态（例如某导航项的 selected 对 normal），哪些图层换了，哪些没换。没换的（同一枚图标、同一段始终存在的底）放进该组件的 `shared`（也作 `common_parts` 或 `parts`），规范名**不加状态后缀**。会换的（槽/底的明暗、选中高亮、只在选中时出现的标签）放进 `states.<state>.parts`，文件名带上状态段。

规则：

1. **不要把状态烘焙在一起。** 选中描边不要画进底图；on/off 不要合成一张。
2. **看整图里当前显示的是哪一态**，把它标为该组件的 `default_state`。
3. **到隐藏层里找交替状态。** 先判断它是「同一控件的另一态」还是「旧版 / 备选稿」，不要因为位置重叠就当成状态。
   - **状态变体**：主体/内容不变（同一个形象、同一句文案、同一块底），差别只在状态样式，例如明度、饱和度、高亮、描边、发光、缩放、配色。这类可以纳入该组件，导出时临时显示；但导出前要有覆盖这些 `layer_ids` 的 `hidden_layer_state` 决定和用户经 `confirm --decision` 记录的「作为状态导出」回答（`state` 之类），否则 `validate` 报 `hidden_layer_exported_without_decision`。下面的选中槽高亮例外。决定之前不导出该层（导出器会跳过它，记 `skipped_pending_decision`），也不用它替换文档当前可见的外观。
   - **旧版或备选稿**：同一位置的隐藏层换了内容或换了母题（例如换了一枚不同的徽记，或数字与符号不是同一套图形），应忽略，不要导出，也不要用它去填某一态。若该零件本身是跨状态共用的，把已经在用的那份真资源放进 `shared`，**不要**因为忽略了旧稿就把图标写成 missing。
   - **吃不准**：不要擅自选边。在 `reuse_decisions` 里写一条 `hidden_layer_state`（`layer_ids` 带上两边的图层、`question`、`options`，建议附 `crops` 裁切图路径），让用户决定。审核清单 / 部件上的 `uncertain_hidden` 只能当索引：写了却没有覆盖其 `layer_ids` 的阻塞性决定，等于没提问，`validate` 报 `uncertain_hidden_without_decision`。
   隐藏层若与任何已识别组件的状态无关，同样忽略。

   落在**当前选中项槽位**上的隐藏层（先做文首的「叠加还是替换」判断）：若主体/内容与该项正在使用的资源相同，只是加了发光、描边或高亮样式，应作为该选中态的 `highlight`（不要挂起提问）。若默认态就是这一项被选中，即便该层在 PSD 里仍隐藏，也要纳入默认选中态，否则校验拼图会少一层；这种叠在默认态可见底上的高亮不需要决定。只有内容或徽记明显不同、无法判断是高亮还是旧稿时，才按「吃不准」写 `hidden_layer_state`。
4. **状态是那一刻画面上全部部件的状态。** 一个状态要写全：评分星、进度点、等级点这类**有序累积**的组，「第 N 档」包含所有更低的格子（5 星 = 第 1–5 格都亮，不是只点亮第 5 格）。这类组在清单里声明 `cumulative: true`（见第 5 节），`build-layout` 会展开成 `level_0` … `level_N` 的完整状态，`validate` 按整档出预览。要单独写某个组合时，在状态上写完整的 `activation`（每个成员各是什么状态）。没声明的组不按名字猜。
5. **只有按设计会随状态变化的部件，找不到合格变体时，才记 missing。** 先看同组里哪一项的某一态最完整（零件最多），用它的角色集合当模板。例如某个选中项有 base + highlight + label，则每个缺少其中一件的兄弟项都要在 `missing` 里列出对应 `{ "state": "selected", "role": "..." }`，**包括 highlight**，等用户确认要不要补。跨状态不变的零件即使只有一份，也不是缺失，不要写「没有另一态的图标」。禁止捏造未画出的变体。

   单个特殊样式的列表行（例如只有一行画了 self 高亮）只是**这一行的一个实例**，不是「每一行都该有 self / 这一行还缺 normal」。不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都画出了成套状态变体。
6. **导出目标要看效果在哪一层。** 图层效果（如投影、外发光、描边）经常加在代表该元素的**父组**上，内层叶子（智能对象、形状）自身没有效果。选 `layer_id` / `select_ids` 时，检查最近的父组（或代表该元素的组）：组有效果而叶子没有，按第 1 节前的「组效果：烘焙还是引擎绘制」选 `effect_mode`。`baked` 选组 id，可见框用组的含效果框；`engine` 选子层，并写 `engine_effects`。组 id 没有裁切图时同样如此。例如某个图标组 `<item>` 上有 DropShadow、内层叶子没有：`baked` 写该组 id；`engine` 写子层 id + `"effect_mode": "engine"` + 抄自 `effect_params` 的 `engine_effects`。不能只写子层而什么都不记。
7. 底图、高亮、图标、文字始终是独立 `parts`。类型是 `image` | `nine_slice` | `dynamic_text` | `static_text` | `art_text`。可变文案（分数、名字、倒计时、1–2 位名次）用 `dynamic_text`（只出属性，不出图）。固定文案（页签名、导航名、标题等）默认也只出属性：纯色填充，可带描边和/或投影，用 `static_text`，参数写在 `text.layer_effects` / `text.stroke`，由引擎绘制。只有引擎画不了时才用 `art_text` 出图：渐变填充或渐变叠加、斜面浮雕、变形（或非仿射变换）、图案叠加、绸缎、内发光、内阴影、外发光、颜色叠加，以及已栅格化或非文字图层；未知效果同样保守地出图。渐变/图案描边也出图。`dynamic_text`、`static_text`、`art_text` 都不得带九宫边距（不要对文字图做九宫分析）。
8. 重复元素按「重复元素：复用还是分开」处理：逻辑上该复用的导出一张、各实例用 `reuse_of` 引用；与设计对不上的拆开；有证据却对不上的交用户决定。头像图与头像框分开（先自己拆，拆不开才问）；frame 类元素按视觉层次拆成九宫 / 三宫 / 装饰部件。全部 1x。

---

## 4. 命名

规范名：`模块_类型_用途_状态`，**小写**，用下划线。不要直接复制 PSD 图层名。

| 段 | 含义 | 例子 |
|----|------|------|
| 模块 | 功能模块或画板的拉丁/拼音缩写 | `main`、`<module>`、`common` |
| 类型 | 资源种类 | `bg`、`btn`、`icon`、`img`、`tab` |
| 用途 | 这个零件干什么 | `highlight`、`title`、`item_a`、`avatar`、`item3` |
| 状态 | 只给随状态变化的零件加这一段；跨状态共用的零件不加 | `normal`、`selected`、`on`、`off` |

同一组件下，不同状态的同一零件应共用前三段、只改状态段，例如 `main_tab_left_normal` 与 `main_tab_left_selected`。跨状态不变的零件去掉状态段，例如 `common_icon_item_a`，不要写成 `common_icon_item_a_selected`。可选的 `--rules` / [naming_rules.example.json](../examples/naming_rules.example.json) 只提供项目用词习惯（模块词、固定标签等），供 agent 参考，不是自动分类器。

---

## 5. Manifest schema（`psjs-slice-manifest/v2`）

Agent 编写的清单顶层如下。`flatten` 会根据 `components` 生成 `entries`（导出用的扁平列表），不要手写 entries 作为主源。若清单里带上 `layer_tree`（`prepare` 写出的图层树），`flatten` 和 `build-layout` 会按 PSD 自下而上的绘制顺序排列条目；默认状态校验的 src-over 拼图依赖这个顺序，顺序不对会明显拉低 PSNR。

```json
{
  "schema": "psjs-slice-manifest/v2",
  "status": "pending_review",
  "source": {
    "file": "design.psb",
    "doc_size": [1080, 1920],
    "artboard": { "id": 100, "name": "screen", "bounds": [0, 0, 1080, 1920], "size": [1080, 1920] }
  },
  "components": [],
  "missing": [],
  "notes": []
}
```

清单顶层还可有 `reuse_decisions`（交用户决定的复用/拆分问题）：

```json
"reuse_decisions": [
  {
    "id": "rd_row_bg",
    "type": "reuse",
    "instances": ["<module>_bg_row1", "<module>_bg_row2", "<module>_bg_row3"],
    "reuse_of": "<module>_bg_row",
    "evidence": [{ "type": "ui_role_prior", "role": "list_row_bg", "prior": "likely_reuse" }, { "type": "same_name_pattern", "value": "row #" }],
    "problem": "第 3 行底色与前两行不同，复用会与效果图不一致",
    "compare_image": "reuse_compare/reuse_001.png",
    "options": ["reuse", "separate", "reuse_with_variant"],
    "default": "separate",
    "decision": null
  }
]
```

`type` 为 `reuse` 或 `split_avatar`。`split_avatar` 只在 agent 试过拆分、拆不开或拆后对不上时才写，必须带 `attempts`（每项 `method`、`result`、`reason`）和 `compare_image`，`default` 不能是 `accept_placeholder` 等合并导出：

```json
{
  "id": "rd_avatar",
  "type": "split_avatar",
  "instances": ["<module>_img_avatar_item1"],
  "attempts": [
    { "method": "smart_object_contents", "result": "candidate_unverified", "reason": "框的内阴影（正片叠底）落在头像上，按拆分导出后与合并导出对比 25 dB" },
    { "method": "frame_from_instance", "result": "not_available", "reason": "没有只含框的同款层" }
  ],
  "compare_image": "avatar_split/10001/compare.png",
  "problem": "拆开后框的内阴影不再作用于头像",
  "options": ["split_best_effort", "split_with_engine_overlay", "provide_split_frame_asset"],
  "default": "split_best_effort",
  "decision": null
}
```

`decision` 为空时 `flatten` 会在 `pending_reuse_decisions` 里列出；未决定的按 `default` 导出；但 `default` 不能替用户解除 `split_below_threshold` 或复用超上限（见上）。

**1.4.12：所有交用户回答的问题都进 `reuse_decisions`。** 除 `reuse` / `split_avatar` 外，还有三种会改变真值（整图 / 导出内容）的类型，带 `layer_ids`：

| `type` | 问什么 | 常用 `options` | 排除类回答 |
|---|---|---|---|
| `layer_inclusion` | 某个当前可见的图层算不算当前设计（还是旧稿、展示稿） | `include` / `exclude` | `exclude` |
| `exclude_visible_layer` | 同上，问法是「要不要把它排除」 | `keep` / `exclude` | `exclude` |
| `hidden_layer_state` | 某个隐藏层是交替状态还是残留 | `state` / `leftover` | —（隐藏层本来不在整图里） |

**1.4.13：隐藏层的提问和采用。** 吃不准的隐藏层、以及任何要当作某一态资源导出的隐藏层，都写 `hidden_layer_state`：

```json
{
  "id": "state_<layer>",
  "type": "hidden_layer_state",
  "layer_ids": [<hidden_layer_id>],
  "question": "隐藏的 <图层名> 是 <组件> 的另一态，还是旧稿？",
  "crops": ["crops/<visible_layer_id>.png", "crops/<hidden_layer_id>.png"],
  "options": ["state", "leftover"],
  "default": "state",
  "decision": null
}
```

- 用户经 `confirm --decision state_<layer>=state` 选了「作为状态导出」（`state`、`export`、`include`、`use_as_state` 等），导出图才可以用这个层；回答 `leftover` 就不导出。`validate` 的 `hidden_layer_exported_without_decision` 检查这一点（第 9 节）。
- 例外：`role: highlight` 的部件，且同一组件默认态里有打开时可见的底（叠在当前选中项上的发光/气泡），可以不经决定纳入默认选中态。
- 组件 / 状态 / 部件上（或清单顶层）的 `uncertain_hidden` 只是索引；其中每个 layer id 都要被某条阻塞性决定的 `layer_ids` 覆盖，否则 `uncertain_hidden_without_decision`。已经能确定是状态变体或旧稿的，不必挂 `uncertain_hidden`。

其它类型的问题如果答案会改变导出内容，加 `"affects": "truth"`（或 `"export"`、`"full_render"`，也可 `"blocking": true`）。这些条目是**阻塞性**的：

```json
{
  "id": "inc_<layer>",
  "type": "layer_inclusion",
  "layer_ids": [<layer_id>],
  "question": "<图层名>（可见）是当前设计，还是旧稿/展示稿？",
  "evidence": ["与第 1–3 行内容重复", "crops/<layer_id>.png"],
  "options": ["include", "exclude"],
  "default": "include",
  "decision": null
}
```

- 默认值应是安全的一侧：可见层 `include`。`default` 只是建议，导出器不会按它隐藏任何层。
- 用户的回答只能用 `confirm --decision ID=VALUE` 记录：写 `decision`、`decided_by: "user"`、`decided_at`，并把 `{id, decision, decided_at}` 追加进 `manifest.confirmed.sha256` 的 `decision_log`（重新锁定时保留）。阻塞性条目要求当前 `decision` 与日志里该 id 最后一次记录一致；agent 手写的 `decided_by: "user"` 不在日志里，仍算未答（`reason: decided_by_user_not_recorded_by_confirm`）。`reuse` / `split_avatar` 仍按 1.4.9 的规则（`decided_by: "user"`）。
- 排除类回答（`exclude`、`omit`、`old_draft`、`leftover` 等）只在 `decided_by: "user"` 且在 `decision_log` 里时生效：导出器在切图和整图里隐藏这些 `layer_ids`，`validate` 不再把它们算作 `visible_layer_hidden` / `visible_layer_unassigned`。
- `flatten` 输出新增 `pending_user_decisions`（全部未答条目，带 `blocking` 和 `reason`）；1.4.13 起还有 `uncertain_hidden_without_decision`（没有对应决定的 `uncertain_hidden`）。

组件节点常用字段：

| 字段 | 含义 |
|------|------|
| `id` | 稳定 id，用于布局和状态预览文件名，如 `main_tab_group` |
| `type` | `screen`、`tab_group`、`tab`、`navbar`、`nav_item`、`list_item`、`button` 等 |
| `name` | 规范名或语义名 |
| `default_state` | PSD **当前显示**的状态名 |
| `shared`（亦作 `common_parts` / `parts`） | 跨状态不变的部件（图标、始终存在的底等）。规范名不加状态后缀 |
| `states.<state>.parts` | 只放随该状态变化的部件（底/槽的明暗、选中高亮、仅某态出现的标签） |
| `states.<state>.from_hidden` | 这些部件来自隐藏层时为 `true` |
| `missing` | **仅**用于按设计会随状态变化、但 PSD 里没有合格变体的部件。写成 `{ "state", "role"?, "reason"? }` 或状态名字符串。不要把共用零件的「没有另一态」写成 missing |
| `uncertain_hidden` | 可选的**索引**（对象或列表，带 `layer_ids`、`crops`、`question`），方便审核时对照；它本身不算提问。其中每个 layer id 都必须有一条覆盖它的阻塞性 `reuse_decisions` 条目（通常是 `hidden_layer_state`），否则 `validate` 报 `uncertain_hidden_without_decision`。不要在这里擅自选用其中一侧 |
| `ignored_hidden` | 已认定为无关或旧稿而忽略的隐藏层（`layer_id` + `reason`），供审核对照 |
| `children` | 子组件 |
| `cumulative`（亦作 `ordinal`） | `true` 表示子组件是有序累积的格子（评分星、进度点、等级点）：第 N 档 = 前 N 个成员处于 `cumulative_state`，其余处于 `off_state`。`build-layout` 展开成 `expanded_states.level_<N>.activation`（含 `level_0`），`validate` 按整档出预览，不再单独预览某一格 |
| `members` | 可选，累积组的成员顺序（子组件 id）；默认按 `children` 顺序 |
| `cumulative_state` / `off_state` | 可选，成员「亮」的状态名（默认取每个成员都声明了的那唯一一个状态）和「灭」的状态名（默认取某个成员不等于亮态的 `default_state`，再没有就是 `off`） |
| `states.<state>.activation` | 可选，这个状态的完整组合 `{组件 id: 状态名}`；预览按它整体切换。累积组里漏掉更低的格子会报 `state_activation_incomplete` |
| `source_path` / `layer_id` | 仅供对照的源图层引用 |

累积组示例（虚构；默认亮 3 格，第 4、5 格的亮星来自隐藏层，已由用户经 `confirm --decision` 答为状态）：

```json
{
  "id": "<module>_star_row",
  "type": "rating",
  "cumulative": true,
  "cumulative_state": "filled",
  "off_state": "empty",
  "children": [
    { "id": "<module>_star_slot1", "type": "rating_slot", "default_state": "filled",
      "shared": [{ "name": "<module>_icon_star_empty_slot1", "role": "base", "reuse_of": "<module>_icon_star_empty" }],
      "states": { "filled": { "parts": [{ "name": "<module>_icon_star_full_slot1", "role": "highlight", "reuse_of": "<module>_icon_star_full" }] } } },
    { "id": "<module>_star_slot4", "type": "rating_slot", "default_state": "empty",
      "shared": [{ "name": "<module>_icon_star_empty_slot4", "role": "base", "reuse_of": "<module>_icon_star_empty" }],
      "states": { "filled": { "from_hidden": true, "parts": [{ "name": "<module>_icon_star_full_slot4", "role": "highlight", "reuse_of": "<module>_icon_star_full" }] } } }
  ]
}
```

部件（`part`）常用字段：`name`（规范文件名）、`role`（`base`、`highlight`、`icon`、`text` 等）、`kind`、`layer_id`、`select_ids`、`hidden`、`nine_slice`（`{left, top, right, bottom}`，确认后原样进布局；留 `null` 或某项为 `null` 表示交给检测补）、`text`、`logical_bounds`、`source_name`、`source_path`。重复元素复用时加 `reuse_of`（共享图名，不带实例序号）；（`reuse_accepted: true` 只是说明，不能绕过复用误差上限；接受差异必须是用户在 `reuse_decisions` 里填的 `decision`。）运行时内容的占位图（如头像）加 `placeholder: true`。拆分出来的部件加 `split_method`（`smart_object_contents` | `mask_or_clip_layers` | `frame_from_instance` | `pixel_mask_estimate`）、`split_evidence`（对比结果和对比图），智能对象拆分再加 `split: { "hide_inner": [{ "id": 内层id, "name": 内层名 }] }`（导出时在临时副本里隐藏这些内层；先按 id、再按唯一的名字匹配，找不到就报错），像素估算加 `split_approximate: true`。frame 类元素仍整张烘焙时加 `bake_reason`。父组带效果时再加：`effect_mode`（`"baked"` | `"engine"`）、`effect_reason`（一句话理由，审核时可见）、`engine_effects`（仅 `engine`，从 `effect_params` 抄）。

```json
{
  "name": "<module>_icon_<item>",
  "kind": "image",
  "layer_id": 1000,
  "select_ids": [1001, 1002],
  "effect_mode": "engine",
  "effect_reason": "简单投影，引擎组件可复现；多个实例共用一张图",
  "engine_effects": [
    { "type": "DropShadow", "blend_mode": "multiply", "color": "#000000", "opacity": 0.5,
      "angle": 90, "distance": 4, "size": 8, "spread": 0 }
  ]
}
```

`dynamic_text` 和 `static_text` 的 `text` 在导出时由 Photoshop 补全（内容、字体 PostScript 名、字号、颜色、描边、对齐、`click_point` 为相对整份文档的百分比；从 1.3.0 起还有样式片段、变换缩放后的像素字号和行距、字距方式、抗锯齿方式、图层效果及其开关，字段见第 8 节「属性文字」）。`static_text` 的描边和投影也在这些字段里，供引擎绘制。

### 5.1 示例：双页签开关 + 底栏导航

下面是一份**虚构**界面，不要把它当成某个项目的图层名。屏幕当前显示：左侧页签选中、底栏第一项选中。隐藏层里有右侧页签的选中文案、左侧页签的未选中文案，以及第一项的未选中底。右侧页签没有单独的未选中高亮（高亮是 group 上的共享资源）。导航图标在两种状态下不变，放在各 `nav_item` 的 `shared` 里。以第一项的 selected（base + highlight + label）为模板，第二项缺少的 selected 底、高亮和标签都记入 `missing`，不要把共用图标标成缺失。

```json
{
  "schema": "psjs-slice-manifest/v2",
  "status": "pending_review",
  "components": [
    {
      "id": "main_screen",
      "type": "screen",
      "name": "main_screen",
      "children": [
        {
          "id": "main_tab_group",
          "type": "tab_group",
          "name": "main_tab_group",
          "default_state": "left",
          "parts": [
            {
              "name": "main_tab_highlight_selected",
              "role": "highlight",
              "kind": "nine_slice",
              "layer_id": 201,
              "select_ids": [201],
              "hidden": false,
              "source_name": "tab highlight",
              "source_path": "tabs/highlight"
            }
          ],
          "children": [
            {
              "id": "main_tab_left",
              "type": "tab",
              "name": "main_tab_left",
              "default_state": "selected",
              "states": {
                "selected": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_tab_left_selected",
                      "role": "text",
                      "kind": "static_text",
                      "layer_id": 211,
                      "hidden": false,
                      "source_name": "Left on"
                    }
                  ]
                },
                "normal": {
                  "from_hidden": true,
                  "parts": [
                    {
                      "name": "main_tab_left_normal",
                      "role": "text",
                      "kind": "static_text",
                      "layer_id": 212,
                      "hidden": true,
                      "source_name": "Left off"
                    }
                  ]
                }
              },
              "missing": []
            },
            {
              "id": "main_tab_right",
              "type": "tab",
              "name": "main_tab_right",
              "default_state": "normal",
              "states": {
                "normal": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_tab_right_normal",
                      "role": "text",
                      "kind": "static_text",
                      "layer_id": 221,
                      "hidden": false,
                      "source_name": "Right off"
                    }
                  ]
                },
                "selected": {
                  "from_hidden": true,
                  "parts": [
                    {
                      "name": "main_tab_right_selected",
                      "role": "text",
                      "kind": "static_text",
                      "layer_id": 222,
                      "hidden": true,
                      "source_name": "Right on"
                    }
                  ]
                }
              },
              "missing": []
            }
          ]
        },
        {
          "id": "main_navbar",
          "type": "navbar",
          "name": "main_navbar",
          "default_state": "item_a",
          "shared": [
            {
              "name": "main_bg_navbar",
              "role": "base",
              "kind": "nine_slice",
              "layer_id": 301,
              "hidden": false,
              "source_name": "nav bg"
            }
          ],
          "children": [
            {
              "id": "main_nav_item_a",
              "type": "nav_item",
              "name": "main_nav_item_a",
              "default_state": "selected",
              "shared": [
                {
                  "name": "main_icon_item_a",
                  "role": "icon",
                  "kind": "image",
                  "layer_id": 311,
                  "hidden": false
                }
              ],
              "states": {
                "selected": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_bg_nav_item_a_selected",
                      "role": "base",
                      "kind": "nine_slice",
                      "layer_id": 310,
                      "hidden": false
                    },
                    {
                      "name": "main_img_item_a_selected",
                      "role": "text",
                      "kind": "static_text",
                      "layer_id": 312,
                      "hidden": false
                    },
                    {
                      "name": "main_nav_item_a_highlight_selected",
                      "role": "highlight",
                      "kind": "nine_slice",
                      "layer_id": 314,
                      "hidden": true
                    }
                  ]
                },
                "normal": {
                  "from_hidden": true,
                  "parts": [
                    {
                      "name": "main_bg_nav_item_a_normal",
                      "role": "base",
                      "kind": "nine_slice",
                      "layer_id": 313,
                      "hidden": true
                    }
                  ]
                }
              },
              "missing": []
            },
            {
              "id": "main_nav_item_b",
              "type": "nav_item",
              "name": "main_nav_item_b",
              "default_state": "normal",
              "shared": [
                {
                  "name": "main_icon_item_b",
                  "role": "icon",
                  "kind": "image",
                  "layer_id": 321,
                  "hidden": false
                }
              ],
              "states": {
                "normal": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_bg_nav_item_b_normal",
                      "role": "base",
                      "kind": "image",
                      "layer_id": 322,
                      "hidden": false
                    }
                  ]
                }
              },
              "missing": [
                {
                  "state": "selected",
                  "role": "base",
                  "reason": "The most complete selected sibling has base + highlight + label; this item has no selected base drawn."
                },
                {
                  "state": "selected",
                  "role": "highlight",
                  "reason": "The most complete selected sibling has a highlight; this item has none drawn."
                },
                {
                  "state": "selected",
                  "role": "text",
                  "reason": "The most complete selected sibling has a label; this item has none drawn."
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "missing": []
}
```

要点：页签和导航上的固定文案是 `static_text`（示例里只有描边/投影能画的效果，只出属性）。若标题带渐变、斜面浮雕或变形，才用 `art_text` 出图。文字部件不要写 `nine_slice`。高亮是 `tab_group` 上的共享部件，没有和某个页签的文案烤在一起。导航图标若在各状态下不变，放在该 `nav_item` 的 `shared` 里，文件名不加 `_selected` / `_normal`。`missing` 只记录按设计会变却没画出来的零件（例如某项没有 selected 底或高亮），不要因为「没有另一态的图标」去标共用图标。忽略旧稿不会让共用图标变成 missing。`flatten` 之后，隐藏的状态部件会带 `hidden: true`，导出脚本会临时显示再恢复；共用部件会出现在每一个状态的预览里。

---

## 6. 审核门闩

确认前不要导出。

1. 用 `flatten` 把组件树展成 `entries`，再用 `preview` 画标注预览（框出每个切图、标规范名和九宫格线；来自隐藏态的条目会计入标题；有 `effect_mode` 的标 `fx:baked` / `fx:engine(效果类型)`，`engine` 再用细品红框画出估计的效果范围）。`flatten` 输出里的 `effect_issues` 必须为空。
2. 把 `preview_annotated.png` 和 `manifest.json` 交给用户。用户改 JSON：改名、改类型、改九宫格、拆/合并部件、确认或删除 `missing`、把不该导出的隐藏层拿掉、切换 `effect_mode`、对 `reuse_decisions` 填 `decision`（看对比图）。隐藏层的问题（含 `uncertain_hidden` 里列的）要在 `reuse_decisions` 里有 `hidden_layer_state` 条目，用户的回答用 `confirm --decision` 记录。
3. `status` 在用户确认前保持 `pending_review`。用户确认后运行 `python slicing_helper.py confirm --manifest manifest.json`：改为 `confirmed`、写 `confirmed_at`，并写出锁 `manifest.confirmed.json`（逐字节副本）和 `manifest.confirmed.sha256`。用户对某条 `reuse_decisions` 的回答用 `--decision ID=VALUE`（可重复）记进去，会写 `decided_by: "user"` 和 `decided_at`；`confirm` 输出的 `not_user_decided` 列出**所有**还没有用户回答的条目（任何 `type`，带 `blocking` 和 `reason`），`blocking_not_user_decided` 单列阻塞性的——卡着低于阈值的拆分、超上限复用或阻塞性问题的，要先问用户再导出。**一句「清单确认」（`confirm --note "清单确认"`）只确认切图清单本身**：照常锁定，但不回答任何一条决定；agent 不能把它解读成用户选了某个选项（例如「隐藏某个可见层」）。要回答就单独问，并用 `--decision` 记录。导出脚本拒绝其它状态（测试授权下允许 `pending_review`）。
4. 用户确认后才进入导出。**锁定后的清单不得再改**（包括加条目、改 `reuse_accepted`、改 `split_evidence`、跑会重写清单的 `flatten`）。`export_slices.psjs` 发现 manifest 和 `manifest.confirmed.json` 不一致就拒绝导出；`build-layout` 发现哈希不一致就退出（退出码 3，不写布局）；`validate` 记 `manifest_changed_after_confirm`；没锁记 `manifest_not_locked`。导出前可以先跑 `check-lock`（改过退出 3，没锁退出 4）。
5. 导出或校验中发现清单需要改：把改动写进 `manifest.proposal.json`（改什么、为什么、证据和对比图），**停下**交用户；用户同意后再改清单，并用 `confirm --relock` 重新锁定。

---

## 7. 导出

[examples/export_slices.psjs](../examples/export_slices.psjs) 只硬编码一个 `CONFIG_URL`，从作业 JSON 读路径（见 [export_config.example.json](../examples/export_config.example.json)）。按 `layer_id`（及 `select_ids` 里的剪贴层）导出 1x PNG，图层效果烘焙进图；带 `reuse_of` 的实例只导出一张共享图（见下）。`dynamic_text` 和 `static_text` 不出图，只写文本属性（含描边、投影）；`art_text` 出图。

带 `reuse_of` 的条目由 `flatten` 分配：源实例（名称等于共享名的那条，否则第一条默认态实例）导出 `<共享名>.png`（`image_name`），其余实例 `export_png: false`，脚本只记录它们的边界，不再出图。

`select_ids` 有多个 id 时，脚本把它们一起选中（`addToSelection`）、「从图层新建」、`mergeVisible` 后 trim 导出。`engine` 部件就靠这条路径：选的是子层，新文档里没有父组，所以组上的效果、组不透明度和混合模式都不会带进 PNG，子层自己的效果仍在。PNG 原点照样用 trim 内缩量计算；`layer_id` 留组 id 时，日志里的 `logical_bounds` 是组的 `boundsNoEffects`。

若条目 `hidden`（或 `from_hidden`）为真，脚本先显示该层及其被隐藏的祖先，再「从图层新建」、trim、保存，然后把可见性改回去。整次作业在 `suspendHistory` 里进行；脚本自己打开的文档一律 `closeWithoutSaving`。每条描述符 `_options.dialogOptions = "silent"`。只操作作业打开的文档，绝不改、存、关用户已打开的其它文档。

可见框记录规则不变：临时文档与源画布对齐时，紧致可见原点 = trim 前 `layer.bounds.left/top` 减 trim 后的 left/top。不要把 trim 前的松框左上角当成 PNG 原点。

**量边界用像素，不用实时效果框。** 单个带图层效果的图层，Photoshop 报的效果边界是取整过的，trim 内缩算出的原点可能差 1 px。脚本先给临时副本加一个空图层再合并可见图层，在像素层上量边界（日志 `measured_on_raster: true`）。16 位文档在量边界 / trim 之前先转 8 位，使内缩量与导出 PNG 一致。`boundsNoEffects` 才是不含效果的逻辑框；不要把 DOM / 描述符里的 `bounds` 直接当成「含效果可见框」。对多画板文档做「从图层新建」时，新文档可能仍是整份画布，`doc.width` 也可能仍是整布尺寸，即使 `saveAs.png` 按当前画板写出了目标尺寸；模板会尝试按 `artboard.bounds` 裁切，校验以实际 PNG 尺寸为准。

**1.4.12 的导出记录（`validate` 据此检查真值，见第 9 节）：**
- **导出器原样使用。** 把 skill 的 `examples/export_slices.psjs` 复制到作业目录，只改 `CONFIG_URL`。按字节复制：Python `shutil.copy2`，或按字节读写（`read_bytes()` → 替换 `CONFIG_URL` 那一行 → `write_bytes()`）；PowerShell `Copy-Item`；`cmd /c copy`。按文本读写会改写文件：PowerShell 5 的 `Get-Content` / `Set-Content` 用默认编码改写非 ASCII 注释（`exporter_modified`）；Windows 上 Python 的文本模式（`write_text`、`open(..., "w")`）把 LF 全改成 CRLF。一定要用文本模式时，写入时传 `newline=""` 并用 UTF-8。作业 JSON 加 `exporter_url` 指向这份副本。脚本读自己的文本，统一换行、去掉 BOM、把 `CONFIG_URL` 那一行置空后算 sha256，记在 `exporter`（`release`、`script_url`、`script_path`、`sha256`；读不到时 `sha256: null` 加 `error`）。不要为了隐藏图层、改量法或消掉某个误报去改脚本；误报写进报告交给维护者。
- **打开时的可见性。** 打开（或复用已打开的）源文档后、任何临时显隐之前，记 `layers_at_open`：每个图层的 `id`、`name`、`parent_id`、`kind`、`visible`（自身）、`effective_visible`（含祖先）、`opacity`、`bounds`（文档坐标，含效果）、`group`。1.4.13 起同时记 `layers_at_open_ms`（快照耗时，毫秒）和 `layers_at_open_count`；图层很多的文档上一次快照可能要几秒，属正常，不要为了省时间改采样方式。
- **排除只由用户决定驱动。** 脚本读 `manifest.confirmed.sha256` 的 `decision_log`，只隐藏阻塞性 / 排除类条目里用户经 `confirm --decision` 选了排除的 `layer_ids`（记 `excluded_by_user_decision`），整图导完后恢复；没有任何别的隐藏开关。未答的阻塞性条目记在 `pending_user_decisions`，不做任何显隐改动。
- **未答的隐藏层不出图。** 条目的 `layer_id` / `select_ids` 里有打开时隐藏的层、且被未答的阻塞性决定覆盖时，脚本不显示它、不出 PNG，只记边界：条目上记 `skipped_pending_decision`（`layer_ids`、`decision_ids`），日志顶层 `skipped_pending_decision` 列出全部。用户回答后重新导出。
- **出图时间。** 每张导出的 PNG 在条目上记 `exported_at`（ISO 时间，UTC）；`validate` 用它和决定的回答时间比较（第 9 节 `exported_before_decision`）。
- **整图前再记一次。** 导 `full_render.png` 前记 `full_render.hidden_at_render`：打开时可见、此刻被隐藏的图层（1.4.13 起耗时记在 `full_render.hidden_at_render_ms`）。导隐藏状态时的临时显示、拆分时在临时副本里隐藏内层，都必须在整图前恢复，不会出现在这里。
- **多图层条目。** `select_ids` 多于一层时，逐层读边界记 `select_bounds`（`id`、`logical`、`visible`），并记逻辑框并集 `logical_bounds_union`；`logical_bounds` 仍是 `layer_id` 那一层。

---

## 8. 布局 JSON schema（`psjs-layout/v2`）

层级按**组件语义**重组，例如 `screen > tab_group > tab > state > part`。源图层的 `layer_id`、`source_name`、`source_path` 只作对照，不是树的结构依据。

顶层：

```json
{
  "schema": "psjs-layout/v2",
  "source": { "artboard": { "id": 100, "name": "screen", "bounds": [0, 0, 1080, 1920], "size": [1080, 1920] } },
  "components": [],
  "nodes": [],
  "missing": [],
  "notes": []
}
```

- `components`：与 manifest 相同的语义树，但每个 part 已补上导出后的 `image`、`visible_bounds`、`logical_bounds`、`nine_slice`、文本属性。
- `nodes`：扁平列表，便于引擎遍历和校验放置。每条含 `name`、`kind`、`component_id`、`part_role`、`state`、`default`、`hidden`、`visible_bounds`（含烘焙效果，画板坐标）、`logical_bounds`（不含效果）、`anchor`（逻辑框中心）、`pivot`（0.5, 0.5）、`nine_slice`、`image`、`text`。`dynamic_text` / `static_text` 的 `image` 和 `nine_slice` 均为 null；`art_text` 出图但 `nine_slice` 也必须为 null。`validate` 若发现文字条目带九宫边距，记 `text_has_nine_slice`。有 `effect_mode` 时附 `effect_mode`、`effect_reason`；`engine` 还附 `engine_effects` 和 `effect_bounds`（逻辑框按各效果的距离/角度/大小外扩的**估计**范围，`effect_bounds_estimated: true`，不是 PS 的精确框），此时 `logical_bounds` = `visible_bounds` = 图片框。`components` 里对应的 part 也带这些字段。
- 九宫：`kind` 和 `nine_slice` 取自确认后的清单（第 1 节前「frame 类元素」）；`nine_slice_source` 为 `manifest`、`auto_detected` 或 `manifest+auto_detected`，补过的项在 `nine_slice_auto_filled`；`nine_slice_detected` 只是检测建议，引擎不用。
- 导出跳过：`export_skipped: "pending_decision"` 的节点没有 PNG（决定未答），`skipped_pending_decision` 记原因；`exported_at` 抄自导出日志。
- 状态：累积组带 `cumulative_expanded`（`members`、`on_state`、`off_state`、`default_level`）和 `expanded_states.level_<N>.activation`，引擎按整档切换。
- 复用：带 `reuse_of` 的节点 `image` 都是 `<共享名>.png`，九宫格取源实例；只记边界的实例带 `bounds_only: true`；非源实例的 `visible_bounds` = 源实例可见框按两者逻辑框原点的差平移。顶层附 `reuse_decisions`。
- `blend_mode`：part 的混合模式原样带出。两张拆法的框是 `normal`。`split_fix` 记修法（`two_part_approx` / `frame_from_merged` / `overlay_part`）。
- `effect_groups` / `effect_issues`：参与检查的带效果组，以及组效果被悄悄丢掉的部件（应为空）。
- 坐标是画板像素（1x）。各引擎导入器自己写，不在本 skill 里。

### 属性文字（`dynamic_text` / `static_text` 的 `text`，`text_attrs_version: 2`）

`dynamic_text`（运行时可变文案）和 `static_text`（引擎可绘制的固定标签）共用这套字段；描边和投影写在 `layer_effects` 以及兼容字段 `stroke` 里，由引擎按同一份布局绘制。`export_slices.psjs` 的 `parseText()` 从图层描述符写出，`build-layout` 原样带入并补派生字段（`text_render.complete_text_attrs()`；只加不删）。Python 侧 `text_render.text_attrs_from_descriptor()` 与它逐字段一致，可用于已持有 batchPlay 描述符的流程。

单位约定：**px** 是画板像素（已乘文字图层变换），**pt** 是 Photoshop 文字空间的点（未乘变换）。文字图层常带缩放变换，例如 29.74 pt × 1.345 = 40 px，所以字号和行距都必须乘变换，不能直接把 pt 当像素用。

| 字段 | 含义 |
| --- | --- |
| `content` | 文本，换行为 `\r`（PS 原样） |
| `font` / `size` / `color` / `alignment` / `tracking` | 第一个样式片段的值；`size` 为 px（v1 起就是乘过 y 缩放的值） |
| `size_pt` | 第一个片段的 pt 字号 |
| `leading` / `leading_px` / `auto_leading` | 第一个片段的行距：`leading` 为 **pt**（与 v1 含义相同，未乘变换），`leading_px` 为 px；`auto_leading: true` 时两者为 null |
| `transform` / `scale` | 变换矩阵 `xx, xy, yx, yy, tx, ty`；`scale.x/y` 为矩阵两轴的缩放 |
| `click_point` | 文字锚点（点文字为首行基线起点），相对整份文档的百分比 |
| `runs[]` | 样式片段：`from`、`to`（字符下标，含换行）、`font`、`size_pt`、`size_px`、`color`、`tracking`（1/1000 em）、`auto_leading`、`leading_pt`、`leading_px`、`baseline_shift_pt`、`faux_bold`、`faux_italic`、`horizontal_scale`、`vertical_scale`、`auto_kern` |
| `paragraphs[]` | 段落片段：`from`、`to`、`align`、`auto_leading_pct` |
| `kerning` | `manual` / `metrics` / `optical`；片段不一致时为 `mixed` |
| `manual_kerning_ranges` | 手动字距对的数量（字距对本身不导出） |
| `anti_alias` | PS 抗锯齿方式，如 `antiAliasPlatformGray`、`antiAliasSharp`、`antiAliasCrisp`、`antiAliasStrong`、`antiAliasSmooth` |
| `warp` / `shape` / `box_bounds_pt` | 变形样式（无变形为 null）；`point` 点文字或 `box` 段落文字及其文字框（pt） |
| `opacity` / `fill_opacity` | 图层不透明度与填充不透明度（0–1） |
| `effects_visible` | 图层效果总开关（PS 的 `layerFXVisible`）；false 时所有效果都不画 |
| `layer_effects[]` | 文字图层上**所有存在的**效果，各带 `enabled`；类型名与 `engine_effects` 相同（`Stroke` 带 `size`、`position` outside/inside/center、`fill_type`；`DropShadow` 带 `size`、`distance`、`spread`、已解析全局光的 `angle`；`OuterGlow` 带 `size`、`spread`） |
| `stroke` | 兼容字段：仅在 `effects_visible` 且描边启用时给出，否则为 null（1.2.0 及以前不看总开关） |

旧版导出日志（无 `text_attrs_version`，即 v1）仍可用：只有一个样式片段，`leading` 按 pt 乘 y 缩放，描边按 `stroke` 字段。`validate` 报告的 `text_renderer.legacy_v1_text_attrs` 给出这类节点数；想要更接近的文字预览，用新版 `export_slices.psjs` 重新导出。

`tab_group` / `navbar` 默认视为互斥：预览某个子项的 `selected` 时，兄弟项改用 `normal`（或 `off`）。

---

## 9. 校验

真值与交付护栏（都进 `issues`，`ok:false`；数据来自 `build-layout` 抄进布局 `export` 段的导出记录和 `manifest.confirmed.json`）：
- `pending_user_decision`：阻塞性的 `reuse_decisions` 条目（`layer_inclusion` / `exclude_visible_layer` / `hidden_layer_state`，或带 `affects: truth|export|full_render` / `blocking: true`）没有经 `confirm --decision` 记录的用户回答。带 `id`、`decision_type`、`question`、`layer_ids`、`reason`（`no_decision`、`decision_pending`、`not_decided_by_user`、`decided_by_user_not_recorded_by_confirm`）。`build-layout` 也会警告。
- `visible_layer_hidden`：`full_render.hidden_at_render` 里有打开时可见、却没有被用户排除（自身或祖先在已记录的排除决定里）的图层。此时参考图已被改过，分数不可信。
- `visible_layer_unassigned`：`layers_at_open` 里画板内（祖先链含画板 id，且边界与画板相交）、有效可见、不透明度 > 0、边界非空的像素类图层（像素、文字、智能对象、填充/形状层；调整层和组不算），既没被任何节点的 `layer_id` / `select_ids` 覆盖（被选中的祖先组也算覆盖），也没被用户排除。最多列 50 个，带 `count`。
- `uncertain_hidden_without_decision`（1.4.13）：清单 JSON 里组件、状态、部件或顶层的 `uncertain_hidden`（对象或列表，取 `layer_ids` / `layer_id`）中，有 layer id 没被任何阻塞性 `reuse_decisions` 条目的 `layer_ids` 覆盖。带 `layer_ids`（未覆盖的那些）、`component_id`、`state`、`part`、`question`。已覆盖但未答的只报 `pending_user_decision`，不重复报。不解析审核 markdown。`flatten` 摘要里提前列出。
- `hidden_layer_exported_without_decision`（1.4.13）：导出 PNG 的节点（不含属性文字、只记边界的复用实例和导出器因决定未答而跳过的节点）的 `layer_id` / `select_ids` 里，有 `layers_at_open` 中打开时隐藏（自身 `visible: false` 或 `effective_visible: false`）的层，而覆盖它的阻塞性决定没有用户经 `confirm --decision` 记录的「作为状态导出」类回答。`entries` 里每条带 `name`、`layer_id`、`component_id`、`state`、`part_role`、`decision_ids`、`reason`（`no_decision` / `user_answer_is_not_export_as_state`）。覆盖它的决定还没答的交给 `pending_user_decision`。例外：`highlight` 部件且同一组件默认态（`state` 为空或等于 `default_state`）里有打开时可见的非高亮部件。没有 `layers_at_open` 的旧日志不查这条（`exporter_modified` 照旧报缺记录）。
- `exported_before_decision`：PNG 用到的图层被一条已答的阻塞性决定覆盖，而这张 PNG 的导出时间（节点 `exported_at`，没有就用日志 `started`）早于该决定拿到当前回答的时间（`decision_log` 里这次回答首次记录的 `decided_at`；重新锁定时重复同一回答不改变它）。`reason`：`png_older_than_decision`；`skipped_while_pending`（导出时决定未答被跳过，现在答成「作为状态导出」）；`full_render_older_than_decision`（整图早于用户记录的排除）。只记边界的复用实例不算（它画的是源实例的图）。没有时间的旧日志不判。处理：重新导出 → `build-layout` → `validate`。
- `nine_slice_changed_after_confirm`：布局节点的 `kind` 或九宫边距与 `manifest.confirmed.json`（`manifest_lock.manifest` 旁边）里同名条目不一致（`reason`：`kind_changed` / `margins_changed`，带两边的值）。清单留空、由检测补上且节点记了 `nine_slice_source` 含 `auto_detected` 的项不算。`nine_slice_margins_missing`：`kind: nine_slice` 却没有边距。
- `state_activation_incomplete`：声明了 `cumulative` 的组里，某个带显式 `activation` 的状态让较高的成员亮、却让更低的成员停在默认或灭的状态（带 `group`、`state`、`lower_members_off`、`activation`）。没声明的组不判。
- `exporter_modified`：导出日志没有 `exporter` 记录或 `sha256`、`sha256` 与 skill 自带的 `examples/export_slices.psjs`（`validate --shipped-exporter` 可改路径，默认在 `slicing_helper.py` 旁边）按同样规则算出的值不一致、`script_path` 上的文件现算不一致，或没有 `layers_at_open`。`reasons` 列出原因。这只防「复制后改两行」，不是防篡改；导出器和助手要用同一个 skill 版本。

1.4.9 起的护栏（都进 `issues`，不能由 agent 自行标记接受）：`manifest_changed_after_confirm` / `manifest_not_locked`（第 6 节）；`split_below_threshold`（实测：导出的拆分部件拼回去，在其合并框内与 PS 整图比 dB，见 `split_checked`；或 `split_evidence` 里实测 dB 低于阈值）、`split_unverified`；`reuse_mismatch`（复用误差超上限，见「重复元素」）。用户在 `reuse_decisions` 填了 `decision` 的，改记在 `split_below_threshold_user_accepted` / `reuse_mismatch_accepted`。

1. **默认状态**：只拼 `default: true` 的部件（即 PSD 里当前显示的那一态，加上始终可见的 chrome），用 src-over（`Image.alpha_composite`）叠回画板，与 Photoshop 整图比像素差 / CIEDE2000 ΔE。同时报告画上属性文字（`dynamic_text` / `static_text`）近似后的数字（`diff_text_rendered`）、以及遮罩这些文字区域后的数字（`diff_text_masked`），并列出误差最大的 10 条。再检查漏切、重名、空图。**判断切图精度只看遮罩后的指标**；带文字的数字只说明预览和效果图有多像。
2. **引擎效果**：`effect_mode: engine` 的部件，投影 / 外发光按 Pillow 近似画在部件下面（alpha → 按角度/距离偏移 → 扩展后高斯模糊 → 颜色/不透明度，普通混合），报告列在 `engine_effects_emulated`。描边、内阴影、叠加等无法模拟的，按估计的 `effect_bounds` 把外圈（内部效果则整框）从遮罩指标里去掉，列在 `engine_effects_unemulated`。`issues` 里的 `group_effect_lost` 表示选了带效果组的子层却既没烘焙也没写 `engine_effects`，必须修。部件带 `blend_mode` 时按对应公式叠到画布上（normal / multiply / screen / linear_burn / lighten / darken / linear_dodge；其它模式按 normal 画并在 `placed` 里记 `blend_mode_approximated_normal`）。拆分相关的 `outer_effect_into_hole`、`blend_mode_content_dependent`、`blend_mode_not_replayable` 见「拆开后拼回去对不上」。
3. **复用实例**：每个带 `reuse_of` 的默认态节点，在其可见框内（去掉属性文字与无法模拟的效果区域）比 PS 整图与拼图。MAE > `--reuse-tol-mae`（默认 4）或差值 >16 的像素占比 > `--reuse-tol-pct`（默认 1%）时记为 `reuse_mismatch`（含实例名、区域、指标和 `validate/reuse/<名>.png` 对比图）；`reuse_accepted` 的改记 `reuse_mismatch_accepted`。逐实例数据在 `reuse_checked`。
4. **位置（1.4.11）**：`layer_bounds_mismatch`：清单的 `logical_bounds` 与该条目 `layer_id` 实测的逻辑边界差超过 2 px（指向了兄弟实例的图层）；借用别的实例图的非默认态改按清单的框放，不报。1.4.12 起，多图层条目（`select_ids` 多于一层）用日志里的 `logical_bounds_union`（或 `select_bounds` 的并集）比；旧日志只有 `layer_id` 那一层时，实测框落在清单框内（容差 2 px）算并集的一部分、不报，框在别处照报。`placement_offset`：每张正常混合、100% 不透明的图（含按原尺寸画的九宫格），用它自己的不透明像素、去掉上层盖住的部分，在 ±2 px 内和 PS 整图配准；某个偏移的 MAE 不到原位的一半且低 0.5 以上就报，附 `fits_at_shift`。处理办法是按当前 `export_slices.psjs` 重导或改正 `layer_id`，不要手动挪布局。参与配准的图数在 `placement_checked`。
4. **交替状态**：对每个交互组件的非默认状态（含仅存在于 `missing` 中的状态），按「共用部件 + 该状态部件」拼图，互斥组的兄弟项切到对应态，写出 `validate/states/<component_id>_<state>.png`。状态带 `activation` 时按它整体切换；累积组按整档预览（`<组 id>_level_<N>.png`，默认档除外），成员自己的亮 / 灭态不再单独预览。只对真正缺失的、按设计随状态变化的零件画红框并标 `missing`。这些预览**不**和默认态整图比 PSNR（对不上是正常的）。
5. **导出后标注预览（最终交付）**：用实际导出的 PNG 按布局 JSON 拼默认态（同上，含引擎效果近似），再按审核预览的样式画标注（标签字体见下）：每个切图按 `visible_bounds` 画框、规范名、九宫格线、类型和 `fx:baked` / `fx:engine`，复用实例的标签后缀为 `→ <共享名>`。写出 `validate/preview_exported_annotated.png` 和 `validate/preview_exported_vs_ps.png`（左 PS 整图、右导出拼图+标注），路径记在报告的 `exported_preview`。把它们交给用户。

   **标签字体。** 画板名和切图名可能是中文。标注用的字体按顺序找：`--label-font <字体文件>`（`preview` / `validate`）或环境变量 `PSJS_LABEL_FONT`，再找系统里的中日韩字体（Microsoft YaHei、PingFang、Noto Sans CJK、Source Han Sans、WenQuanYi 等）。找不到时照样画（中文会显示成方框），报告的 `label_font` 带 `warning`，stderr 也提示；标签只是标注，不影响校验结果。

6. **属性文字预览**（`dynamic_text` / `static_text`，[examples/text_render.py](../examples/text_render.py)）：只是近似，不是 Photoshop 的文字引擎，也不是目标引擎的渲染。投影按规则画出，总开关或单项关闭的效果不画；没有 Pillow 回退。要像素一致的文字，让 Photoshop 栅格化文字图层（或临时显示后导出带字整图）做底图；要和上线一致，用目标引擎按同一份布局 JSON 出图。

   - `--text-renderer auto`（默认）：装了 `uharfbuzz` + `freetype-py` 用 `freetype`（HarfBuzz 排版，FreeType 不做 hinting、直接填充轮廓，描边用圆角外扩的轮廓描边）；否则有 `skia-python` 用 `skia`（Skia 轮廓路径）；也可显式指定 `freetype` 或 `skia`。所需的包缺了就报错退出，不会换渲染器，也没有 Pillow 文字路径。报告 `text_renderer` 记 `requested`、`used`、参数，以及旧格式导出日志的 `legacy_v1_text_attrs` 数量和 `legacy_v1_nodes` 名单（这些节点照常渲染，但只有一个样式片段、不知道效果总开关，stderr 也会警告）；逐条细节在 `dynamic_text_render_info`（也包含 `static_text`：所用字体文件和匹配方式、是否开 kern、画了哪些效果、因总开关或单项关闭而跳过的效果、没画的效果和近似项）。
   - 参数解读：字形在文字空间（pt）排版后整体乘图层变换，字号和行距随之缩放；按样式片段取字体、字号、颜色和行距，不是整层一个字号；行距取该行最大值（自动行距 = `auto_leading_pct` × 字号，默认 1.2）；`effects_visible` 为 false 或效果 `enabled: false` 时不画；`kerning: manual` 且没有手动字距对时关闭 OpenType `kern`，`metrics` / `optical` 时打开（optical 只用字体字距对近似）；`tracking` 按 tracking/1000 em 加到每个字形；颜色叠加改填充色。
   - 1.4.0 的绘制规则（在 345 张 Photoshop 2026 金图上拟合并用验证集确认，历史实测见 [slicing-history.md](slicing-history.md) 10.6；默认值在 `text_render.DEFAULT_OPTIONS`）：
     - 混排行距：后一行字号更大时**不再额外下推**（`mixed_leading_k` 默认 0；1.3.x 是 0.86，在新建文字图层上被推翻）。
     - 抗锯齿：`antiAliasPlatformGray` 覆盖率做 gamma 1.4；`Strong` gamma 1.2 + 外扩 0.1 px、只在 x 方向取整笔位；`Smooth` 只在 x 方向取整；`PlatformLCD` gamma 1.2（样本少）。`Sharp` / `Crisp` / `None` 不加规则。
     - 描边：在 4 倍分辨率上用距离变换算描边区（需要 scipy），填充与描边按面积分开合成（内部/居中描边从填充里扣掉重叠部分），所以填充不透明度只作用于填充：填充不透明度 0 时只剩描边环。
     - 投影/外发光的扩展用 4 倍分辨率距离变换外扩；外发光模糊 σ = 大小 / 2.5，并读取外发光的「范围」（`inputRange`，新版 `export_slices.psjs` 导出为 `range`）。
     - 仿粗体：轮廓外扩 0.01 em，每个字形前进量加 0.03 em；仿斜体：水平错切 0.2。都只是近似（报告里记在 `approximations`），仿粗体仍有约 21 dB 的误差。
     - `--snap-glyph-origin`（默认开）在新数据上复核成立：关掉后中位数掉约 6 dB。
   - 按图层自动选择（1.4.1 起，1.4.2 起是唯一模式，历史实测见 [slicing-history.md](slicing-history.md) 10.7）：段落 `leadingType` 为 `leadingAbove` 时混排行距系数取 0.86，否则取 0；图层变换不等比时不加抗锯齿 gamma。1.4.2 删除了 `--text-profile`（含 `ps2026` / `older-file`），再传会报错退出。布局里有「不同行字号不同」的属性文字时，`validate` 会在 stderr 提示并在报告 `text_renderer.mixed_size_line_nodes` 列出节点。`--mixed-leading-k` 仍可单独覆盖。`--text-legacy-rules` 按 1.3.x 规则画，只用于对比。`validate --psd`（或 layout 的 `source.file`）用于在描述符缺少 `leadingType` 时从 EngineData 读取。
   - 1.4.3 的三条规则（历史实测见 [slicing-history.md](slicing-history.md) 10.8；默认值在 `text_render.DEFAULT_OPTIONS`，`--text-legacy-rules` 回到旧行为）：
     - 投影偏移：把已画好的图层覆盖率整体平移整像素，偏移 = 距离 × (−cos 角度, sin 角度) 四舍五入（半数远离 0，带 1e-6 容差），不再按亚像素重排字形（`shadow_offset_model`）。
     - 不应用字体的 OpenType `locl` 特性（`font_locl` 默认关）：Noto CJK 等字体在默认语言下会把 ASCII 数字换成等宽数字，Photoshop 用的是默认的比例数字。
     - 段落文字（`shape: box`）按框宽贪心换行：在空格后、CJK 字符前后断行，不在闭标点前或开标点后断行；行末是 CJK 字符时允许超出框宽 0.125 em（`box_cjk_overflow_em`）；首行基线 = 框顶 + 首行最高字形墨迹；基线落在框底以下的行不画（`box_wrap`）。
     - 段落框的左上角是文字的 `click_point`；`box_bounds_pt` 只取宽高，它的 left / top 可能在别的坐标系里（图层被移动或跨画板复制过），不加到锚点上。这样放出的框和图层实测框不相交时，改按实测框放（框顶 = 实测框顶，水平按首段对齐取左 / 中 / 右），`dynamic_text_render_info` 里记 `box_origin: bounds`。
   - 不支持、会记入 `approximations` 的：变形文字、段落文字的两端对齐 / 连字符和 Photoshop 的逐行排版器（Every-line Composer），字符水平/垂直缩放、渐变描边、非正常混合模式、颜色叠加以外的内部效果。找不到某条文字的字体、或某个字形加载失败时，`validate` 报错退出（`error.type: text_render_failed`，消息里带节点名），不跳过、不换字体也不换渲染器。用到描边、投影或外发光的扩展时需要 scipy，缺了同样报错（`text_renderer_unavailable`，安装命令 `pip install scipy`）。`--fonts-dir` 指向设计所用字体（Windows 用户安装的字体通常在 `%LOCALAPPDATA%\Microsoft\Windows\Fonts`）。

`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。预览文字复现不了 PS 自己的光栅化、混合模式和效果算法，带文字的指标残差集中在文字本身属于预期。

---

## 10. 实测记录

排查渲染、阈值或历史 dB 时，读 [slicing-history.md](slicing-history.md)（10.1–10.18）。那些是当时的实测记录，不是第二份规则。
