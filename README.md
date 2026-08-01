---
tags: [codex-agent-team-template, codex, team, magic, ai, bash, cli, git, worktree]
timestamp: 2026-08-01T00:00:00.000Z
---
# Codex Agent Team Magic Template

一键把当前 Git 项目改造成 Codex agent-team 开发环境：安装 Superpowers 流程技能、自定义研发角色 agent、
run-state 基础设施、机械化 gate 脚本、唤醒/诊断/安全收尾脚本。

**规范文本以 `skills/agent-team-dev/SKILL.md` 为准**；本 README 只解释架构和使用方式，
wake prompt 只负责调用 skill，agent TOML 只描述角色差异。版本见 `VERSION`。

## 一键安装

在目标项目根目录执行：

```bash
bash ~/.local/share/devspace/qnote/tools/codex-agent-team-template/install.sh
```

也可以显式指定目标项目：

```bash
bash ~/.local/share/devspace/qnote/tools/codex-agent-team-template/install.sh /path/to/project
```

安装后先诊断（文件、config schema、版本漂移；`--smoke` 额外做一次真实 Codex 冒烟测试）：

```bash
.codex/bin/agent-team-doctor
```

## 一键唤醒 Codex

```bash
.codex/bin/wake-agent-team "实现你的目标，必须按计划推进，每个任务后 review 并补测试"
```

非交互执行需要已批准的 spec/plan（active run、`--plan <file>`，或显式 `--allow-assumptions`），
否则直接失败 `EXEC_REQUIRES_APPROVED_SPEC` —— 无人值守任务不允许默认猜需求：

```bash
.codex/bin/wake-agent-team --exec --plan docs/plans/2026-08-01-auth.md "执行这个计划"
.codex/bin/wake-agent-team --exec --allow-assumptions "小任务，允许合理假设"
```

只打开带默认 coordinator prompt 的 Codex TUI：

```bash
.codex/bin/wake-agent-team
```

## 安全收尾（默认只报告，不 commit 不 push）

```bash
.codex/bin/agent-team-finish                        # report only
.codex/bin/agent-team-finish --commit "feat: ..."   # 本地集成/提交
.codex/bin/agent-team-finish --commit "..." --push  # 并推送
```

在 main/master/trunk 上必须额外加 `--allow-default-branch`。staging 范围来自 run manifest
（各任务 write_set 的并集），不是 `git add -A`；`.agents/runs/`、`*.bak.*` 永远不会被暂存。
质量门禁由 workflow 负责；这个脚本只把关范围和目的地。

## 执行模型

```text
Coordinator（主 session，不写业务代码）
│
├── spec / architecture / parallel plan（DAG + waves）
├── 创建 integration branch: agent/<run-id>/integration
│
├── Wave N（并行任务一律 worktree-per-task）
│   ├── worktree T1 → developer → 本地 commit 到 agent/<run-id>/T1
│   ├── worktree T2 → developer → 本地 commit 到 agent/<run-id>/T2
│   └── worktree T3 → developer → 本地 commit 到 agent/<run-id>/T3
│
├── 机械验证: agent-team-check-task（write-set / forbidden / rename-delete / clean）
├── coordinator 按依赖顺序 cherry-pick 到 integration branch
├── integration branch 上串行: focused tests → tester gap-fill → integration_tester
├── spec_reviewer + code_reviewer（wave 级，看 integration diff）
├── fix tasks（新 worktree）→ re-review 直到 findings 闭环
├── knowledge_distiller → .agents/runs/<run-id>/learning-outbox/
└── agent-team-finish（显式 flag 才动用户分支/远端）
```

运行状态持久化在 `.agents/runs/<run-id>/`（state.json + events.jsonl + tasks/*.json），
coordinator 重启后从最后一个未完成 gate 恢复，所有阶段幂等。详见
`skills/agent-team-dev/references/run-state-schema.md`。

## 安装内容

```text
.codex/config.toml                      # [agents] max_concurrent_threads_per_session/max_depth
.codex/agents/researcher.toml           # 代码库/文档调研（只读）
.codex/agents/architect.toml            # 架构和方案设计（只读）
.codex/agents/parallel-planner.toml     # DAG/waves 并行计划拆分（只读）
.codex/agents/developer.toml            # 单任务实现，worktree + task branch，可多实例并发
.codex/agents/debugger.toml             # 通用故障复现和根因定位
.codex/agents/frontend-debugger.toml    # 前端/UI/浏览器调试
.codex/agents/system-debugger.toml      # kernel/backend/system 调试
.codex/agents/tester.toml               # 双模式：wave 前只读设计 / merge 后串行补测试
.codex/agents/integration-tester.toml   # merge 后串行集成测试
.codex/agents/spec-reviewer.toml        # spec/plan 符合性 review（只读）
.codex/agents/code-reviewer.toml        # 代码质量/正确性 review（只读）
.codex/agents/knowledge-distiller.toml  # 只写 learning-outbox 提案
.codex/bin/wake-agent-team              # 唤醒（--exec 有 spec/plan 门禁）
.codex/bin/agent-team-check-task        # 机械任务 gate（merge 前必须通过）
.codex/bin/agent-team-finish            # 安全收尾（默认 report-only）
.codex/bin/agent-team-doctor            # 安装/schema/漂移诊断
.codex/agent-team-template.version      # 版本戳（doctor 用于漂移检测）
.agents/skills/agent-team-dev/          # coordinator 工作流技能（规范来源）+ 模板
.agents/skills/<superpowers>/           # brainstorming / writing-plans 等流程技能
.gitignore                              # 追加 .agents/runs/、.agents/tmp/、*.bak.*
```

qnote 侧（不安装到目标项目）：

```text
bin/import-agent-learning.py            # 从 qnote root 执行，导入 learning-outbox
```

## 设计原则

- Superpowers 是流程：brainstorm → write plan → execute plan。
- 自定义 agents 是角色；主 session 只做 coordinator，不让 agent 自由聊天。
- 并行 wave 一律 worktree-per-task + task branch 本地 commit；共享 tree 只允许串行。
  developer 不 push、不 merge、不碰 integration/用户分支；coordinator 负责集成。
- 每个任务 merge 前必须通过机械 gate（agent-team-check-task），不靠"记得检查"。
- 每个 wave 完成后统一 merge gate：机械验证、串行测试、spec/code review、修复全部问题。
- 验证通过后 distiller 写 learning-outbox 提案，qnote 侧 importer 闭环；
  canonical skill 只接受 proposal，永不自动覆盖。
- 不写 workaround，不加未要求的 fallback，不把 review 问题延期。

## Token discipline

coordinator fan-out / digest fan-in，不采用长期自由聊天团队。文件系统 run state 是唯一事实源，
subagent 输入输出都必须有界（细则见 SKILL.md）：

- 只有当 agent 输出会改变决策或降低风险时才启动它。
- subagent 输入只给 task record 字段；输出必须是 bounded digest。
- reviewer 默认只看 plan、wave diff、task digest；高风险才扩大。
- 小任务不走 parallel planner / multi-agent wave / 完整 run 基础设施。

推荐规模：

```text
small task:  coordinator + developer（串行，可共享 tree）+ reviewer
medium task: researcher/architect as needed + developer + tester + reviewers
large task:  parallel_planner + developer x2-4（worktrees）+ integration_tester
             + reviewers + knowledge_distiller
```

硬规则：并发 agent 不是越多越好；如果一个 agent 的输出不会改变下一步动作，就不要启动它。
