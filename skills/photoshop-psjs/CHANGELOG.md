# photoshop-psjs changelog

维护者向的版本记录。现行切图规则只在 [references/slicing.md](references/slicing.md)；历史实测（原第 10 节 10.1–10.17，之后的接着编号）在 [references/slicing-history.md](references/slicing-history.md)。入口是 [SKILL.md](SKILL.md)。

## 1.4.15

一次新画板盲测（弹窗）暴露的交付问题，都改成机械检查（`validate` 的 `issues`，`ok:false`）：

- **用户回答之前不出图，回答之后要重导。** `export_slices.psjs`：条目用到打开时隐藏、且被未答阻塞性决定覆盖的层时，不显示、不出 PNG，只记边界（条目和日志顶层的 `skipped_pending_decision`）；每张 PNG 记 `exported_at`。`validate` 新增 `exported_before_decision`：PNG 早于覆盖它的决定拿到当前回答的时间（`png_older_than_decision`）、导出时被跳过而现在答成状态（`skipped_while_pending`）、整图早于用户记录的排除（`full_render_older_than_decision`）。只记边界的复用实例不再被 `hidden_layer_exported_without_decision` 误判（布局节点新增 `bounds_only: true`）。
- **九宫数据以确认的清单为准。** `build-layout` 不再把 `image` 升成 `nine_slice`，也不再用检测值覆盖已确认的边距；只补清单里空着的边距并记 `nine_slice_source`（`manifest` / `auto_detected` / `manifest+auto_detected`）和 `nine_slice_auto_filled`，其它检测结果只写在 `nine_slice_detected`。`validate` 新增 `nine_slice_changed_after_confirm`（对照 `manifest.confirmed.json`，`kind_changed` / `margins_changed`）和 `nine_slice_margins_missing`。
- **段落文字预览放对位置。** 段落框的左上角取 `click_point`，`box_bounds_pt` 只取宽高；框与图层实测框不相交时改按实测框放（`box_origin`）。仍是 HarfBuzz + FreeType，没有 Pillow 回退。
- **按字节复制导出器的说明补上 Python 文本模式**（Windows 上 `write_text` / `open("w")` 把 LF 改成 CRLF）。
- **标注字体支持中文。** `preview` / `validate` 按 `--label-font`、`PSJS_LABEL_FONT`、系统中日韩字体的顺序找标签字体；找不到照样画，报告 `label_font.warning` 提示。
- **状态写全。** 组件可声明 `cumulative: true`（可选 `members`、`cumulative_state`、`off_state`），`build-layout` 展开 `expanded_states.level_<N>`，`validate` 按整档出状态预览；任何状态可写完整的 `activation`。新增 `state_activation_incomplete`：声明了累积的组里，显式组合让高位亮、低位没亮。

导出器真的改了（不只是版本号），作业目录要重新按字节复制 1.4.15 的 `examples/export_slices.psjs`。

## 1.4.14

仅文档重构，行为与 1.4.13 的机械检查相同。

- 单一事实源：切图判断规则、schema、validate issue 列表只写在 `references/slicing.md`；`SKILL.md` 第 9 节收成带完成标准的薄流程。
- 每一步有可检查的完成标准（准备材料、写清单、审核门闩、导出、布局、校验与收尾）。
- 履历外置：第 10 节（10.1–10.17）历史实测移到 `references/slicing-history.md`；1.4.8–1.4.13 说明与更早的版本记录整理到本文件。
- description 收成两条触发：用独立 `.psjs` 驱动 Photoshop，以及切图工作流。

自带导出器的 sha 会变（仅 `EXPORTER_RELEASE` 版本字符串从 1.4.13 改为 1.4.14）。作业目录必须用 1.4.14 的 `examples/export_slices.psjs` 重新按字节复制，不能继续用 1.4.13 的副本。

## 1.4.13

隐藏层的「问没问用户」落到机械检查上，都进 `validate` 的 `issues`（`ok:false`）：

- **`uncertain_hidden` 只是索引，问题要写成决定。** 吃不准就写 `hidden_layer_state`（带 `layer_ids`、`question`、`options`，建议附 `crops`）；`uncertain_hidden` 可以留作给人看的索引。`validate` 遍历清单 JSON 里组件、状态、部件和顶层的 `uncertain_hidden`，其中的 layer id 没被任何阻塞性 `reuse_decisions` 条目的 `layer_ids` 覆盖，记 `uncertain_hidden_without_decision`（带 `layer_ids`、`component_id`、`state`、`part`、`question`）；已覆盖但未答的只报 `pending_user_decision`，不双报。`flatten` 的摘要里也会列出。不解析审核 markdown。
- **采用隐藏层作为状态要用户点头。** 导出图条目（有 PNG、不是只记边界的复用实例、不是属性文字）的 `layer_id` / `select_ids` 里有 `layers_at_open` 中打开时隐藏（自身或祖先）的层，而覆盖它的阻塞性决定没有用户经 `confirm --decision` 记录的「作为状态导出」类回答（`state`、`export`、`include` 等），记 `hidden_layer_exported_without_decision`（`reason`：`no_decision` 或 `user_answer_is_not_export_as_state`）。覆盖它的决定还没答的交给 `pending_user_decision`。例外：`highlight` 部件且同一组件默认态里有打开时可见的底。没有 `layers_at_open` 的旧日志跳过这条。
- **复制导出器按字节复制；快照耗时进日志。** 文档写明复制方式（Windows 上用 `shutil.copy2` / `Copy-Item` / `cmd /c copy`，不要用 PowerShell 5 的 `Get-Content` / `Set-Content` 转存）；`export_slices.psjs` 记 `layers_at_open_ms` / `layers_at_open_count` 和 `full_render.hidden_at_render_ms`，只为排查，不新增 issue。

## 1.4.12

三处让校验分数「对着改过的参考图」偏高的漏洞，都进 `validate` 的 `issues`（`ok:false`）：

- **问题必须是结构化决定，「清单确认」不是回答。** 会改变导出内容的问题（可见层是否当前设计 `layer_inclusion` / `exclude_visible_layer`、隐藏层是否交替状态 `hidden_layer_state`、或带 `affects: "truth" | "export"` 的任意类型）没有用户本人经 `confirm --decision ID=VALUE` 记录的回答时，记 `pending_user_decision`。`confirm` 把每次 `--decision` 追加进锁文件的 `decision_log`，手写的 `decided_by: "user"` 不在日志里，仍算未答。一句「清单确认」照样锁清单，`not_user_decided` 列出全部未答条目（不限复用和拆分）。
- **可见层不得从真值里除掉；导出器不得改。** `export_slices.psjs` 在打开文档后、任何临时显隐前记下 `layers_at_open`，导整图前再记 `hidden_at_render`；不是用户记录的排除却被隐藏，记 `visible_layer_hidden`。画板内打开时可见、有像素、没被任何条目（`layer_id` / `select_ids` 或被选中的祖先组）覆盖、也没被用户排除的图层，记 `visible_layer_unassigned`。导出日志里的导出器 sha256（统一换行、去掉 `CONFIG_URL` 那一行后计算）与 skill 自带脚本不一致、缺失，或 `script_path` 上的文件现算不一致，记 `exporter_modified`；这只防「复制后改两行」，不是防篡改。
- **多图层条目按并集比边界。** 合并多层的条目（`select_ids` 多于一层）清单框是并集；导出器记 `select_bounds` 和 `logical_bounds_union`，`layer_bounds_mismatch` 用并集比较。旧日志只有 `layer_id` 那一层时，实测框落在清单框内（容差 2 px）视为并集的一部分；框在别处（兄弟实例误用 `layer_id`）照报。

## 1.4.11

两处「图画对了、位置记错了」的问题，都在 `validate` 的 `issues` 里报：

- **按像素量位置，不按实时效果量。** 单个带图层效果的图层，Photoshop 报的效果边界是取整过的；裁掉透明边后边界平移再取整，可能取到另一边，trim 内缩算出的原点就差 1 px（PNG 本身是对的）。`export_slices.psjs` 先给临时副本加一个空图层再合并可见图层，在像素层上量边界（`measured_on_raster: true`）；合成结果和原来一样，单层带效果直接存 PNG 时偶尔与 PS 整图不一致的情况也一并消失。`validate` 的 `placement_offset`：每张图自己的不透明像素（去掉被上层盖住的、文字和无法模拟的效果区域）在 ±2 px 内与 PS 整图配准，挪一下明显更吻合（MAE 至少减半且降 0.5 以上）就报，并给出 `fits_at_shift`。正常混合、100% 不透明的图和按原尺寸画的九宫格才查。
- **条目的图层要真的在它的框上。** 复制兄弟条目时 `layer_id` 没改，`build-layout` 按该图层实测的边界放，结果兄弟被画两次、自己那一格没画。`build-layout` 比较清单的 `logical_bounds` 和该图层实测的逻辑边界，差超过 2 px 记 `layer_bounds_mismatch`（默认态和自己导出图的条目都报）。非默认态、借用别的实例图（`export_png: false`）、PSD 里本来没有这一层的交替状态，改按清单的框放（`placed_from: manifest_logical_bounds`），不报。

## 1.4.10

拆分框与内容时两处修正，都按「效果归属」处理：

- **内容不能带框色的边。** 包住框和内容两侧的组（或智能对象内层的组）带的内阴影 / 内发光 / 描边 / 斜面浮雕，在只留内容导出时会按内容自己的 alpha 重画，在内容边缘画出一圈框色的边（设计里这些效果画在整组外缘、落在框上，不碰内容）。这些效果和内容无关，**归框**：导出内容时关掉（组用逐项 `enabled: false`，读回确认），框里保留。
- **框洞内缘的抗锯齿带不逐像素硬解。** 用公式模拟的目标逐像素拟合，低 alpha 的边缘像素会在深色和亮色之间来回跳（阶梯 / 暗圈）。首选 `split-fix --probe`：同一临时副本里把内容像素隐藏、内容形状填成几种纯色（黑、白、中灰、再加彩色），每种导出一次合并图；框在这些**真实渲染**上拟合，换内容用留一法在真实渲染上验证（`swap_is_real_render: true`）。没有探针时，模拟路径在抗锯齿带上把最小二乘的方程按邻域合并求解（`aa_band_smoothed`），结果标为模拟。
- `split-fix` 另报 `aa_band_noise`（边缘带的颜色跳变，只作诊断）和 `content_check`（框完全透明处，内容导出和合并图不一致时 `rim_suspect: true`，不算通过）。

## 1.4.9

三条护栏，问题一律进 `validate` 的 `issues`，agent 不能靠自己写的「接受」标记消掉：

- **效果归属默认判断**：框与它框住的动态内容（头像、标题文字、徽章）叠在一起时，内容外侧、不依赖内容的外部效果（投影、外发光、外描边）默认归框，烘进框、不挂在内容上、不漏进洞里；做法不限（`split-fix` 两张近似只是工具之一）。拆完拼回去低于 `split_match_psnr`（默认 35 dB）时：先套这个默认判断 → 再逐层关效果定位选修法 → 仍低于就**停下**写 `split_avatar` 决定交用户。`validate` 按导出的 PNG 实测（`split_checked`），低于阈值记 `split_below_threshold`，直到该决定记着用户本人的回答（`decided_by: "user"`，用 `confirm --decision ID=VALUE` 记录；agent 抄 `default`、一句「清单确认」都不算）；没有 Photoshop 实测 dB 的 `candidate_unverified` 记 `split_unverified`。
- **清单锁**：用户确认后 `confirm` 锁定清单；没锁记 `manifest_not_locked`。锁定后不得再改，要改就写 `manifest.proposal.json` 停下等用户重新确认。
- **复用误差上限**：共享图不透明区域内（去掉被上层节点盖住的像素）MAE ≤ `reuse_max_mae`（默认 4）且差值 >16 的像素 ≤ `reuse_max_pct_gt16`（默认 1%），超过是 `reuse_mismatch`；part 上的 `reuse_accepted` 不算数，只有 `reuse_decisions` 里记着用户回答（`decided_by: "user"`）的才算。超限先怀疑复用判断并拆开。

阈值放在项目规则文件的 `thresholds` 里可改（见 slicing.md「阈值」）。

## 1.4.8

固定文案仅在引擎画不了的效果（渐变填充/渐变叠加、斜面浮雕、变形、图案叠加、绸缎、内发光、内阴影、外发光、颜色叠加等，以及已栅格化文字）时出图（`art_text`）；只带纯色填充和描边和/或投影的固定文字改为 `static_text`，只出属性由引擎绘制。三种文字类型都不得带九宫边距。

## 1.4.7

`split-fix` 的 `--swap-content` 和代表性内容按不透明核心拟合，不再把整张含透明边的画布拉到目标尺寸。

## 1.4.6

头像/框拆分默认只出两张：占位内容 + 一张把框侧效果近似进去的 `frame.png`（普通混合）。低于约 35 dB 时再把 overlay 或保留合并交用户。

## 1.4.5

拆开后拼回去对不上时，先在临时副本里逐层关效果定位，再决定要不要交用户。可用「从合并导出裁框」加上混合模式 overlay。

## 1.4.4

头像与框在 PSD 里合在一起时，agent 先按可靠性自己拆，拆不开再问。面板、弹窗、卡片、按钮底等 frame 类元素默认按视觉层次拆成九宫 / 三宫 / 装饰部件。

## 1.4.3

文字预览：投影按整像素平移整层（半数远离 0）；不应用字体的 `locl`（中文字体里的数字用比例字形）；段落文字按框宽换行。

## 1.4.2

删除 `--text-profile`，只保留按图层自动选择的文字规则；再传入该参数会报错退出。

## 1.4.1

按图层自动选择混排行距系数和文字抗锯齿 gamma：`leadingType` 为 `leadingAbove` 时系数取 0.86，否则取 0；图层变换不等比时不加 gamma。

## 1.4.0

文字预览规则在 Photoshop 2026 新建文字图层的金图上拟合并用验证集确认（混排行距、抗锯齿 gamma、描边与填充分开合成、外发光范围、仿粗体/仿斜体近似）。

## 1.3.1

文字预览没有回退渲染器；所需的包缺了就报错退出。

## 1.3.0

属性文字预览改用 HarfBuzz + FreeType（或 Skia），并修正文字属性的读取（行距与字号乘变换、样式片段、效果总开关）。

## 1.2.0

Repeated elements are no longer always exported per instance. The agent decides reuse from UI purpose, game-UI priors and evidence in `reuse_candidates.json` (pixel similarity, smart-object id, name pattern, `ui_role_prior`); reused instances share one image (`reuse_of`) but keep their own bounds. Instances that do not match the design are split; conflicts with strong evidence go to the user as `reuse_decisions` with comparison images. Avatar pictures are runtime placeholders kept separate from their frames. `validate` reports `reuse_mismatch`.

## 1.1.0

Group layer effects can be either baked into the slice (`effect_mode: "baked"`, default) or drawn by the engine (`effect_mode: "engine"` + `engine_effects` parameters read by psd-tools); the agent picks per part and the review preview shows the choice. `validate` approximates engine drop shadows / outer glows, masks effects it cannot emulate, flags parts that silently drop a group effect, and writes a final annotated preview rendered from the exported PNGs (`validate/preview_exported_annotated.png`, plus a side-by-side with the PS render).

## 1.0.0

Standalone repository; first marketplace release.
