# Photoshop Agent Skills

Agent skills that let an AI coding agent drive **Adobe Photoshop** through standalone
**UXP scripts (`.psjs`)**: write a script, launch it, poll a result file, and verify the
output, without building a UXP plugin or an MCP bridge.

Skills follow the open [Agent Skills](https://agentskills.io/specification) format
(`SKILL.md` + supporting files), so they work with Claude Code, Codex, Cursor, OpenCode,
OpenClaw and other agents that load `SKILL.md`.

## Skills

| Skill | What it does |
| --- | --- |
| [`photoshop-psjs`](skills/photoshop-psjs/SKILL.md) | Create, edit and export Photoshop documents from an agent with `.psjs` scripts: `executeAsModal`, `batchPlay`, file tokens, doc-id safety guards, launch/poll recipes for Windows and macOS. Includes an agent-driven **UI slicing workflow**: the agent reads a PSD, writes a component/state manifest for review, exports 1x PNG slices (nine-slice, baked effects, interaction states), builds an engine-agnostic layout JSON, and validates by recompositing against the full render. |

## Requirements

- Adobe Photoshop **2023 (v23.5) or later** (verified on Photoshop 2026), Windows or macOS.
- An agent that can run shell commands on the machine where Photoshop is installed.
- For the slicing workflow (optional): Python 3 with `psd-tools`, `Pillow`, `numpy`
  (`scipy` for some gradient composites).
  `validate` also **requires** `uharfbuzz` + `freetype-py` (or `uharfbuzz` + `skia-python`
  with `--text-renderer skia`) when the layout has dynamic_text or static_text:
  `pip install uharfbuzz freetype-py`. Without them it exits with an error before drawing
  any text; there is no fallback renderer.

## Install

**skills.sh / Vercel `skills` CLI** (installs into Claude Code, Cursor, Codex, OpenCode and more):

```bash
npx skills add younglark/photoshop-skills
# or just this skill, globally
npx skills add younglark/photoshop-skills --skill photoshop-psjs -g
```

**ClawHub / OpenClaw:**

```bash
clawhub install photoshop-psjs
```

**SkillsMP:** listed automatically from this public GitHub repo; install with either command above.

**Manual:** copy `skills/photoshop-psjs/` into your agent's skills directory
(e.g. `~/.claude/skills/photoshop-psjs/`).

## Layout

```text
skills/
  photoshop-psjs/
    SKILL.md                 # entry point: when to use, launch/poll recipes, safety rules
    CHANGELOG.md             # version history (maintainer-facing)
    references/
      batchplay.md           # batchPlay descriptor recipes
      slicing.md             # agent slicing workflow, manifest schema, validation
      slicing-history.md     # measured sessions and root-cause notes (not a second rulebook)
    examples/
      run_job.psjs           # minimal guarded job script template
      export_slices.psjs     # slice export template
      slicing_helper.py      # prepare / preview / build-layout / validate helper
      text_render.py         # dynamic-text preview renderer (HarfBuzz + FreeType or Skia)
      *.example.json         # config templates
```

## Safety notes

- Scripts only touch the documents a task names and guard every edit by document id;
  other open documents are left alone. Never overwrite a document with unsaved user changes
  unless the task explicitly allows it.
- All `batchPlay` descriptors use `dialogOptions: "silent"` so a failing command cannot
  block Photoshop with a modal dialog.
- Work on copies of your PSD/PSB files. The slicing workflow stops at a review manifest
  and exports nothing until you approve it.
- The skill instructions are mostly written in Simplified Chinese, with English trigger
  keywords; modern models read both.

## Changelog

Version history for `photoshop-psjs` is in [`skills/photoshop-psjs/CHANGELOG.md`](skills/photoshop-psjs/CHANGELOG.md). Measured slicing sessions and root-cause notes are in [`skills/photoshop-psjs/references/slicing-history.md`](skills/photoshop-psjs/references/slicing-history.md).

## License

[MIT-0](LICENSE) (MIT No Attribution), matching ClawHub's required license.

---

## 简体中文

让 AI agent 通过独立 UXP 脚本（`.psjs`）驱动 Photoshop：写脚本、启动、轮询结果文件并校验，
不需要写 UXP 插件或 MCP 桥。附带一套由 agent 主导的 UI 切图流程：读 PSD → 写组件/状态清单供审核 →
导出 1x 切图（九宫格、烘焙效果、交互状态）→ 生成引擎无关的布局 JSON → 拼图回放做像素校验。

- 环境：Photoshop 2023（23.5）及以上（已在 2026 上实测），Windows / macOS。
- 安装：`npx skills add younglark/photoshop-skills`，或 `clawhub install photoshop-psjs`。
- 安全：只改任务指定的文档，按 doc id 守卫；`dialogOptions` 一律 `"silent"`；请在 PSD 副本上操作。
