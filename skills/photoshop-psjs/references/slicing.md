# Agent 切图工作流

助手只准备材料；跑这个 skill 的 **agent**（大模型）阅读材料后编写 manifest（组件、部件、状态、规范名）。用户改 JSON 确认后，再由 `.psjs` 按 `layer_id` 导出 1x PNG，写出引擎无关的布局 JSON，并校验默认状态拼图。启动、守卫、`silent` 等规则见 [SKILL.md](../SKILL.md)。模板：[examples/export_slices.psjs](../examples/export_slices.psjs)、[examples/slicing_helper.py](../examples/slicing_helper.py)。

## 决策树

按这个顺序做，**确认前不要导出**。

1. **打开材料。** 先看 `full_render_preview.png`（若已有 Photoshop 导出的 `full_render.png`，以它为准），再对照 `crops_sheet.jpg`、`crops/<layer_id>.png` 和 `group_effects.json`。用 `crops_index.json` 把 id 对回 `tree.json`（可见性、路径、文本、效果）。不要猜测 id，也不要去读不存在的裁切图。选导出目标时，检查该元素最近的父组（或代表该元素的组）上有没有图层效果（如投影）：效果在组上、内层叶子没有时，按下文「组效果：烘焙还是引擎绘制」给该部件选 `effect_mode`——`baked` 导出该组（默认），`engine` 导出子层并把组的 `effect_params` 写进 `engine_effects`。不要因为 `crops/` 里没有该组缩略图就只退回叶子而不记录效果。
2. **识别组件。** 把页签组、底栏、按钮、列表行等交互控件收成组件，按槽位拆开，不要按 PSD 分组原样切开。
3. **分清共用零件和随状态变化的零件。** 图标、始终存在的底等跨状态不变的，放进该组件的 `shared`，文件名不加状态后缀。槽/底的明暗、选中高亮、只在某一态出现的标签等，放进 `states.<state>.parts`。
4. **区分状态变体和旧稿。** 主体/内容不变、只改明度、描边、发光、配色或缩放的，是状态变体，可以导出。同一槽位但换了内容或徽记的，当旧稿忽略。内容或母题对不上、吃不准时才写 `uncertain_hidden`，并附上两侧裁切图。
5. **只给会随状态变化的零件记 missing。** 以同组里状态最完整的那一项为模板（例如某选中项有 base + highlight + label），每个缺少其中一件的兄弟项都要列出，**包括 highlight**。不要因为共用图标没有另一态就标 missing。单个特殊样式的列表行（例如 self 高亮行）只是这一行的一个实例：不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都有成套变体。
6. **判断重复元素是否复用（见下文「重复元素：复用还是分开」）。** 先按 UI 用途和常识（游戏 UI 先验）加上 `reuse_candidates.json` 的证据，从逻辑上判断是否该共用一张图；该复用就用 `reuse_of` 指向同一张共享图。再对照效果图：复用后与设计对不上，先怀疑分析错了，只把对不上的实例拆出去单独导出；逻辑证据很强却对不上，写进 `reuse_decisions` 交用户决定。头像图与头像框必须分开；PSD 里合在一起的，写进 `reuse_decisions` 请用户决定，不要合着导出。
7. **写好审核清单后停下。** 跑 `flatten` + `preview`，`status` 保持 `pending_review`，等用户改 JSON 确认后再导出。
8. **确认后一口气跑完，不要停在导出。** 导出 → `build-layout` → `validate` 是一个整体；`export_log.json` 写出不算完成，只有 `validate/validate_report.json` 写出并读过 `issues`（含 `group_effect_lost`）、遮罩 PSNR / ΔE 后才算完成。`validate` 的最后一步用**实际导出的 PNG** 拼出默认态并画上标注，写出 `validate/preview_exported_annotated.png` 与 `validate/preview_exported_vs_ps.png`（左 PS 整图、右导出拼图+标注）；把这两张图作为最终成果交给用户。第 7 步导出前的审核预览不变。若 `issues` 里有 `reuse_mismatch`，按第 6 步处理（拆出单独导出，或交用户决定）后重跑。每条长命令都设超时并把输出写进日志；某步超时或报错时，读日志、修正后重跑这一步，不要静默结束。

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
3. **有逻辑证据却对不上时，不要默默决定。** 写进清单顶层 `reuse_decisions`，附对比图（每个实例：设计裁切 | 共享图 | 差异）、问题描述和证据，交用户决定。决定前默认单独导出，并标明待决定。用户确认按复用处理的实例写 `reuse_accepted: true`，校验时差异改记在 `reuse_mismatch_accepted`，不再算问题。

**游戏 UI 常见先验**（默认判断，像素对照可以推翻，冲突交用户）：

- 列表 / 排行 / 背包的行底：很可能是一张复用的模板，另有少量特殊变体（自己那一行、高亮行等）。
- 头像框：很可能复用。**头像图本身是运行时动态内容**，要和框分开：单独一个 part，`role: avatar`、`placeholder: true`，导出 PSD 里的示意图作占位，绝不和框烘焙在一起。PSD 里头像和框在同一层或合并的智能对象里时，在 `reuse_decisions` 里标为需要拆分/决定（`type: split_avatar`），不要合着导出。
- 道具/图标格、同款按钮、按名次的奖牌/徽章（少量固定集合）、货币图标、进度条的底和填充：典型的可复用资源。
- 玩家名、分数、数字：`dynamic_text`。

`reuse_candidates.json` 里每组带 `evidence.ui_role_prior`（`type: ui_role_prior`，按图层名/路径关键词猜的角色和先验：`likely_reuse`、`small_fixed_set`、`dynamic_placeholder`），以及 `avatar_merge_suspects`（疑似头像与框合并的层）。这些只是证据，由 agent 判断。

助手子命令：

```text
python slicing_helper.py prepare --psd design.psb --out-dir out [--artboard <画板名或id>]
python slicing_helper.py flatten --manifest out/manifest.json
python slicing_helper.py preview --manifest out/manifest.json --out out/preview_annotated.png [--ref out/full_render.png]
python slicing_helper.py build-layout --manifest out/manifest.json --export-log out/export_log.json --slices-dir out/slices --out out/layout.json
python slicing_helper.py validate --layout out/layout.json --slices-dir out/slices --full-render out/full_render.png --out-dir out/validate [--fonts-dir <字体目录>] [--text-renderer auto|freetype|skia|pillow]
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
# 可选：校验预览里的动态文字（HarfBuzz 排版 + FreeType 填充轮廓）
pip install uharfbuzz freetype-py
# 可选：--text-renderer skia 时再装
pip install skia-python
# 之后一律用该环境的解释器调用助手，例如：
#   .venv\Scripts\python.exe examples/slicing_helper.py prepare ...
#   .venv/bin/python examples/slicing_helper.py prepare ...
```

若工作区附近已有装好这些包的 venv，直接用它的 `python`，不必再装一份。需要的包：`psd-tools`、Pillow、numpy；预览合成与九宫格分析还要 `scipy`（已包含在 `psd-tools[composite]` 里）。`uharfbuzz` + `freetype-py` 可选，只影响 `validate` 里动态文字的预览；没装时退回 Pillow，`validate_report.json` 的 `text_renderer.fallback_reason` 会写明，不会静默降级。

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
6. 底图、高亮、图标、文字始终是独立 `parts`。类型仍是 `image` | `nine_slice` | `dynamic_text` | `art_text`。可变文案（分数、名字、倒计时、1–2 位名次）用 `dynamic_text`（只出属性，不出图）；带效果的固定标签（页签名、导航名、标题）用 `art_text`（出图）。
7. 重复元素按「重复元素：复用还是分开」处理：逻辑上该复用的导出一张、各实例用 `reuse_of` 引用；与设计对不上的拆开；有证据却对不上的交用户决定。头像图与头像框分开。全部 1x。

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

`type` 为 `reuse` 或 `split_avatar`（头像与框合在一起，需拆分）。`decision` 为空时 `flatten` 会在 `pending_reuse_decisions` 里列出；未决定的按 `default` 导出。

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

部件（`part`）常用字段：`name`（规范文件名）、`role`（`base`、`highlight`、`icon`、`text` 等）、`kind`、`layer_id`、`select_ids`、`hidden`、`nine_slice`、`text`、`logical_bounds`、`source_name`、`source_path`。重复元素复用时加 `reuse_of`（共享图名，不带实例序号）；用户确认接受差异时加 `reuse_accepted: true`。运行时内容的占位图（如头像）加 `placeholder: true`。父组带效果时再加：`effect_mode`（`"baked"` | `"engine"`）、`effect_reason`（一句话理由，审核时可见）、`engine_effects`（仅 `engine`，从 `effect_params` 抄）。

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

`dynamic_text` 的 `text` 在导出时由 Photoshop 补全（内容、字体 PostScript 名、字号、颜色、描边、对齐、`click_point` 为相对整份文档的百分比；从 1.3.0 起还有样式片段、变换缩放后的像素字号和行距、字距方式、抗锯齿方式、图层效果及其开关，字段见第 8 节「dynamic_text 文字属性」）。

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
                      "kind": "art_text",
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
                      "kind": "art_text",
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
                      "kind": "art_text",
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
                      "kind": "art_text",
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
                      "kind": "art_text",
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

要点：高亮是 `tab_group` 上的共享部件，没有和某个页签的文案烤在一起。导航图标若在各状态下不变，放在该 `nav_item` 的 `shared` 里，文件名不加 `_selected` / `_normal`。`missing` 只记录按设计会变却没画出来的零件（例如某项没有 selected 底或高亮），不要因为「没有另一态的图标」去标共用图标。忽略旧稿不会让共用图标变成 missing。`flatten` 之后，隐藏的状态部件会带 `hidden: true`，导出脚本会临时显示再恢复；共用部件会出现在每一个状态的预览里。

---

## 6. 审核门闩

确认前不要导出。

1. 用 `flatten` 把组件树展成 `entries`，再用 `preview` 画标注预览（框出每个切图、标规范名和九宫格线；来自隐藏态的条目会计入标题；有 `effect_mode` 的标 `fx:baked` / `fx:engine(效果类型)`，`engine` 再用细品红框画出估计的效果范围）。`flatten` 输出里的 `effect_issues` 必须为空。
2. 把 `preview_annotated.png` 和 `manifest.json` 交给用户。用户改 JSON：改名、改类型、改九宫格、拆/合并部件、确认或删除 `missing`、把不该导出的隐藏层拿掉、切换 `effect_mode`、对 `reuse_decisions` 填 `decision`（看对比图）。
3. `status` 在用户确认前保持 `pending_review`，确认后改为 `confirmed`。导出脚本拒绝其它状态（测试授权下允许 `pending_review`）。
4. 用户确认后才进入导出。

---

## 7. 导出

[examples/export_slices.psjs](../examples/export_slices.psjs) 只硬编码一个 `CONFIG_URL`，从作业 JSON 读路径（见 [export_config.example.json](../examples/export_config.example.json)）。按 `layer_id`（及 `select_ids` 里的剪贴层）导出 1x PNG，图层效果烘焙进图；带 `reuse_of` 的实例只导出一张共享图（见下）。`dynamic_text` 不出图，只写文本属性；`art_text` 出图。

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
- `nodes`：扁平列表，便于引擎遍历和校验放置。每条含 `name`、`kind`、`component_id`、`part_role`、`state`、`default`、`hidden`、`visible_bounds`（含烘焙效果，画板坐标）、`logical_bounds`（不含效果）、`anchor`（逻辑框中心）、`pivot`（0.5, 0.5）、`nine_slice`、`image`、`text`。有 `effect_mode` 时附 `effect_mode`、`effect_reason`；`engine` 还附 `engine_effects` 和 `effect_bounds`（逻辑框按各效果的距离/角度/大小外扩的**估计**范围，`effect_bounds_estimated: true`，不是 PS 的精确框），此时 `logical_bounds` = `visible_bounds` = 图片框。`components` 里对应的 part 也带这些字段。
- 复用：带 `reuse_of` 的节点 `image` 都是 `<共享名>.png`，九宫格取源实例；非源实例的 `visible_bounds` = 源实例可见框按两者逻辑框原点的差平移。顶层附 `reuse_decisions`。
- `effect_groups` / `effect_issues`：参与检查的带效果组，以及组效果被悄悄丢掉的部件（应为空）。
- 坐标是画板像素（1x）。各引擎导入器自己写，不在本 skill 里。

### dynamic_text 文字属性（`text`，`text_attrs_version: 2`）

`export_slices.psjs` 的 `parseText()` 从图层描述符写出，`build-layout` 原样带入并补派生字段（`text_render.complete_text_attrs()`；只加不删）。Python 侧 `text_render.text_attrs_from_descriptor()` 与它逐字段一致，可用于已持有 batchPlay 描述符的流程。

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

1. **默认状态**：只拼 `default: true` 的部件（即 PSD 里当前显示的那一态，加上始终可见的 chrome），用 src-over（`Image.alpha_composite`）叠回画板，与 Photoshop 整图比像素差 / CIEDE2000 ΔE。同时报告画上动态文字近似后的数字（`diff_text_rendered`）、以及遮罩动态文字区域后的数字（`diff_text_masked`），并列出误差最大的 10 条。再检查漏切、重名、空图。**判断切图精度只看遮罩后的指标**；带文字的数字只说明预览和效果图有多像。
2. **引擎效果**：`effect_mode: engine` 的部件，投影 / 外发光按 Pillow 近似画在部件下面（alpha → 按角度/距离偏移 → 扩展后高斯模糊 → 颜色/不透明度，普通混合），报告列在 `engine_effects_emulated`。描边、内阴影、叠加等无法模拟的，按估计的 `effect_bounds` 把外圈（内部效果则整框）从遮罩指标里去掉，列在 `engine_effects_unemulated`。`issues` 里的 `group_effect_lost` 表示选了带效果组的子层却既没烘焙也没写 `engine_effects`，必须修。
3. **复用实例**：每个带 `reuse_of` 的默认态节点，在其可见框内（去掉动态文字与无法模拟的效果区域）比 PS 整图与拼图。MAE > `--reuse-tol-mae`（默认 4）或差值 >16 的像素占比 > `--reuse-tol-pct`（默认 1%）时记为 `reuse_mismatch`（含实例名、区域、指标和 `validate/reuse/<名>.png` 对比图）；`reuse_accepted` 的改记 `reuse_mismatch_accepted`。逐实例数据在 `reuse_checked`。
4. **交替状态**：对每个交互组件的非默认状态（含仅存在于 `missing` 中的状态），按「共用部件 + 该状态部件」拼图，互斥组的兄弟项切到对应态，写出 `validate/states/<component_id>_<state>.png`。只对真正缺失的、按设计随状态变化的零件画红框并标 `missing`。这些预览**不**和默认态整图比 PSNR（对不上是正常的）。
5. **导出后标注预览（最终交付）**：用实际导出的 PNG 按布局 JSON 拼默认态（同上，含引擎效果近似），再按审核预览的样式画标注：每个切图按 `visible_bounds` 画框、规范名、九宫格线、类型和 `fx:baked` / `fx:engine`，复用实例的标签后缀为 `→ <共享名>`。写出 `validate/preview_exported_annotated.png` 和 `validate/preview_exported_vs_ps.png`（左 PS 整图、右导出拼图+标注），路径记在报告的 `exported_preview`。把它们交给用户。

6. **动态文字预览**（[examples/text_render.py](../examples/text_render.py)）：只是近似，不是 Photoshop 的文字引擎，也不是目标引擎的渲染。要像素一致的文字，让 Photoshop 栅格化文字图层（或临时显示后导出带字整图）做底图；要和上线一致，用目标引擎按同一份布局 JSON 出图。

   - `--text-renderer auto`（默认）：装了 `uharfbuzz` + `freetype-py` 用 `freetype`（HarfBuzz 排版，FreeType 不做 hinting、直接填充轮廓，描边用圆角外扩的轮廓描边）；否则有 `skia-python` 用 `skia`（Skia 轮廓路径）；都没有就退回 `pillow`。也可显式指定。报告 `text_renderer` 记 `requested`、`used`、`fallback_reason`、参数和逐条退回；逐条细节在 `dynamic_text_render_info`（所用字体文件和匹配方式、是否开 kern、画了哪些效果、因总开关或单项关闭而跳过的效果、没画的效果和近似项）。
   - 参数解读：字形在文字空间（pt）排版后整体乘图层变换，字号和行距随之缩放；按样式片段取字体、字号、颜色和行距，不是整层一个字号；行距取该行最大值（自动行距 = `auto_leading_pct` × 字号，默认 1.2）；`effects_visible` 为 false 或效果 `enabled: false` 时不画；`kerning: manual` 且没有手动字距对时关闭 OpenType `kern`，`metrics` / `optical` 时打开（optical 只用字体字距对近似）；`tracking` 按 tracking/1000 em 加到每个字形；描边按圆角外扩轮廓，投影按距离、角度、大小、扩展画在下面，外发光同理；颜色叠加改填充色。
   - **经验值，未广泛验证**：`--mixed-leading-k`（默认 0.86）——后一行最大字号比上一行大时，PS 会把这一行再往下推，约为字号增量 × k。只在少量两行混排样本上拟合，不是 PS 公开规则；换字体或排版对不上时先调这个值。`--snap-glyph-origin`（默认开，`--no-snap-glyph-origin` 关闭）——把每个字形的笔位取整到整像素再光栅化；在实测样本上明显更接近 PS（见 10.5），但同样只是观察，不是文档化行为。
   - 不支持、会记入 `approximations` 的：变形文字、段落文字自动换行、仿粗体/仿斜体、字符水平/垂直缩放、渐变描边、非正常混合模式、颜色叠加以外的内部效果。找不到字体时用后备字体并标出（`fonts[*].match: fallback`），这时文字误差没有参考价值。`--fonts-dir` 指向设计所用字体（Windows 用户安装的字体通常在 `%LOCALAPPDATA%\Microsoft\Windows\Fonts`）。

`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。预览文字复现不了 PS 自己的光栅化、混合模式和效果算法，带文字的指标残差集中在文字本身属于预期。

---

## 10. 实测（Windows / Photoshop 2026）

对象：一份多画板 UI PSB 的**副本**（未改、未保存、未关闭用户可能打开的原文档或其它未指定文档）。实测画板是一个 1080×2340 的列表界面，65 个可见元素。时间均为 Asia/Shanghai（UTC+8）。项目词表不进技能仓库，回归时用仓库外的 `--rules` 文件。

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
| Pillow 退回路径（正确参数，无排版） | 26.4 dB | 21.1 dB |

整板 `validate`（同一批切图，`diff_text_rendered` 为整图 PSNR；文字框内为只算动态文字框）：

| 运行 | `diff_text_rendered` | 文字框内 | `diff_text_masked` |
|------|----------------------|----------|--------------------|
| 1.2.0（Pillow） | 25.07 dB | 15.3 dB | 47.29 dB |
| 1.3.0，旧导出日志（v1 文字属性） | 26.63 dB | 17.0 dB | 47.29 dB |
| 1.3.0，v2 文字属性，`freetype` | **44.83 dB** | **38.0 dB** | 47.29 dB |
| 1.3.0，v2，`skia` | 44.21 dB | 36.8 dB | 47.29 dB |
| 1.3.0，v2，`pillow` | 34.14 dB | 24.4 dB | 47.29 dB |
| 1.3.0，v2，`freetype --no-snap-glyph-origin` | 37.00 dB | 27.5 dB | 47.29 dB |

要点：

- 误差主要来自参数读错，而不是光栅化算法：行距没乘变换（45 px 被当成 33.5 px）、两行混排只用一个字号、被总开关隐藏的描边照画。
- 混排行距系数 0.86 是在这 5 条两行样本上拟合的；取 0.75 或 1.0 时逐条 PSNR 掉到约 37 dB。
- 本样本的手动字距为 `manual` 且没有字距对；打开 `kern` 反而掉约 2 dB。
- v2 文字属性取自同一批图层描述符：用 `text_render.text_attrs_from_descriptor()` 解析，与新版 `export_slices.psjs` 的 `parseText()` 在全部 19 条上逐字段一致（Node 下对照）。这一轮没有重跑 Photoshop 导出。
- 遮罩后的 47.29 dB 不变，说明改动只影响文字预览，不影响切图校验。
- **未验证**：中文与 CJK 标点挤压、段落文字、变形文字、其它抗锯齿方式、仿粗体、渐变描边、macOS 上的 Photoshop 光栅化。笔位取整和混排行距系数都只在这一份样本上成立。

