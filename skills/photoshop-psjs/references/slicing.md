# Agent 切图工作流

助手只准备材料；跑这个 skill 的 **agent**（大模型）阅读材料后编写 manifest（组件、部件、状态、规范名）。用户改 JSON 确认后，再由 `.psjs` 按 `layer_id` 导出 1x PNG，写出引擎无关的布局 JSON，并校验默认状态拼图。启动、守卫、`silent` 等规则见 [SKILL.md](../SKILL.md)。模板：[examples/export_slices.psjs](../examples/export_slices.psjs)、[examples/slicing_helper.py](../examples/slicing_helper.py)。

## 决策树

按这个顺序做，**确认前不要导出**。

1. **打开材料。** 先看 `full_render_preview.png`（若已有 Photoshop 导出的 `full_render.png`，以它为准），再对照 `crops_sheet.jpg`、`crops/<layer_id>.png` 和 `group_effects.json`。用 `crops_index.json` 把 id 对回 `tree.json`（可见性、路径、文本、效果）。不要猜测 id，也不要去读不存在的裁切图。选导出目标时，检查该元素最近的父组（或代表该元素的组）上有没有图层效果（如投影）：效果在组上、内层叶子没有时，按下文「组效果：烘焙还是引擎绘制」给该部件选 `effect_mode`——`baked` 导出该组（默认），`engine` 导出子层并把组的 `effect_params` 写进 `engine_effects`。不要因为 `crops/` 里没有该组缩略图就只退回叶子而不记录效果。
2. **识别组件。** 把页签组、底栏、按钮、列表行等交互控件收成组件，按槽位拆开，不要按 PSD 分组原样切开。
3. **分清共用零件和随状态变化的零件。** 图标、始终存在的底等跨状态不变的，放进该组件的 `shared`，文件名不加状态后缀。槽/底的明暗、选中高亮、只在某一态出现的标签等，放进 `states.<state>.parts`。
4. **区分状态变体和旧稿。** 主体/内容不变、只改明度、描边、发光、配色或缩放的，是状态变体，可以导出。同一槽位但换了内容或徽记的，当旧稿忽略。内容或母题对不上、吃不准时才写 `uncertain_hidden`，并附上两侧裁切图。
5. **只给会随状态变化的零件记 missing。** 以同组里状态最完整的那一项为模板（例如某选中项有 base + highlight + label），每个缺少其中一件的兄弟项都要列出，**包括 highlight**。不要因为共用图标没有另一态就标 missing。单个特殊样式的列表行（例如 self 高亮行）只是这一行的一个实例：不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都有成套变体。
6. **判断重复元素是否复用（见下文「重复元素：复用还是分开」）。** 先按 UI 用途和常识（游戏 UI 先验）加上 `reuse_candidates.json` 的证据，从逻辑上判断是否该共用一张图；该复用就用 `reuse_of` 指向同一张共享图。再对照效果图：复用后与设计对不上，先怀疑分析错了，只把对不上的实例拆出去单独导出；逻辑证据很强却对不上，写进 `reuse_decisions` 交用户决定。头像图与头像框必须分开：PSD 里合在一起时，**先自己按下文「头像与框合在一起：先拆，拆不开再问」尝试拆开**，拆完用框 + 占位头像重新拼合并和效果图对比；对得上就按拆分导出；对不上时先按「拆开后拼回去对不上：先定位效果，再决定问不问」逐层关效果定位、按两张修法重做并验证（换内容后约 35 dB，见 10.11），只有拆不开、修不好或属于适用边界之外才写 `reuse_decisions`（`type: split_avatar`）交用户，附上哪一层、为什么、修法和 dB 以及对比图。面板、弹窗、卡片、按钮底等 frame 类元素同理：**默认按视觉层次拆成可九宫 / 三宫 / 复用的部件**（见下文「frame 类元素：按视觉层次拆，九宫与装饰」），整张烘焙只在拆不开或拆后对不上时使用，并写 `bake_reason`。
7. **写好审核清单后停下。** 跑 `flatten` + `preview`，`status` 保持 `pending_review`，等用户改 JSON 确认后再导出。用户确认后跑 `confirm` 锁定清单（见第 6 节）；**锁定后不要再改 manifest**，要改就写 `manifest.proposal.json` 并停下等用户重新确认。
8. **确认后一口气跑完，不要停在导出。** 导出 → `build-layout` → `validate` 是一个整体；`export_log.json` 写出不算完成，只有 `validate/validate_report.json` 写出并读过 `issues`（含 `group_effect_lost`）、遮罩 PSNR / ΔE 后才算完成。`validate` 的最后一步用**实际导出的 PNG** 拼出默认态并画上标注，写出 `validate/preview_exported_annotated.png` 与 `validate/preview_exported_vs_ps.png`（左 PS 整图、右导出拼图+标注）；把这两张图作为最终成果交给用户。第 7 步导出前的审核预览不变。若 `issues` 里有 `reuse_mismatch`，按第 6 步处理（拆出单独导出，或交用户决定）后重跑。每条长命令都设超时并把输出写进日志；某步超时或报错时，读日志、修正后重跑这一步，不要静默结束。 `issues` 里的问题不能靠 agent 自己写个「接受」标记消掉（`reuse_accepted`、`split_best_effort`、`effects_checked` 之类的说明都不算）：按对应规则修，修不好就停下，把对比图和数字交给用户，等用户在 `reuse_decisions` 里填 `decision`。

选中槽上的隐藏层：若它落在**当前选中项**的槽里，并且是同一主体、只是加了发光或高亮样式，应作为该态的 `highlight`。若默认态就是这一项被选中，即使该高亮层在 PSD 里仍是隐藏的，也要纳入默认选中态，否则拼图会缺一层。只有内容或徽记对不上时，才用 `uncertain_hidden`。

**叠加还是替换：** 判断选中槽上的隐藏层时，先问它是叠在已有资源上面，还是要取代某个资源。一层发光/气泡/光晕盖在当前选中槽之上（与可见的选中底同时存在、范围相近、颜色同系，图层名或所在组像「当前 / 选中 / current / selected / glow / bg」之类），就是**叠加的高亮**，即使形状和底不同也算该态的 `highlight`，不要因为「可能被可见的选中底取代」就挂进 `uncertain_hidden`。只有它与某个可见资源占同一角色、内容或徽记不同（同一位置另一枚图标、另一段文案）时，才是替换，按旧稿处理。认作高亮后，按第 5 步给缺高亮的兄弟项记 `missing`（`role: highlight`）。

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

1. **智能对象内部有独立图层（`split_method: smart_object_contents`）。** 跑 `split-avatar`（见下）：它用 psd-tools 打开内嵌的 PSB，按「被剪贴的位图层 + 剪贴底 = 头像，其余可见层 = 框」分类，写出 `hide_inner_for_avatar` / `hide_inner_for_frame`、对比图和建议的部件。导出时 `export_slices.psjs` 对带 `split` 的条目：在**临时导出文档**里对智能对象做「编辑内容」（`placedLayerEditContents`），隐藏 `split.hide_inner` 列出的内层，存回这个临时副本再导出，源文档不动。链接型或非 PSD 的智能对象读不到内层，换下一种方法。
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

适用于头像、图标、道具等动态内容与带效果的框拆开的情况，也适用于其他拆分（按钮底与图标、卡片与插图等）。拆完用「框 + 占位内容」拼回去，和合并导出（同一临时副本、同一画布）比 PSNR。低于精确拆分的参考阈值（约 40 dB，见 10.10）或两张近似的阈值（约 35 dB，见 10.11）时，**agent 先自己定位**，再决定要不要交用户。

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
   - 不要把占位照片的内缘一圈直接裁进框来凑 dB：换内容后会露出旧照片（见 10.11）。

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
   - **状态变体**：主体/内容不变（同一个形象、同一句文案、同一块底），差别只在状态样式，例如明度、饱和度、高亮、描边、发光、缩放、配色。这类可以纳入该组件，导出时临时显示。
   - **旧版或备选稿**：同一位置的隐藏层换了内容或换了母题（例如换了一枚不同的徽记，或数字与符号不是同一套图形），应忽略，不要导出，也不要用它去填某一态。若该零件本身是跨状态共用的，把已经在用的那份真资源放进 `shared`，**不要**因为忽略了旧稿就把图标写成 missing。
   - **吃不准**：不要擅自选边。在审核清单里写 `uncertain_hidden`，带上两边的 `layer_id` 和裁切图路径，让用户决定。
   隐藏层若与任何已识别组件的状态无关，同样忽略。

   落在**当前选中项槽位**上的隐藏层（先做文首的「叠加还是替换」判断）：若主体/内容与该项正在使用的资源相同，只是加了发光、描边或高亮样式，应作为该选中态的 `highlight`（不要只用 `uncertain_hidden` 挂起）。若默认态就是这一项被选中，即便该层在 PSD 里仍隐藏，也要纳入默认选中态，否则校验拼图会少一层。只有内容或徽记明显不同、无法判断是高亮还是旧稿时，才写 `uncertain_hidden`。
4. **只有按设计会随状态变化的部件，找不到合格变体时，才记 missing。** 先看同组里哪一项的某一态最完整（零件最多），用它的角色集合当模板。例如某个选中项有 base + highlight + label，则每个缺少其中一件的兄弟项都要在 `missing` 里列出对应 `{ "state": "selected", "role": "..." }`，**包括 highlight**，等用户确认要不要补。跨状态不变的零件即使只有一份，也不是缺失，不要写「没有另一态的图标」。禁止捏造未画出的变体。

   单个特殊样式的列表行（例如只有一行画了 self 高亮）只是**这一行的一个实例**，不是「每一行都该有 self / 这一行还缺 normal」。不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都画出了成套状态变体。
5. **导出目标要看效果在哪一层。** 图层效果（如投影、外发光、描边）经常加在代表该元素的**父组**上，内层叶子（智能对象、形状）自身没有效果。选 `layer_id` / `select_ids` 时，检查最近的父组（或代表该元素的组）：组有效果而叶子没有，按第 1 节前的「组效果：烘焙还是引擎绘制」选 `effect_mode`。`baked` 选组 id，可见框用组的含效果框；`engine` 选子层，并写 `engine_effects`。组 id 没有裁切图时同样如此。例如某个图标组 `<item>` 上有 DropShadow、内层叶子没有：`baked` 写该组 id；`engine` 写子层 id + `"effect_mode": "engine"` + 抄自 `effect_params` 的 `engine_effects`。不能只写子层而什么都不记。
6. 底图、高亮、图标、文字始终是独立 `parts`。类型是 `image` | `nine_slice` | `dynamic_text` | `static_text` | `art_text`。可变文案（分数、名字、倒计时、1–2 位名次）用 `dynamic_text`（只出属性，不出图）。固定文案（页签名、导航名、标题等）默认也只出属性：纯色填充，可带描边和/或投影，用 `static_text`，参数写在 `text.layer_effects` / `text.stroke`，由引擎绘制。只有引擎画不了时才用 `art_text` 出图：渐变填充或渐变叠加、斜面浮雕、变形（或非仿射变换）、图案叠加、绸缎、内发光、内阴影、外发光、颜色叠加，以及已栅格化或非文字图层；未知效果同样保守地出图。渐变/图案描边也出图。`dynamic_text`、`static_text`、`art_text` 都不得带九宫边距（不要对文字图做九宫分析）。
7. 重复元素按「重复元素：复用还是分开」处理：逻辑上该复用的导出一张、各实例用 `reuse_of` 引用；与设计对不上的拆开；有证据却对不上的交用户决定。头像图与头像框分开（先自己拆，拆不开才问）；frame 类元素按视觉层次拆成九宫 / 三宫 / 装饰部件。全部 1x。

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
| `uncertain_hidden` | 吃不准是状态变体还是旧稿时填写：`layer_ids`、对应 `crops`、以及要问用户的问题。不要在这里擅自选用其中一侧 |
| `ignored_hidden` | 已认定为无关或旧稿而忽略的隐藏层（`layer_id` + `reason`），供审核对照 |
| `children` | 子组件 |
| `source_path` / `layer_id` | 仅供对照的源图层引用 |

部件（`part`）常用字段：`name`（规范文件名）、`role`（`base`、`highlight`、`icon`、`text` 等）、`kind`、`layer_id`、`select_ids`、`hidden`、`nine_slice`、`text`、`logical_bounds`、`source_name`、`source_path`。重复元素复用时加 `reuse_of`（共享图名，不带实例序号）；（`reuse_accepted: true` 只是说明，不能绕过复用误差上限；接受差异必须是用户在 `reuse_decisions` 里填的 `decision`。）运行时内容的占位图（如头像）加 `placeholder: true`。拆分出来的部件加 `split_method`（`smart_object_contents` | `mask_or_clip_layers` | `frame_from_instance` | `pixel_mask_estimate`）、`split_evidence`（对比结果和对比图），智能对象拆分再加 `split: { "hide_inner": [{ "id": 内层id, "name": 内层名 }] }`（导出时在临时副本里隐藏这些内层；先按 id、再按唯一的名字匹配，找不到就报错），像素估算加 `split_approximate: true`。frame 类元素仍整张烘焙时加 `bake_reason`。父组带效果时再加：`effect_mode`（`"baked"` | `"engine"`）、`effect_reason`（一句话理由，审核时可见）、`engine_effects`（仅 `engine`，从 `effect_params` 抄）。

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
2. 把 `preview_annotated.png` 和 `manifest.json` 交给用户。用户改 JSON：改名、改类型、改九宫格、拆/合并部件、确认或删除 `missing`、把不该导出的隐藏层拿掉、切换 `effect_mode`、对 `reuse_decisions` 填 `decision`（看对比图）。
3. `status` 在用户确认前保持 `pending_review`。用户确认后运行 `python slicing_helper.py confirm --manifest manifest.json`：改为 `confirmed`、写 `confirmed_at`，并写出锁 `manifest.confirmed.json`（逐字节副本）和 `manifest.confirmed.sha256`。用户对某条 `reuse_decisions` 的回答用 `--decision ID=VALUE`（可重复）记进去，会写 `decided_by: "user"` 和 `decided_at`；`confirm` 输出的 `not_user_decided` 列出还没有用户回答的 `split_avatar` / `reuse` 条目——其中卡着低于阈值的拆分或超上限复用的，要先问用户再导出。导出脚本拒绝其它状态（测试授权下允许 `pending_review`）。
4. 用户确认后才进入导出。**锁定后的清单不得再改**（包括加条目、改 `reuse_accepted`、改 `split_evidence`、跑会重写清单的 `flatten`）。`export_slices.psjs` 发现 manifest 和 `manifest.confirmed.json` 不一致就拒绝导出；`build-layout` 发现哈希不一致就退出（退出码 3，不写布局）；`validate` 记 `manifest_changed_after_confirm`；没锁记 `manifest_not_locked`。导出前可以先跑 `check-lock`（改过退出 3，没锁退出 4）。
5. 导出或校验中发现清单需要改：把改动写进 `manifest.proposal.json`（改什么、为什么、证据和对比图），**停下**交用户；用户同意后再改清单，并用 `confirm --relock` 重新锁定。

---

## 7. 导出

[examples/export_slices.psjs](../examples/export_slices.psjs) 只硬编码一个 `CONFIG_URL`，从作业 JSON 读路径（见 [export_config.example.json](../examples/export_config.example.json)）。按 `layer_id`（及 `select_ids` 里的剪贴层）导出 1x PNG，图层效果烘焙进图；带 `reuse_of` 的实例只导出一张共享图（见下）。`dynamic_text` 和 `static_text` 不出图，只写文本属性（含描边、投影）；`art_text` 出图。

带 `reuse_of` 的条目由 `flatten` 分配：源实例（名称等于共享名的那条，否则第一条默认态实例）导出 `<共享名>.png`（`image_name`），其余实例 `export_png: false`，脚本只记录它们的边界，不再出图。

`select_ids` 有多个 id 时，脚本把它们一起选中（`addToSelection`）、「从图层新建」、`mergeVisible` 后 trim 导出。`engine` 部件就靠这条路径：选的是子层，新文档里没有父组，所以组上的效果、组不透明度和混合模式都不会带进 PNG，子层自己的效果仍在。PNG 原点照样用 trim 内缩量计算；`layer_id` 留组 id 时，日志里的 `logical_bounds` 是组的 `boundsNoEffects`。

若条目 `hidden`（或 `from_hidden`）为真，脚本先显示该层及其被隐藏的祖先，再「从图层新建」、trim、保存，然后把可见性改回去。整次作业在 `suspendHistory` 里进行；脚本自己打开的文档一律 `closeWithoutSaving`。每条描述符 `_options.dialogOptions = "silent"`。只操作作业打开的文档，绝不改、存、关用户已打开的其它文档。

可见框记录规则不变：临时文档与源画布对齐时，紧致可见原点 = trim 前 `layer.bounds.left/top` 减 trim 后的 left/top。不要把 trim 前的松框左上角当成 PNG 原点。

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
- 复用：带 `reuse_of` 的节点 `image` 都是 `<共享名>.png`，九宫格取源实例；非源实例的 `visible_bounds` = 源实例可见框按两者逻辑框原点的差平移。顶层附 `reuse_decisions`。
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

1.4.9 起的护栏（都进 `issues`，不能由 agent 自行标记接受）：`manifest_changed_after_confirm` / `manifest_not_locked`（第 6 节）；`split_below_threshold`（实测：导出的拆分部件拼回去，在其合并框内与 PS 整图比 dB，见 `split_checked`；或 `split_evidence` 里实测 dB 低于阈值）、`split_unverified`；`reuse_mismatch`（复用误差超上限，见「重复元素」）。用户在 `reuse_decisions` 填了 `decision` 的，改记在 `split_below_threshold_user_accepted` / `reuse_mismatch_accepted`。

1. **默认状态**：只拼 `default: true` 的部件（即 PSD 里当前显示的那一态，加上始终可见的 chrome），用 src-over（`Image.alpha_composite`）叠回画板，与 Photoshop 整图比像素差 / CIEDE2000 ΔE。同时报告画上属性文字（`dynamic_text` / `static_text`）近似后的数字（`diff_text_rendered`）、以及遮罩这些文字区域后的数字（`diff_text_masked`），并列出误差最大的 10 条。再检查漏切、重名、空图。**判断切图精度只看遮罩后的指标**；带文字的数字只说明预览和效果图有多像。
2. **引擎效果**：`effect_mode: engine` 的部件，投影 / 外发光按 Pillow 近似画在部件下面（alpha → 按角度/距离偏移 → 扩展后高斯模糊 → 颜色/不透明度，普通混合），报告列在 `engine_effects_emulated`。描边、内阴影、叠加等无法模拟的，按估计的 `effect_bounds` 把外圈（内部效果则整框）从遮罩指标里去掉，列在 `engine_effects_unemulated`。`issues` 里的 `group_effect_lost` 表示选了带效果组的子层却既没烘焙也没写 `engine_effects`，必须修。部件带 `blend_mode` 时按对应公式叠到画布上（normal / multiply / screen / linear_burn / lighten / darken / linear_dodge；其它模式按 normal 画并在 `placed` 里记 `blend_mode_approximated_normal`）。拆分相关的 `outer_effect_into_hole`、`blend_mode_content_dependent`、`blend_mode_not_replayable` 见「拆开后拼回去对不上」。
3. **复用实例**：每个带 `reuse_of` 的默认态节点，在其可见框内（去掉属性文字与无法模拟的效果区域）比 PS 整图与拼图。MAE > `--reuse-tol-mae`（默认 4）或差值 >16 的像素占比 > `--reuse-tol-pct`（默认 1%）时记为 `reuse_mismatch`（含实例名、区域、指标和 `validate/reuse/<名>.png` 对比图）；`reuse_accepted` 的改记 `reuse_mismatch_accepted`。逐实例数据在 `reuse_checked`。
4. **位置（1.4.11）**：`layer_bounds_mismatch`：清单的 `logical_bounds` 与该条目 `layer_id` 实测的逻辑边界差超过 2 px（指向了兄弟实例的图层）；借用别的实例图的非默认态改按清单的框放，不报。`placement_offset`：每张正常混合、100% 不透明的图（含按原尺寸画的九宫格），用它自己的不透明像素、去掉上层盖住的部分，在 ±2 px 内和 PS 整图配准；某个偏移的 MAE 不到原位的一半且低 0.5 以上就报，附 `fits_at_shift`。处理办法是按当前 `export_slices.psjs` 重导或改正 `layer_id`，不要手动挪布局。参与配准的图数在 `placement_checked`。
4. **交替状态**：对每个交互组件的非默认状态（含仅存在于 `missing` 中的状态），按「共用部件 + 该状态部件」拼图，互斥组的兄弟项切到对应态，写出 `validate/states/<component_id>_<state>.png`。只对真正缺失的、按设计随状态变化的零件画红框并标 `missing`。这些预览**不**和默认态整图比 PSNR（对不上是正常的）。
5. **导出后标注预览（最终交付）**：用实际导出的 PNG 按布局 JSON 拼默认态（同上，含引擎效果近似），再按审核预览的样式画标注：每个切图按 `visible_bounds` 画框、规范名、九宫格线、类型和 `fx:baked` / `fx:engine`，复用实例的标签后缀为 `→ <共享名>`。写出 `validate/preview_exported_annotated.png` 和 `validate/preview_exported_vs_ps.png`（左 PS 整图、右导出拼图+标注），路径记在报告的 `exported_preview`。把它们交给用户。

6. **属性文字预览**（`dynamic_text` / `static_text`，[examples/text_render.py](../examples/text_render.py)）：只是近似，不是 Photoshop 的文字引擎，也不是目标引擎的渲染。投影按规则画出，总开关或单项关闭的效果不画；没有 Pillow 回退。要像素一致的文字，让 Photoshop 栅格化文字图层（或临时显示后导出带字整图）做底图；要和上线一致，用目标引擎按同一份布局 JSON 出图。

   - `--text-renderer auto`（默认）：装了 `uharfbuzz` + `freetype-py` 用 `freetype`（HarfBuzz 排版，FreeType 不做 hinting、直接填充轮廓，描边用圆角外扩的轮廓描边）；否则有 `skia-python` 用 `skia`（Skia 轮廓路径）；也可显式指定 `freetype` 或 `skia`。所需的包缺了就报错退出，不会换渲染器，也没有 Pillow 文字路径。报告 `text_renderer` 记 `requested`、`used`、参数，以及旧格式导出日志的 `legacy_v1_text_attrs` 数量和 `legacy_v1_nodes` 名单（这些节点照常渲染，但只有一个样式片段、不知道效果总开关，stderr 也会警告）；逐条细节在 `dynamic_text_render_info`（也包含 `static_text`：所用字体文件和匹配方式、是否开 kern、画了哪些效果、因总开关或单项关闭而跳过的效果、没画的效果和近似项）。
   - 参数解读：字形在文字空间（pt）排版后整体乘图层变换，字号和行距随之缩放；按样式片段取字体、字号、颜色和行距，不是整层一个字号；行距取该行最大值（自动行距 = `auto_leading_pct` × 字号，默认 1.2）；`effects_visible` 为 false 或效果 `enabled: false` 时不画；`kerning: manual` 且没有手动字距对时关闭 OpenType `kern`，`metrics` / `optical` 时打开（optical 只用字体字距对近似）；`tracking` 按 tracking/1000 em 加到每个字形；颜色叠加改填充色。
   - 1.4.0 的绘制规则（在 345 张 Photoshop 2026 金图上拟合并用验证集确认，见 10.6；默认值在 `text_render.DEFAULT_OPTIONS`）：
     - 混排行距：后一行字号更大时**不再额外下推**（`mixed_leading_k` 默认 0；1.3.x 是 0.86，在新建文字图层上被推翻）。
     - 抗锯齿：`antiAliasPlatformGray` 覆盖率做 gamma 1.4；`Strong` gamma 1.2 + 外扩 0.1 px、只在 x 方向取整笔位；`Smooth` 只在 x 方向取整；`PlatformLCD` gamma 1.2（样本少）。`Sharp` / `Crisp` / `None` 不加规则。
     - 描边：在 4 倍分辨率上用距离变换算描边区（需要 scipy），填充与描边按面积分开合成（内部/居中描边从填充里扣掉重叠部分），所以填充不透明度只作用于填充：填充不透明度 0 时只剩描边环。
     - 投影/外发光的扩展用 4 倍分辨率距离变换外扩；外发光模糊 σ = 大小 / 2.5，并读取外发光的「范围」（`inputRange`，新版 `export_slices.psjs` 导出为 `range`）。
     - 仿粗体：轮廓外扩 0.01 em，每个字形前进量加 0.03 em；仿斜体：水平错切 0.2。都只是近似（报告里记在 `approximations`），仿粗体仍有约 21 dB 的误差。
     - `--snap-glyph-origin`（默认开）在新数据上复核成立：关掉后中位数掉约 6 dB。
   - 按图层自动选择（1.4.1 起，1.4.2 起是唯一模式，见 10.7）：段落 `leadingType` 为 `leadingAbove` 时混排行距系数取 0.86，否则取 0；图层变换不等比时不加抗锯齿 gamma。1.4.2 删除了 `--text-profile`（含 `ps2026` / `older-file`），再传会报错退出。布局里有「不同行字号不同」的属性文字时，`validate` 会在 stderr 提示并在报告 `text_renderer.mixed_size_line_nodes` 列出节点。`--mixed-leading-k` 仍可单独覆盖。`--text-legacy-rules` 按 1.3.x 规则画，只用于对比。`validate --psd`（或 layout 的 `source.file`）用于在描述符缺少 `leadingType` 时从 EngineData 读取。
   - 1.4.3 的三条规则（见 10.8；默认值在 `text_render.DEFAULT_OPTIONS`，`--text-legacy-rules` 回到旧行为）：
     - 投影偏移：把已画好的图层覆盖率整体平移整像素，偏移 = 距离 × (−cos 角度, sin 角度) 四舍五入（半数远离 0，带 1e-6 容差），不再按亚像素重排字形（`shadow_offset_model`）。
     - 不应用字体的 OpenType `locl` 特性（`font_locl` 默认关）：Noto CJK 等字体在默认语言下会把 ASCII 数字换成等宽数字，Photoshop 用的是默认的比例数字。
     - 段落文字（`shape: box`）按框宽贪心换行：在空格后、CJK 字符前后断行，不在闭标点前或开标点后断行；行末是 CJK 字符时允许超出框宽 0.125 em（`box_cjk_overflow_em`）；首行基线 = 框顶 + 首行最高字形墨迹；基线落在框底以下的行不画（`box_wrap`）。
   - 不支持、会记入 `approximations` 的：变形文字、段落文字的两端对齐 / 连字符和 Photoshop 的逐行排版器（Every-line Composer），字符水平/垂直缩放、渐变描边、非正常混合模式、颜色叠加以外的内部效果。找不到某条文字的字体、或某个字形加载失败时，`validate` 报错退出（`error.type: text_render_failed`，消息里带节点名），不跳过、不换字体也不换渲染器。用到描边、投影或外发光的扩展时需要 scipy，缺了同样报错（`text_renderer_unavailable`，安装命令 `pip install scipy`）。`--fonts-dir` 指向设计所用字体（Windows 用户安装的字体通常在 `%LOCALAPPDATA%\Microsoft\Windows\Fonts`）。

`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。预览文字复现不了 PS 自己的光栅化、混合模式和效果算法，带文字的指标残差集中在文字本身属于预期。

---

## 10. 实测（Windows / Photoshop 2026）

对象：一份多画板 UI PSB 的**副本**（未改、未保存、未关闭用户可能打开的原文档或其它未指定文档）。实测画板是一个 1080×2340 的列表界面，65 个可见元素。时间均为 Asia/Shanghai（UTC+8）。项目词表不进技能仓库，回归时用仓库外的 `--rules` 文件。

1.4.8 起固定文案的分类规则已改：只带纯色填充和描边/投影的固定标签改为 `static_text`（只出属性），不再一律出图。下面 10.1–10.4 表里的类型计数（如 image 18 / nine_slice 21 / dynamic_text 22 / art_text 4）是此规则之前测的，这里不改写。按新规则，当时出图的标题、页签和导航文案多数会改判为 `static_text`。

### 10.1 第一轮（修前）

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 82.9 秒（打开 2.4 s，条目合成 64.6 s，画板合成 15.8 s）。初步类型：image 23、nine_slice 16、dynamic_text 24、art_text 2。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 24、art_text 2。 |
| 预览 | 已写出标注预览。测试中将自动清单视为已确认。 |
| 导出 | 65/65 成功（41 张 PNG + 24 条仅属性的动态文字），另写出 1080×2340 整图。墙钟约 43.1 秒（15:36:48–15:37:31）。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 均为空。 |
| 校验 | 漏切 / 重名 / 空图：0。全图：MAE 10.225，PSNR 17.65 dB，mean ΔE 4.082。遮罩动态文字后：MAE 6.277，PSNR 20.54 dB，mean ΔE 2.599，p95 ΔE 13.569，ΔE>2 占 16.423%，ΔE>5 占 9.01%。 |

当时的问题：成对页签里同一文案角色一种判为 `art_text`、另一种判为 `dynamic_text`，拼图缺字；带前导零的数字串被当成名次；几乎每条切图周围有一圈描边状残差。

描边残差的根因（已用逐条 best-fit 和一次 PS probe 核实，不是色管、JPEG 或预乘）：「从图层新建」得到的临时文档与源画布对齐，DOM `layer.bounds` 比 trim 后的不透明区域更松。第一轮把这个较松框的左上角当成 PNG 原点，没有加上被 trim 掉的左边/上边像素。trim 后 `layer.bounds.left/top` 是内缩量（常为负数，如 `-5,-6`）。紧致原点 = `before.left - after.left`。不透明像素（A≥250）在正确原点上与 PS 整图完全重合（mae=0）。偏移不是全局 +2,+2：有的切图约 (0,+2)，有的约 (+5,+6) 或 (+3,+4)，卡片/美术字约 (+2,+2)。

### 10.2 第二轮（修后重跑，同副本）

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 21.9 秒（15:54:32–15:54:54；打开 2.5 s，条目 1.7 s，画板合成 17.7 s）。类型：image 23、nine_slice 16、dynamic_text 22、art_text 4。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 22、art_text 4。 |
| 预览 | 已重写标注预览。测试中将自动清单视为已确认。 |
| 分类 | 成对页签文案均为 `art_text`。顶栏标题与导航标签都是带效果的固定文案，各自为 `art_text`（角色不同，不必成对）。带前导零的数字串为 `dynamic_text`（按名字，不是名次）。分数、倒计时、名字、1–2 位名次均为 `dynamic_text`。 |
| 导出 | 65/65 成功（43 张 PNG + 22 条仅属性的动态文字；页签与导航上先前缺字的固定文案改为美术字出图）。墙钟约 42.1 秒（15:55:34–15:56:16），整图 5.3 秒。43 条光栅切图都记下了 `visible_bounds_from_trim_insets`。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 均为空。日志里 `full_render.out_size` 仍可能是整份多画板画布的尺寸（crop 后 `doc.width` 不变），但 `saveAs.png` 写出的文件是当前画板 1080×2340。 |
| 校验 | 漏切 / 重名 / 空图：0。约 10.4 秒（15:57:17–15:57:27）。用文档所用字体（`--fonts-dir`）渲染了全部 22 条动态文字。 |

校验数字对比（同画板、同副本）：

| 指标 | 修前（遮罩动态文字） | 修后（遮罩动态文字） | 修后（Pillow 文字，全图） | 修后（Pillow 文字，排除文字框） |
|------|----------------------|----------------------|---------------------------|----------------------------------|
| MAE | 6.277 | **0.076** | 2.440 | 0.125 |
| PSNR | 20.54 dB | **46.24 dB** | 23.39 dB | 39.01 dB |
| mean ΔE | 2.599 | **0.047** | 1.041 | 0.067 |
| p95 ΔE | 13.569 | 0.153 | 0.429 | 0.166 |
| ΔE>2 | 16.423% | 0.295% | 2.786% | 0.338% |
| ΔE>5 | 9.01% | 0.157% | 2.547% | 0.199% |
| 像素差>16 | 10.893% | 0.147% | 2.527% | 0.190% |

Pillow 文字版 mean ΔE 最差的 10 条全部是 `dynamic_text`（分数、名字、名次），来自 Pillow 无法复现 PS 的 hinting、字框锚点和外描边，不是切图错位。遮罩后残留的 >16 差像素共 3429 个（约占遮罩后像素的 0.15%），几乎都叠在底栏、导航按钮与末行列表底板等重叠层。这是普通 src-over 不模拟 PS 混合模式造成的，属预期残差，不是漏切或原点错误。

### 10.3 规则文件外置后的回归（同副本）

项目词表放在仓库外的 `--rules` JSON（不进技能仓库）。带规则完整重跑：推断 65 条（约 25.8 秒，16:11:01–16:11:28），类型与 8.2 相同（导出后 image 18 / nine_slice 21 / dynamic_text 22 / art_text 4）；导出 65/65 成功、43 张 PNG（16:11:56–16:12:39，约 42.4 秒）；校验约 10.6 秒，漏切/重名/空图为 0。遮罩动态文字：MAE 0.076，PSNR **46.24 dB**，mean ΔE **0.047**（与 10.2 一致）。Pillow 文字全图：PSNR 23.39 dB，mean ΔE 1.041。`docs_before` / `docs_after` 为空。

不传 `--rules` 时，对同一画板只跑推断也成功，且 **kind 完全一致**（同样 22 条动态文字、4 条美术字），说明分类核心不依赖项目词表。规范名则更泛化：画板名无法拉丁化时模块落在 `main`，徽章/头像等缺少用途词，只能编成 `item1_3` 这类序号。举例（带规则 → 无规则）：

- `<module>_bg_item1_normal` → `main_bg_item1`
- `<module>_icon_badge_1` → `main_bg_item1_3`
- `<module>_icon_avatar_item1` → `main_bg_item1_2`
- `<module>_img_tab_off` / `<module>_img_tab_on` → `main_img_name_off` / `main_img_name_on`
- `<module>_img_title`（标题或导航标签）→ `main_img_name`
- 分数、倒计时、1–2 位名次、带前导零的名字等，无规则时仍能识别用途（`main_img_score_item1`、`main_img_timer`、`main_img_place_item4`、`main_img_name_item6`），只是模块变成 `main`。

**一并核实的 API / 行为：**

- `boundsNoEffects` 确是不含效果的逻辑框。同次 `get` 还可能出现 `bounds`、`boundsNoMask`，部分层有 `pathBounds` 或 `boundingBox`。不要把 `bounds` 直接当成「含效果可见框」。
- DOM `layer.bounds` 在「从图层新建」的栅格副本上往往比 trim 更松；trim 后 left/top 是内缩量，可见原点用 `before - after`。
- 对多画板文档做「从图层新建」时，新文档可能仍是整份画布；`saveAs.png` 有时会按当前画板写出目标尺寸的文件，但 `doc.width` 仍是整布尺寸。模板会尝试按 `artboard.bounds` 裁切；校验以实际 PNG 尺寸为准。
- 16 位文档导出前需转 8 位（trim 前就转，使内缩量与导出 PNG 一致）。
- `psd-tools` 1.26 默认 `max_objects=1_000_000`，大 PSB 会超限；渐变九宫格分析需要 scipy。
- 校验拼图用 `Image.alpha_composite`（src-over）。`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。

### 10.4 Agent 驱动的组件 / 状态切图（同副本）

改成「助手只准备材料，agent 看图写清单」后再跑同一份列表界面副本、同一块 1080×2340 画板。时间均为 Asia/Shanghai（UTC+8）。

| 步骤 | 结果 |
|------|------|
| 准备材料 | `prepare` 约 25.3 秒。图层树含隐藏层；裁切图 169 张，其中 50 张来自隐藏层或隐藏祖先下的层（psd-tools 需临时打开祖先可见性才能合成出像素，只改内存、不写回 PSD）。 |
| Agent 清单 | 同时阅读整图、裁切图和图层树后编写 v2 manifest，不是关键词推断。交互组件按状态拆开，跨状态不变的零件进 `shared`，隐藏的交替状态纳入，无关隐藏层与旧稿忽略。默认态仍为 65 条，类型与 10.2 / 10.3 相同：image 18 / nine_slice 21 / dynamic_text 22 / art_text 4。 |
| 双页签开关 | `tab_group` 下两个 `tab`。默认：右侧页签为 selected（高亮 + 美术字），左侧为 normal（未选中高亮 + 美术字）。隐藏层里找到了右侧的 normal（高亮 + 文案）和左侧的 selected（高亮 + 文案），均已导出。没有缺失的页签状态。共享底槽与中缝是 group 级部件。预留的第三页签组与其它废弃页签组忽略。 |
| 底栏导航 | `navbar` 下三个 `nav_item`。从已有成对状态看出：槽/底和高亮随状态变化，图标跨状态共用，标签只出现在 selected。三项的图标都进 `shared`（文件名不加状态后缀）。右侧项 selected：槽、标签、以及一层默认下仍隐藏的高亮；normal：另一明度的槽（隐藏层，同主体）；图标始终是那份真资源。同一位置另有一张不同徽记的隐藏图标，按旧稿忽略，**不**把共用图标写成 missing。中、左项：图标共用，normal 槽在可见层；selected 的槽/高亮没有画，记入 `missing`（`role: base` / `highlight`）。中间项另有隐藏的 selected 标签。左侧项没有 selected 标签，一并记入 `missing`。隐藏祖先下的锁与其它预留锁忽略。 |
| 审核 / 导出 | 已写标注预览。测试中将 agent 清单视为已确认（`pending_review`）。导出 75/75 成功（约 57 秒，16:39:18–16:40:16）；当时 10 条隐藏态均临时显示后恢复。随后把跨状态共用的图标从单一状态中抽出（不重跑 Photoshop，只重命名已有切图），并按「只记录随状态变化却没画的零件」重写 `missing`。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 为空。 |
| 校验 | 约 12.7 秒。漏切 / 重名 / 空图：0。按 PSD 绘制顺序拼默认态后，遮罩动态文字：MAE 0.076，PSNR **46.24 dB**，mean ΔE **0.047**（与 10.2 / 10.3 一致）。Pillow 文字全图：PSNR 23.39 dB，mean ΔE 1.041。另写出 4 张交替状态预览（右侧页签 normal、左侧页签 selected、中间导航 selected、右侧导航 normal），只供目视，不和默认整图比 PSNR。 |

要点：默认态数字要和以前对齐，清单条目必须按 PSD 自下而上的顺序绘制；`flatten` / `build-layout` 在提供 `layer_tree` 时会排这个序。顺序不对时（例如把页签中缝画在高亮下面），遮罩 PSNR 会掉到约 38 dB。

### 10.5 动态文字预览渲染（1.3.0，另一份列表界面副本）

样本：一块 1080×2340 列表界面画板上的 19 条动态文字。拉丁字母，单行或两行（两行为小字号标签 + 大字号数字混排），同一字体族的两种字重，文字图层都带约 1.345 的缩放变换；9 条带可见的描边或投影，另有几条的描边被图层效果总开关隐藏。抗锯齿 18 条为 `antiAliasPlatformGray`，1 条为 `antiAliasSharp`。基准是让 Photoshop 把每条文字图层单独栅格化（效果关、开各一张）。时间为 Asia/Shanghai（UTC+8）。

逐条文字（只算有字的像素，叠在 50% 灰上比 PSNR）：

| 渲染 | 无效果 | 带效果（9 条） |
|------|--------|----------------|
| 1.2.0 Pillow（旧参数解读） | 17.2 dB | 16.4 dB |
| 正确参数 + HarfBuzz + FreeType，亚像素笔位 | 28.7 dB | 23.5 dB |
| 同上，`mixed_leading_k = 0`（不做混排行距修正） | 24.6 dB | — |
| 同上 + 字形笔位取整（1.3.0 默认） | **42.8 dB** | **35.2 dB** |
| HarfBuzz + Skia 路径 + 笔位取整 | 40.2 dB | 33.3 dB |
| Pillow（正确参数，无排版；1.3.1 已删除该路径） | 26.4 dB | 21.1 dB |

整板 `validate`（同一批切图，`diff_text_rendered` 为整图 PSNR；文字框内为只算动态文字框）：

| 运行 | `diff_text_rendered` | 文字框内 | `diff_text_masked` |
|------|----------------------|----------|--------------------|
| 1.2.0（Pillow） | 25.07 dB | 15.3 dB | 47.29 dB |
| 1.3.0，旧导出日志（v1 文字属性） | 26.63 dB | 17.0 dB | 47.29 dB |
| 1.3.0，v2 文字属性，`freetype` | **44.83 dB** | **38.0 dB** | 47.29 dB |
| 1.3.0，v2，`skia` | 44.21 dB | 36.8 dB | 47.29 dB |
| 1.3.0，v2，`pillow`（1.3.1 已删除） | 34.14 dB | 24.4 dB | 47.29 dB |
| 1.3.0，v2，`freetype --no-snap-glyph-origin` | 37.00 dB | 27.5 dB | 47.29 dB |

要点：

- 误差主要来自参数读错，而不是光栅化算法：行距没乘变换（45 px 被当成 33.5 px）、两行混排只用一个字号、被总开关隐藏的描边照画。
- 混排行距系数 0.86 是在这 5 条两行样本上拟合的；取 0.75 或 1.0 时逐条 PSNR 掉到约 37 dB。1.4.0 在新建的文字图层上推翻了它（默认改为 0），这块画板仍需 0.86，见 10.6。
- 本样本的手动字距为 `manual` 且没有字距对；打开 `kern` 反而掉约 2 dB。
- v2 文字属性取自同一批图层描述符：用 `text_render.text_attrs_from_descriptor()` 解析，与新版 `export_slices.psjs` 的 `parseText()` 在全部 19 条上逐字段一致（Node 下对照）。这一轮没有重跑 Photoshop 导出。
- 遮罩后的 47.29 dB 不变，说明改动只影响文字预览，不影响切图校验。
- **未验证**：中文与 CJK 标点挤压、段落文字、变形文字、其它抗锯齿方式、仿粗体、渐变描边、macOS 上的 Photoshop 光栅化。笔位取整和混排行距系数都只在这一份样本上成立。


### 10.6 文字参数矩阵：Photoshop 金图 vs 规则渲染（1.4.0）

方法：在 Windows / Photoshop 2026 上，脚本只在自己新建的临时文档（72 ppi，pt = px）里逐条建点文字图层，按参数设好后导出 PNG 和图层描述符做金图；描述符用 `export_slices.psjs` 的 `parseText()` 解析（JS 与 Python 解析在全部条目上一致），再用本助手的 HarfBuzz + FreeType 渲染比较。字体为 3 款拉丁 + 3 款中文开源字体（含粗体），按用户安装，渲染用同一批字体文件。指标只算有墨水的像素（金图或渲染 alpha > 8），叠在 50% 灰上：PSNR、MAE、alpha 误差、ΔE、IoU，另记 ±3 px 最佳平移、外框差和重心差。只报分布，不设阈值。

矩阵：单因素（字体、字号 12–96、缩放 0.5–2、字距、行距、多行、对齐、段落文字、仿粗体/仿斜体、6 种抗锯齿、手动/度量/视觉字距、描边位置与粗细、投影、外发光、颜色叠加、不透明度、同行混排、跨行混排），再加组合（字号×描边、字号×投影、描边×投影、抗锯齿×字号、中文×描边、字号×描边×投影、缩放×效果）。按维度以固定种子 2:1 分训练 / 验证（181 / 84）。规则只在训练集上拟合，然后在验证集和训练后新生成的 48 条（val2，新内容）上一次性确认；之后为排查一个冲突又加了 6 + 8 + 18 条只用于确认的跨行混排样本（固定行距、带缩放变换、Optyca 排版引擎）。共 345 条。

结果（墨水 PSNR 中位数，1.3.1 → 1.4.0）：

| 集合 | 条数 | 1.3.1 | 1.4.0 | 变好 / 变差（>0.1 dB） |
|------|------|-------|-------|------------------------|
| 训练 | 181 | 25.7 | 29.1 | 161 / 0 |
| 验证 | 84 | 25.5 | 28.8 | 73 / 0 |
| val2（训练后新生成） | 48 | 21.5 | 26.5 | 43 / 1 |
| 跨行混排确认（val3–5） | 32 | 12.5–12.9 | 28.3–31.4 | 32 / 0 |
| 全部 | 345 | 24.2 | 28.3 | 309 / 1（无一条变差超过 0.5 dB） |

训练与验证的提升相近（+3.4 / +3.3 dB），没有条目标为疑似过拟合。全部条目的 MAE 中位数 9.2 → 4.3，ΔE 8.3 → 3.9，IoU 0.907 → 0.950。

各规则在验证集上的消融（开 vs 关，只算受影响条目）：

| 规则 | 验证集平均变化 | 说明 |
|------|----------------|------|
| 混排行距 k = 0 | +6.2 dB | 受影响 4/4 变好；val3–5 的 32 条也全部变好 |
| 描边：4× 距离变换 + 分开合成 | +4.8 dB | 81% 变好，最差 −0.9 dB |
| 扩展：4× 距离变换 | +0.8 dB | |
| 外发光：范围 + σ = 大小 / 2.5 | +11.4 dB | |
| 仿粗体 | +8.5 dB | 仍约 21 dB |
| 仿斜体 | +10.6 dB | 只有 val2 的 3 条 |
| PlatformGray gamma 1.4 | +2.4 dB | 110 条，95% 变好 |
| Strong / Smooth | +4.6 / +4.1 dB | 各 3 条 |
| PlatformLCD gamma 1.2 | +0.4 dB | 3 条，样本少 |

未采用（验证集上不成立）：投影偏移取整到整像素（+0.3 dB，只有 57% 变好；1.4.3 查明当时的取整受浮点误差和逐字形取整干扰，改为整层平移后采用，见 10.8）、投影 σ = 大小 / 2.5（38% 变好）、`antiAliasNone` 阈值化（−0.6 dB）。

复核：
- 笔位取整：关掉后中位数 29.1 / 28.8 / 26.5 → 23.1 / 23.2 / 23.6 dB，规则成立。
- `mixed_leading_k = 0.86`：在新建文字图层上被推翻（跨行混排 26.6 / 29.7 / 32.6 → 12.1 / 26.7 / 12.8 dB）。「只对固定行距生效」「只在有缩放变换时生效」「只在 Optyca 排版引擎下生效」三个假设也都被推翻：这些结构在新建图层上全部是 k = 0 对齐（外框差 0 px）。

和 10.5 那块画板的冲突：在 10.5 的列表界面画板上重跑整板 `validate`，文字框内 PSNR 为 1.3.1 规则 38.0 dB、1.4.0 默认 19.9 dB、`--mixed-leading-k 0.86` 34.7 dB、1.4.0 的 older-file 配置（k 0.86 且关 gamma，1.4.2 已删除）39.6 dB。逐项消融显示那块画板的文字图层需要 k = 0.86，且 PlatformGray **不加** gamma（加了掉约 4.9 dB），其余 1.4.0 规则（描边、扩展等）照样有益。用同字体族、同字号、同行距、同缩放、同排版引擎在新建图层上复刻，结果仍是 k = 0 对齐，所以差别来自那份文件里文字图层本身（推测是较早版本保存的文字数据或像素），原因未确认。当时默认按新建图层的规则；根因后来在 10.7 确认。

没对齐或只部分对齐的（误差为墨水 PSNR 中位数）：

| 项目 | 误差 | 可能原因 | 建议 |
|------|------|----------|------|
| 段落文字（文本框） | 1.4.3 前约 11–12 dB | 1.4.3 起按框宽换行，见 10.8 | 中文段落仍差，见 10.8 |
| 视觉字距（optical） | 约 12–13 dB | PS 自有算法，外框宽 5–7 px | 烘焙，或设计改用度量字距 |
| 中文字体里的数字 | 1.4.3 前约 14–23 dB | `locl` 换成了等宽数字，1.4.3 已修，见 10.8 | — |
| 仿粗体 | 约 21 dB | 只是外扩近似 | 改用真粗体字重，或接受 |
| `antiAliasNone` | 约 19–23 dB | 没有通过验证的规则 | 烘焙 |
| 12–16 px 小字号 | 约 20–24 dB | PS 平台光栅化 / hinting | 接受 |
| 小字号硬投影（大小 0） | 1.4.3 前约 16–21 dB | 投影偏移按亚像素逐字形取整，1.4.3 已修，见 10.8 | 仍有残差时接受 |
| 缩放 0.5 × 效果 | 约 20 dB | 未定位 | 接受 |

**未验证**：变形文字、段落换行、渐变描边、字符水平/垂直缩放、内阴影等内部效果、macOS 上的 Photoshop、PlatformLCD 与 None 的大样本。

### 10.7 行距模型与文字 gamma 的根因（1.4.1）

在 10.5 / 10.6 那块列表画板上，文字图层要 `mixed_leading_k` 0.86 且 PlatformGray **不加** gamma 才对得上；在 Photoshop 2026 里新建的图层则是 k = 0、gamma 1.4。1.4.1 确认了这两处差别的来源，并按图层自动选择；1.4.2 删除了手动配置选项，只保留自动模式。

**行距（已确认）。** 画板文字段落的 EngineData `LeadingType` 为 1，即 `leadingAbove`（从上到上 / top-to-top）。batchPlay 描述符经常省略 `leadingType`（画板原文如此；把同一项显式设成 leadingAbove 后描述符里才会出现）。在副本上把 leadingType 改成 `leadingBelow` 后，第二行会上移约 14 px，此时 k = 0 对齐（约 46 dB）；改回 `leadingAbove` 则与原文像素一致。用 leadingAbove 新建的探针（Exo Bold、Noto Sans SC、Noto Serif）也是 k ≈ 0.86 对齐。因此混排行距的「额外下落」来自从上到上模型：`adv = leading + k × (本行字号 − 上一行字号)`（缩小同样适用）。系数 0.86 是拟合值，不是 HarfBuzz typoAscender/em（Exo 约为 1.00，对不上）。Roboto 单独拟合更接近 0.80，残差记为字体度量未完全对齐。自动行距 + leadingAbove 的公式仍未对齐（探针 r09），未采用。检测顺序：描述符 `paragraphStyle.leadingType`，缺了再读 EngineData `LeadingType`（0 = leadingBelow，1 = leadingAbove）。

**Gamma（已确认，有范围）。** 应用设置 `TextBlendGamma` 为 1.45，与颜色探针上拟合的最佳 gamma 1.5 接近（与文字颜色无关）。在 Photoshop 2026 里新建、且图层变换等比（含恒等、xx = yy）的文字，PlatformGray 加 gamma 1.4 对齐。同一套文字只要图层变换**不等比**（xx ≠ yy，包括 1.3488×1.345 这种画板常用缩放，以及 1.001×1.0 这种 0.1% 偏差），新鲜栅格化后也不再套文字 gamma，最佳是 gamma 1.0。位深 16/8、ICC、分辨率、排版引擎、均匀缩放、文字颜色都已排除。未测量：xx = yy 但带旋转/切变时是否关 gamma。

**存储像素的限制（无法从描述符判断）。** Photoshop 打开/导出时不会重画未改动的文字；磁盘上的像素反映当初写入时的环境。画板上一层恒等变换的单行文字，存储像素对齐 gamma 1.0；在本机强制重排后对齐 gamma 1.4。自动规则对「变换等比」的图层仍加 gamma，因此这类缓存像素可能对不齐；这是已知误差，没有选项可以切换。

`validate` 总是按图层应用上述规则；1.4.2 删除了 `--text-profile`（`ps2026` / `older-file`），传入会报错退出。

回归：345 张矩阵金图与 1.4.0 默认逐条 PSNR 完全一致（中位 28.26 dB，均值 28.29 dB）。那块列表画板文字框 PSNR：自动模式 37.39 dB（整板文字渲染 44.51 dB），1.4.0 默认 19.9 / 29.8。当时全局关 gamma 的 older-file 配置为 39.57 / 45.52，高约 2.2 dB，差额来自变换等比的缓存像素（例如名次「4」、部分名字行）被加上了本机文字 gamma；这无法从描述符判断，作为已知误差接受。

### 10.8 投影偏移、`locl` 与段落换行（1.4.3）

起因：参数矩阵里最差的 10 条（10.6）。逐条定位后改了三处，另有一处查明原因但没有改。规则在训练集和另做的 36 张诊断金图上确定，再用验证集和 val2 确认。诊断金图只在 Photoshop 新建的临时文档里生成。

**投影偏移（已确认）。** 1.4.2 及以前按亚像素偏移重排每个字形，再与笔位一起取整，所以同一层里各字形的投影偏移不一致（看起来「投影没对齐」）。Photoshop 把整层像素平移整数像素。证据：
- 距离 3 和距离 4（角度 120°）的两张金图投影完全相同（alpha 总和同为 733913），偏移都是 (2, 3) px。
- 把金图里的投影和本体分离后量到的偏移都是整数；本体本身是对齐的。
- 10 张硬投影诊断金图（不同角度和距离）里，只有「半数远离 0」与全部一致：−1.5 → −2，−0.5 → −1，0.5 → 1。floor(x + 0.5)、截断和银行家舍入都有不符的。
- 浮点边界：3 × cos 120° 算出来是 1.4999999999999993，直接取整会得 1，所以加了 1e-6 容差。10.6 里「取整未采用」的结论就是受了这个和逐字形取整的干扰。

**`locl` 与数字（已确认）。** HarfBuzz 默认会应用字体的 `locl`。Noto CJK 字体的默认语言系统用它把 ASCII 数字换成等宽数字。Photoshop（文字语言为英文时）画的是 cmap 里的比例数字。关掉 `locl` 后，中文字体里带数字的条目 14.3 → 32.6、18.5 → 34.8 dB，其它条目不受影响。

**段落换行（大部分确认）。** 1.4.2 及以前段落文字不换行，并把框左上角当成基线，所以文字整体上移并被裁掉。诊断金图给出：
- 首行基线 = 框顶 + 首行最高字形墨迹：Roboto 0.74、Exo 0.77、Noto Serif 0.76 em，与墨迹高度相符。
- 换行：拉丁按空格、中文按字断行，基线落在框底以下的行隐藏（基线 138.9 pt 的第 4 行在 140 pt 框内照常显示）。
- 7 个全角字（224 pt）在 221 pt 宽的框里不换行，218 pt 时换行。Photoshop 会把这一行挤进去，规则里按行末 CJK 字允许超出 0.125 em 处理。

结果：拉丁段落 10.8 → 27.5 dB；中文段落只略有提高（11.5 → 12.3、11.9 → 15.5、12.6 → 16.4 dB）。

**已知局限（未修）：**
- 中文字体的首行基线不符合墨迹规则（Noto Sans SC 0.785、Noto Serif SC 0.82、站酷快乐体 0.63 em，找不到对应的字体度量），会差 2–3 px。
- Photoshop 把超宽的中文行挤进框内（字距被压缩），这里没有复现。
- 拉丁段落用的是贪心换行；Photoshop 默认用逐行排版器（Every-line Composer），有时断行更早（如 "Claim your" 需要约 169–172 pt 的框，而贪心算法约 152 pt 就会放在一行）。矩阵里的段落恰好断得一致。
- 视觉字距（optical）：三条样本的 tracking 都是 0。实现按 PSD 里的 tracking 实际值加（tracking/1000 em），不额外加字距。差别来自 Photoshop 自己的视觉字距算法：各字对收紧得不均匀，整行窄 5–7 px。样本太少，没有拟合。建议烘焙，或让设计改用度量字距。

回归：
- 345 张矩阵：25 条变好，0 条变差，其余不变。中位 28.26 → 29.21 dB，均值 28.29 → 28.68 dB。
- 按分组：训练 29.14 → 29.45（13 条变好）；验证 28.78 不变（2 条变好）；val2 26.47 → 27.01（10 条变好）。
- 列表画板（10.7）：文字框 37.39 dB、整板文字渲染 44.51 dB，不变。

### 10.9 头像拆分与 frame 拆分（1.4.4）

Photoshop 2026（Windows）上只在脚本新建的临时文档里实测，前后已打开文档列表都为空，原设计文件未打开。

**智能对象内部访问（已实测）：**
- 临时文档里把「头像底（形状）+ 剪贴到它上面的照片 + 外框」转成智能对象并缩放到 62.5 %。`smartObjectMore.transform` 给出四角坐标，`size` 给出内部文档尺寸。
- 「编辑内容」（`placedLayerEditContents`）会把内嵌文档作为新文档打开，内层的名称、id、剪贴关系都在。对「由该层新建的文档」（导出用的临时副本）编辑内容、隐藏一侧再存回，只改这个副本，原文档逐像素不变。框 + 头像按拆分导出后重新拼合，与合并导出相比平均差 0.15（预乘 0–255），PSNR 43.6 dB。
- 「转换为图层」（`placedLayerConvertToLayers`）**不保留**智能对象的缩放：内层按原尺寸展开。再手动缩放回去，与合并导出的最大差是 201，因此不采用。
- psd-tools 读到的内层 id 与 Photoshop 编辑内容时的 id 一致，`split.hide_inner` 按 id 匹配成功。
- 端到端（`prepare` → `split-avatar` → manifest → `flatten` → `export_slices.psjs` → `build-layout` → `validate`）在自建测试文件上跑通。`split-avatar` 判为 `candidate`：拆分自洽 39.2 dB，与该层像素 33.9 dB，重采样本身的误差下限约 36 dB。

**真实设计稿（副本）上的头像（已实测）：**
- 列表行与前三名卡片里的头像都是内嵌 PSB 智能对象，内部有独立的剪贴照片层，可以拆。
- 但框侧的组内阴影（正片叠底）、线性加深且填充为 0 的描边层和内发光都叠在照片上。用 Photoshop 按拆分导出后再拼合，与合并导出只有 24.8 / 25.1 dB，差异集中在框的内缘。
- `split-avatar` 因此判为 `candidate_unverified`，并列出这些层。（1.4.5 定位到主因其实是智能对象图层自己的投影投进了框的洞里，修法后回到 42 dB，见 10.10。）
- 另有只含框的同款智能对象（不同颜色的框），可以用 `frame_from_instance`。

**frame 拆分（离线核对图层结构）：**
- 一个整张烘焙的弹窗外框（7 层，970×1042 px）被 `frame_baked_whole` 报出来。按层角色分成：
  - 外框 3 层（其中 1 层线性加深）
  - 标题栏 2 层
  - 内嵌圆角块 1 层
  - 中央装饰 1 层（颜色加深，418×418 px）
- 这正是外框九宫（标题栏视渐变决定并入或做横向三宫）+ 内嵌块九宫 + 不拉伸的居中装饰。装饰是颜色加深模式，需要按上文写 `blend_mode` 或与底下一小块一起烘焙。
- 这一份只核对了图层结构，没有在 Photoshop 里逐部件导出拼合，**未验证**像素一致性。

**未验证：**
- 链接型智能对象（读不到内层，只能换方法）。
- 带透视 / 变形的智能对象（离线映射只做仿射，`non_affine_px` > 1 判为 mismatch）。
- `frame_from_instance` 与 `pixel_mask_estimate` 在真实稿上的效果。
- `validate` 在透明画布的测试文件上会把透明区的 RGB 差计入，PSNR 很低；正常不透明画板不受影响。

### 10.10 拆开后内缘变暗：效果定位与修法（1.4.5）

Photoshop 2026（Windows）上，在设计稿副本和脚本新建的临时文档里实测（每个临时文档都不保存直接关掉；用户没有打开的文档；副本哈希前后一致）。对象是两个头像智能对象：内嵌 240×240 的内部文档，缩放到 0.75 和 0.9 倍；内部是「底 + 剪贴照片」和环形框。框侧有：包住两侧的组带两项内阴影（正片叠底、变亮）、一个线性加深且填充 0 的纯效果层（内阴影）、一个填充 0 的内发光层；智能对象图层本身有两层黑色 normal 投影（角度朝下，距离 1 和 6）。

**现象：** 按拆分导出后「框 normal 盖到内容上」，和合并导出只有 24.8 / 25.1 dB（合并图可见范围内），框内缘明显偏暗。

**定位（每行与同组合的合并导出比，dB，两个对象）：**

| 组合 | 对象 A | 对象 B |
|---|---|---|
| 全开 | 24.81 | 25.11 |
| 只关内层（组内阴影 / 线性加深层 / 内发光，逐项） | 24.9–25.6 | 24.9–26.0 |
| 内层三类全关 | 27.17 | 27.20 |
| 智能对象投影关一层 / 另一层 | 27.18 / 26.80 | 28.00 / 27.12 |
| 智能对象投影两层都关 | 29.21 | 30.11 |
| 投影关 + 组内阴影全关 | 36.62 | 46.32 |
| 投影关 + 只关组内阴影的变亮项 | 35.73 | 38.96 |
| 投影关 + 只关组内阴影的正片叠底项 | 29.32 | 30.45 |
| 投影关 + 内层三类全关 | 42.52 | 44.22 |

**像素证据：** 内容区里紧贴框内缘 6 px 一圈的平均亮度，合并导出 134.5（上缘 129.7）vs 拼回去 116.7（上缘 100.0）；另一个对象 122.7（97.5）vs 109.9（76.3）。框单独导出在这一圈里的平均 alpha 是 37（上缘 64），本应为 0：沿内缘取一列，框导出是纯黑、alpha 从 191 递减到 32，合并导出同位置就等于内容本身。只关智能对象的投影后，这一圈亮度和合并导出一致（134.3 / 129.5，122.7 / 97.5）。

**机制：** 内缘变暗来自智能对象图层自己的投影——合并时 alpha 是实心，投影只落在外侧；框单独导出时 alpha 成了环，投影投进洞里被固化进框。剩余误差来自组内阴影按环形 alpha 重画（主要是变亮项沿环内缘多画的亮边），以及线性加深 / 内发光层单独导出时和透明底混合（线性加深退化成 normal）。

**修法实测（与原合并导出比；括号内是 `split-fix` 在整张导出画布上的数字）：**

- 修法 a（`frame.png` 从合并导出裁 + 线性加深 overlay + normal 内发光 overlay，引擎按公式画）：42.25 / 42.27 dB（44.54 / 44.42）。线性加深 overlay 改按 normal 画：32.2 / 45.8（只在后者的暖色阴影上碰巧可用，不能当通用规则）；不画线性加深 overlay：34.3 / 38.9；只有 `frame.png`、没有 overlay：26.2 / 26.0。
- 投影单独导成 `shadow.png` 垫在最底（外层填充设 0、投影保留）或直接烘进 `frame.png`：两种都精确，只修投影时 29.06 / 29.95，等同于直接关投影。
- 修法 b（内缘一圈连同内容烘进框）：带占位照片烘 2 / 3 / 4 / 6 px：35.2 / 45.8 / 59.7 / 83.6（对象 A），33.1 / 40.7 / 50.9 / 76.6（对象 B）；只烘内容底色：31–34 dB。带照片 3 px 以上才过 40 dB，但等于冻结占位内容，内容会换时不能用。
- `split-avatar`（离线）在这两个对象上都报出 `outer_effect_risks`（两层投影，`frame_hole_fraction` 约 0.61）和 `edge_effect_risks`（组 → `frame_from_merged`，线性加深层和内发光层 → `overlay_part`），状态 `candidate_unverified`。

**实测中的坑：** 用 batchPlay 改某个内层的混合模式时，Photoshop 弹出「命令"设置"当前不可用」的提示框（`dialogOptions: dontDisplay` 没拦住），脚本卡住直到点掉；改用 DOM `layer.blendMode` 后不再弹框，但该层的混合模式仍改不了（读回来不变）。关单项效果用读出 `layerEffects` 描述符、把对应项 `enabled` 置 false 再 `set` 回去，读回确认。

**未验证：** 颜色减淡 / 加深等依赖内容的混合、调整图层、链接型或带变形的智能对象；overlay 部件经 `export_slices.psjs` 的自动导出（目前要另写临时脚本）；目标引擎上 reverse-subtract 的实际渲染。

1.4.6 起默认不再拆出 overlay，改为把框侧效果近似进一张 `frame.png`，见 10.11。

### 10.11 两张拆法：把框侧效果近似进 frame.png（1.4.6）

同一份设计稿副本上的两个头像智能对象（对象 A、对象 B，内部文档与 10.10 相同），只用已有的 Photoshop 导出，不再开 Photoshop。比较「只出 frame.png + 占位头像」的几种近似。换头像后的参考是公式模拟：新内容 → `frame_from_merged`（normal）→ 线性加深层 → 内发光（normal），**不是** Photoshop 重新导出，报告中已标明。

PSNR 按整张导出画布计算（括号内是合并图可见范围）。现状（框直接盖到头像上）是 27.1 / 27.3 dB（可见范围 24.8 / 25.1）。

| 方法 | A 占位 vs PS | A 换另一真实头像（模拟） | A 变亮/变暗/反相最小（模拟） | B 占位 vs PS | B 换另一真实头像（模拟） | B 变亮/变暗/反相最小（模拟） |
|---|---|---|---|---|---|---|
| (1) 从合并导出裁框，再把线性加深和内发光都按 normal 叠进框 | 34.5（32.2） | 31.8 | 29.9 | 47.9（45.8） | 42.4 | 39.6 |
| **(2) 从合并导出裁框，线性加深用最小二乘解与内容无关的 normal 颜色和 alpha，再和内发光一起烘进框** | **41.9（39.6）** | **39.9** | **38.2** | **45.2（43.0）** | **43.8** | **41.4** |
| (3) 内缘 1 / 2 / 3 px 直接从合并导出取（冻结占位照片） | 39.4 / 51.9 / 66.9 | 30.7 / 28.2 / 25.3 | 29.0 / 25.1 / 22.1 | 37.7 / 47.2 / 61.3 | 31.3 / 28.6 / 26.0 | 30.4 / 27.4 / 24.7 |
| (3b) 同样宽度但从「隐藏照片、只留底色」的合并导出取 | 37.1 / 35.5 / 32.7 | 31.4 / 29.6 / 26.8 | 30.1 / 26.8 / 23.8 | 34.1 / 32.3 / 29.7 | 34.2 / 33.8 / 31.4 | 28.9 / 27.2 / 24.3 |
| (3+2) 1 px 冻结占位内缘 + (2) 的最小二乘填洞 | 55.5 | 32.7 | 30.3 | 64.6 | 32.7 | 31.4 |

方法 (1) 在对象 B 上碰巧可用（那层线性加深是偏暖的半透明，normal 近似误差小），在对象 A 上只有 32 dB，不能当通用规则。方法 (3) 在占位内容上 dB 很高，换一个真实头像就掉到 25–31 dB，会露出一圈旧照片。方法 (2) 在两个对象上，占位内容和换头像（含变亮 / 变暗 / 反相）都在 38 dB 以上，选用它作为默认两张拆法。纯色探针的最低值是 37.6 / 41.4 dB，只作诊断，不作为是否通过的门槛。

两张拆法的门槛按这次实测定为约 35 dB（占位 vs 合并导出，以及换内容 vs 模拟都要到）；低于此再把 overlay 或保留合并交用户。精确按混合公式叠 overlay 仍以约 40 dB 为参考（10.10 的修法 a）。

**未验证：** 换内容的参考仍是公式模拟，没有在 Photoshop 里真正换一张图再导出对照；最小二乘在颜色减淡 / 叠加等强依赖内容的模式下会差得多（那些本来就交用户）；目标引擎上只画两张 src-over 的实际效果。

1.4.7：`--swap-content` 和 `representative_contents` 的 extras 在尺寸与画布不同、或占位内部含透明像素时，先裁到不透明核心（alpha ≥ 250 的包围盒），再取核心半径约 0.80 的居中近方形区域（去掉圆角透明），预乘 alpha 后用 LANCZOS 缩放到占位 alpha 包围盒，反预乘后贴回。此前把整张图（含透明四角的黑色 RGB）用非预乘 BICUBIC 拉到画布再写入占位内部，圆角黑边会漏进探针，换内容的 PSNR 会被低估；导出切片不受影响。上表「换另一真实头像」列的 dB 是旧探针算出的，这里不改写新数字。

### 10.12 固定文案：引擎能画的出属性（1.4.8）

此前「带效果的固定标签（页签名、导航名、标题）用 `art_text` 出图」。很多这类图层其实是普通可编辑文字，字体常规，效果只有描边或投影，引擎可以直接画；出成图既不能改多语言，还会给文字误加九宫边距。

1.4.8 起：

- `dynamic_text`：运行时可变文案（分数、名字、倒计时、名次），只出属性。
- `static_text`：固定文案，且只有纯色填充和描边和/或投影；只出属性，描边/投影在 `text.layer_effects`（及 `text.stroke`），`validate` / `preview` 用与动态文字相同的 HarfBuzz+FreeType 渲染器画出（投影要画，隐藏效果不画，无 Pillow 回退）。
- `art_text`：仅当存在引擎画不了的效果时出图——渐变填充或渐变叠加、斜面浮雕、变形或非仿射变换、图案叠加、绸缎、内发光、内阴影、外发光、颜色叠加、渐变/图案描边，以及已栅格化或非文字图层；未知效果同样出图。
- 三种文字类型的 `nine_slice` 必须为 null。`infer` / `build-layout` 不再对文字做九宫分析；`validate` 对带九宫的文字记 `text_has_nine_slice`。


### 10.13 拆分阈值、清单锁、复用上限（1.4.9）

起因：一次真实的两阶段运行里，`validate` 给出 `ok: true`，但实际有三处问题。第一，头像框拆开后框洞内缘带一圈暗边：`split-fix` 只有 25.4 dB，低于 35 dB 门槛，agent 自己把决定写成 `split_best_effort` 后继续导出。第二，用户确认清单后，第二阶段又加了 3 个条目，并给 13 个条目加了 `reuse_accepted`。第三，共享框的复用误差 MAE 约 4.8，约 11% 的像素差值大于 16，也被记为「已接受」。

1.4.9 的规则见上文「效果归属的默认判断」「阈值」以及第 6 节，主要四条：

- 拆分由 `validate` 按导出的 PNG 重新测量，结果写在 `split_checked`，不再采信 agent 自己报的数值。
- 清单确认后由 `confirm` 加锁。
- 复用误差只在共享图的不透明区域内计算，并去掉被上层节点盖住的像素。
- 只有记录了 `decided_by: "user"` 的决定才能解除这些问题。

回归测试使用已有产物，只重新运行 `validate`，不重新打开 Photoshop：

| 样本 | 1.4.8 | 1.4.9 |
|---|---|---|
| 列表页 A（旧运行，没有锁） | masked 47.29 dB；9 个 `group_effect_lost`、5 个 `text_has_nine_slice`、2 个 `split_avatar_not_attempted` | masked 47.29 dB；问题与左栏相同，另加 1 个 `manifest_not_locked`；没有新增复用或拆分问题 |
| 列表页 B（上面这次运行） | `ok: true`，`issues` 为空；masked 32.15 / 全图 32.35 dB | `ok: false`：7 个 `split_below_threshold`（实测 23.2–24.5 dB，附对比图）、9 个 `reuse_mismatch`（7 个共享框 MAE 4.5–5.1，10.8–11.6% 像素差值 > 16；2 行底图边缘差 1 px，其中一行大部分被导航盖住）、7 个 `outer_effect_into_hole`、14 个 `split_unverified`、1 个 `manifest_not_locked`；dB 不变 |
| 列表页 B 的清单锁 | — | 第一阶段清单执行 `confirm` 后，`check-lock` 返回 0。换成第二阶段改过的清单后，`check-lock` 和 `build-layout` 都返回 3（`manifest_changed_after_confirm`），也不会写出 layout |
| 两个头像智能对象，两张拆法（10.11） | `split-fix` 占位 41.95 / 45.36 dB，换内容最低 40.55 / 43.79 dB | 相同 |

列表页 B 的 agent 在第二阶段把 `decision` 填成了 `default`，所以不能只看 `decision` 有没有填。用户只说一句「清单确认」，也不算回答了具体的拆分或复用问题。这类问题要单独问用户，再用 `confirm --decision ID=VALUE` 记录。

**仍有缺口：** agent 如果自己写 `decided_by: "user"`，规则拦不住。好在锁会抓住确认之后的改动；确认之前写入的，只能靠用户审核清单时看到。


### 10.14 内容边缘的框色边与框洞内缘的阶梯（1.4.10）

起因：10.13 的列表页 B，用户选了两张拆法后重导，拼回去仍有两处问题。一是框洞内缘有阶梯状的暗圈；二是导出的头像图上沿有一圈浅蓝 / 紫、下沿约 5 行青色，是框的颜色。

**定位（临时副本、读层描述符 + 开关导出）：**

- 头像智能对象内部是一个穿透组，组上开着两条内阴影：一条正片叠底、角度朝下，一条变亮、角度朝上。组内是外环（一个带洞的形状层）、两个填充为 0 的效果层（内发光；线性加深 + 内阴影），以及剪贴在圆角底上的头像图。
- 合并时组的 alpha 是整块实心，内阴影只画在外缘，落在环上。只留内容导出时组的 alpha 变成头像形状，两条内阴影沿头像边缘重画，就成了那圈框色的边。头像图层本身没有效果，框色不是画在像素里的。
- 组上 `disableLayerFX` 不可用；逐项 `enabled: false` 再 `set layerEffects` 后读回为全关，重导的头像不再有那圈边（7 行头像，每张约 3200 像素改变，alpha 不变）。
- 这一圈边全部落在框不透明的区域下，所以拼回去的 dB 几乎不受影响（两种内容在框透明处与合并图的差都在 46.6 dB）。但内容图本身是错的：单独显示头像，或换一个框，就会露出来。
- 阶梯来自模拟路径：在抗锯齿带上，对模拟出来的目标逐像素拟合，解在深色（如 (3,28,85)）和亮蓝之间来回跳。用真实 Photoshop 渲染核对模拟：1.4.9 的模拟拟合报告换内容 38–40 dB，但在真实纯色渲染上只有约 27 dB，模拟明显偏乐观。

**修法与实测（PS 2026，Asia/Shanghai）：** 内容导出时关掉组效果；同一临时副本里把头像图隐藏、圆角底填 6 种纯色导出，用 `split-fix --probe` 拟合框。

| 指标 | 1.4.9 模拟拟合 | 1.4.10 探针拟合 |
|---|---|---|
| 7 行占位：拼回去 vs 合并导出（整张导出画布） | 30.2–31.2 dB（`validate` `split_checked`） | 44.3–46.8 dB |
| 换内容（留一法，真实渲染；只算内容透出区域 / `split-fix` 整张画布） | — （模拟 37.5–39.9，真实渲染上约 27） | 39.2–47.6 / 43.8–52.1 dB |
| 抗锯齿带 `aa_band_noise`（同一条带） | 2.86 | 0.33 |
| 整页（遮罩文字 / 全图） | 35.40 / 35.35 dB | 36.52 / 36.34 dB |
| `split_below_threshold` | 7 行（低于 35 dB，靠用户决定放行） | 0 行（第 8 行没有可拆的内层，另需用户决定） |
| 共享框 `reuse_mismatch` | 8 个（约 4.6% 像素差 > 16） | 1 个（第 8 行框形状不同）；第 2 行框尺寸略异，已单独导出 |

只把模拟路径改成抗锯齿带邻域合并、不用探针时，同一输入上占位 33.0 → 33.4 dB，`aa_band_noise` 2.86 → 2.16，真实换内容 27.2 → 27.6 dB：有改善，但离探针拟合很远，所以文档把探针定为首选。

**仍未覆盖：** 探针要求内容形状能单独填色（剪贴底、形状层或内容图层的 alpha）；内容本身半透明、又压在半透明框下的像素（`zone_partial_content_px`）直接取合并图；正片叠底一类与内容相关的混合仍是近似（真实渲染上留一法最低 39 dB）。

### 10.15 位置记错：效果边界取整与复制来的 layer_id（1.4.11）

起因：10.14 之后列表页 B 还剩两处：第 8 行露出一半的头像（被底部导航盖住下半）拼回去 29.94 dB；导航选中格的复用检查 2.48% 像素差 > 16（上限 1%）。

**第 8 行：** 该行头像智能对象内部与第 2–7 行同构（蓝色渐变底 + 头像 + 带斜面浮雕的底板 + 紫色描边 + 内发光层），旧清单却把它当成共享框的复用实例，把 160×166 的共享框画在 140×143 的格子上，内容又只取了头像本身。按第 2–7 行的拆法重做：内容 = 渐变底 + 头像（组效果关掉），框 = 底板 + 描边 + 内发光，自己出图（146×153），不拉伸共享框；渐变底填 6 种纯色（先关掉它自己的渐变叠加，读回确认）用 `split-fix --probe` 拟合框。实测：占位拼回 51.0 dB（导出画布），`content_check` 61.5 dB 无边圈，换内容留一法 32.0–48.4 dB（最低的是白色，误差都在内容半透明的抗锯齿边上：这个智能对象的底板在内容下面，两张拆法下那一圈只能取合并图）。`validate` `split_checked` 29.94 → 62.03 dB。

**导航选中格：** 两个原因叠在一起。
- 中间格的普通态条目复制自左格，`layer_id` 没改：`build-layout` 按左格实测的边界放，中间格画在左格上，中间那格背景缺失，复用检查在叠放位置上比，查不出来。改正 `layer_id` 后两格复用差为 0。
- 选中格和左格都是带内阴影 / 渐变叠加的形状层。导出日志里裁剪前边界 `[1894, 2265]`、裁剪后 `[-5, 366]`（宽度都是 371），按内缩算出的原点 1899，而 PNG 的内容实际从 1900 开始（`boundsNoEffects` 也是 1900）：效果边界是取整后报的，平移后取整方向变了。拼图整体左移 1 px，格子两侧竖边和压在上面的图标边缘都出差。按像素层量（先合并成像素层再量）后两格原点都是 1900，其余 73 条的边界一条没变；导航上的奖杯图标顺带变得与 PS 整图一致（不透明区 MAE 1.64 → 0.01，它不是错位，是单层带效果直接存 PNG 的渲染差）。

| 指标 | 修前 | 修后 |
|---|---|---|
| 第 8 行 `split_checked` | 29.94 dB | 62.03 dB |
| 选中格 `reuse_checked`（MAE / 差 > 16） | 0.746 / 2.48% | 0 / 0% |
| 中间格 | 没画（左格画两次） | 0 / 0% |
| 整页（遮罩文字 / 全图） | 36.52 / 36.34 dB | 55.77 / 44.89 dB |
| `issues` | 2（`split_below_threshold`、`reuse_mismatch`） | 0 |

把修前的数据重放给新检查：`layer_bounds_mismatch` 报出那个复制来的条目，`placement_offset` 报出选中格 `fits_at_shift: [1, 0]`（MAE 0.746 → 0.0）；修后两项都为 0。10.13 表里「2 行底图边缘差 1 px」很可能是同一类问题。

**仍未覆盖：** 借用别的实例图的交替状态（另外两格的选中态、右格的普通态）只能按清单的框放，PSD 里没有对应图层可比；右格普通态在 PSD 里有一个隐藏层，可以改成单独导出。
