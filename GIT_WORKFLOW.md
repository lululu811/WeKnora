# Git 工作流：个人 Fork（永久分叉 / 不回流上游）

> 适用仓库：`lululu811/WeKnora`（fork of `Tencent/WeKnora`）
> 模式：**永久分叉** —— 私有补丁自用，不提 PR，不回流上游。
> `MUST` / `NEVER` / `SHOULD` 按 RFC 2119 解释。

---

## 1. 拓扑与角色

| 引用 | 指向 | 角色 | 允许的操作 |
|---|---|---|---|
| `upstream/main` | `github.com/Tencent/WeKnora` | 只读真源 | `fetch` / `merge`；**NEVER `push`** |
| `origin/main` | `github.com/lululu811/WeKnora` | 上游**纯净镜像** | 只接受 fast-forward `push` |
| `origin/mine` | 同上 | 私有补丁备份 | 正常 `push` |
| `main`（本地） | — | 镜像，**零本地提交** | 只允许 `merge --ff-only upstream/main` |
| `mine`（本地） | — | **唯一工作分支**，承载全部本地补丁 | 日常提交、`merge upstream/main`、`push origin mine` |

一句话铁则：**`main` 永远等于 `upstream/main`；一切改动只存在于 `mine`。**

当前分支的子目录名、包名、提交信息沿用历史命名（如 `zettaranc`、`hithink_finance`、`kline-studio`）即可，与分支名无关。

---

## 2. 铁律

- **NEVER** 在 `main` 上 `commit` / 非 ff `merge` / `rebase` / `cherry-pick` / 直接编辑文件。
  硬性判据：`git rev-list --count upstream/main..main` 必须恒为 `0`。
- **NEVER** 对 `mine` 执行 `rebase`、`filter-branch`、对已推送提交 `commit --amend`、`push --force`。
  长命分支一旦重写历史，别人（含你另一台机器）clone 的 fork 会全乱。**`mine` 的历史只增不改。**
- **NEVER** `git push upstream`（含 `git push` 在 `main` 上裸跑；默认推送目标靠 §6 的三条配置锁死到 `origin`）。
- 同步上游 **MUST** 用 `merge`，即 `git merge upstream/main`；冲突在同一次 merge commit 内解决完。
- **NEVER** 为了"历史干净"而 rebase `mine`。线性历史在这个模式下没有价值，历史稳定才有价值。
- `mine` **SHOULD** 每次提交后 `push origin mine`（离线备份 + 多机一致）。
- 短命实验分支可以从 `mine` 切出并随意 rebase，但合回 `mine` 后 **MUST** 删除；**`mine` 自身 NEVER rebase**。

---

## 3. 日常流程

### 3.1 同步上游（每次开工前）

```bash
git fetch upstream main           # 只取上游 main，不拉 160+ 上游分支与 tag
git checkout main
git merge --ff-only upstream/main # 必须 fast-forward
git push origin main              # 刷新私有 fork 的镜像
git checkout mine
git merge upstream/main           # 长命分支用 merge
# 有冲突 → 按 §4 解决 → git add -A && git merge --continue
git push origin mine
```

- `git merge --ff-only` 是**校验器**：它失败就说明 `main` 被污染过，按 §5 修复，别绕过。
- 执行前工作区必须干净（`git status`）；有未提交改动就先 `git stash` 或提交到 `mine`。
- 分支名不是 `mine` 时（例如当前是 `zettaranc`），一次性改名接替：

```bash
git branch -m <当前工作分支> mine
git push -u origin mine
```

### 3.2 开发

```bash
git checkout mine
# ... 改动 ...
git add -A && git commit -m "feat(<scope>): ..."
git push origin mine
```

提交信息沿用仓库既有风格（Conventional Commits，`feat(...)` / `fix(...)`）。

---

## 4. 冲突处理

`git merge upstream/main` 期间：`ours` = `mine`，`theirs` = `upstream/main`。

```bash
git status                     # 看冲突清单
git checkout --theirs -- <file>   # 整份取上游版本，丢弃本方（慎用）
git checkout --ours   -- <file>   # 整份保留本方（慎用，等于放弃上游该文件改动）
# 手工合并后
git add -A && git merge --continue
```

- 中途放弃：`git merge --abort`（回到 merge 前状态，无损）。
- `mine` 已提交 merge 但**还没 push**：`git reset --hard ORIG_HEAD` 可直接重来。
- `mine` 已 push：**NEVER** `reset` + `push --force`；用 `git revert -m 1 <merge-commit>` 生成反向提交。
- 反复在同一区域冲突时，用 §6 的 `rerere` 自动复用历史解法。

---

## 5. `main` 被污染后的修复

```bash
git log --oneline upstream/main..main      # 先看 main 上多出什么
git checkout mine
git cherry-pick <多出的提交>               # 先抢救进 mine（若尚未在里面）
git checkout main
git fetch upstream main
git fetch origin main                      # 先刷新 origin/main 跟踪引用，否则 --force-with-lease 会因过期而拒推
git reset --hard upstream/main
git push --force-with-lease origin main    # 唯一允许 force push 的场景
```

`origin/main` 是镜像不是共享发布分支，所以这里允许重写；但 **MUST** 用 `--force-with-lease`，**NEVER** 裸 `--force`。

---

## 6. 必须的本地配置（一次性）

`branch.main.remote=upstream` 会让 `git push` 在 `main` 上的默认目标变成 **upstream（Tencent）**。
实测本仓库（git 2.55.0）：未加固时 `git rev-parse --abbrev-ref 'main@{push}'` 输出 `upstream/main`。

```bash
# 推送目标加固（这三条必须一起配）
git config --local remote.pushDefault origin
git config --local push.default current              # 必须；与上一行组合后 main@{push} == origin/main（实测）
git config --local remote.upstream.pushurl no_push   # 兜底：任何指向 upstream 的 push 直接失败

# 防误操作
git config --local pull.rebase false
git config --local branch.mine.rebase false
git config --local push.useForceIfIncludes true      # 裸 --force 自动获得 --force-with-lease 语义
git config --local merge.conflictStyle zdiff3        # 冲突块里带共同祖先，好读
git config --local rerere.enabled true               # 同一处冲突只解一次
git config --local fetch.prune true

git config --local alias.upstream-sync '!f(){ set -e; git fetch upstream main; git checkout main; git merge --ff-only upstream/main; git push origin main; git checkout mine; git merge upstream/main; }; f'
```

只设 `remote.pushDefault` 而不设 `push.default` 时，`simple` 模式会直接报
`无法解析 'simple' 推送至一个单独的目标`——两者必须成对配置。

之后 §3.1 全流程只需 `git upstream-sync`（遇到冲突会停在 merge 状态等你解决）。

校验：

```bash
git rev-parse --abbrev-ref 'main@{push}'   # 必须输出 origin/main
git remote get-url --push upstream         # 必须输出 no_push
```

---

## 7. 本仓库的冲突热点

| 路径 | 说明 |
|---|---|
| `AGENTS.md` | **mine 独有文件**（当前上游无同名文件）。上游若将来新增 `AGENTS.md`，会是 add/add 冲突，保留双方内容即可 |
| `README*.md`、`CHANGELOG.md` | 上游高频改动，本地尽量只追加、少改原句 |
| `internal/container/container.go` | dig DI 注册顺序，上游新增服务必改此文件 |
| `internal/router/routes_*.go` | 路由注册，本地新增接口会叠在同一个注册块 |
| `internal/types/interfaces/*.go` | 接口集中地 |
| `internal/config/config.go`、`config/config.yaml`、`config/builtin_agents.yaml`、`config/agent_type_presets.yaml` | 上游持续新增配置键与内置 agent |
| `migrations/versioned/NNN_*.{up,down}.sql` | **序号碰撞**：本地新加的序号会与上游新加的撞号。冲突时把本地文件重命名为「上游最大序号 + 1」，同步改 `.down.sql` 与 `migrations/sqlite/` 兄弟文件，并检查代码中的硬编码迁移号 |
| `frontend/package-lock.json` | NEVER 手工合并。以一侧为基线后 `npm install` 重新生成（本仓库用 **npm**，NEVER pnpm/yarn） |
| `docs/swagger.{yaml,json}`、`docs/docs.go` | 生成物，冲突后 `make docs` 重新生成 |
| `.env.example` | 上游按 A–J 分区持续追加；本地新增项放在对应分区末尾 |

---

## 8. 自检清单

```bash
git rev-list --count upstream/main..main                       # 必须输出 0
git merge-base --is-ancestor main upstream/main && echo OK      # main 是上游祖先
git log --oneline --graph --decorate -12                        # 期望形态见下
```

期望形态（`mine` 上有 merge commit，`main` 干净跟随上游）：

```
*   8b9cd66 (mine) Merge remote 'upstream/main' into mine
|\
| * 0b2f29b (upstream/main, origin/main, main) add c.txt
* | e5b587c local patch
```

出现下列任一情况即违规，按 §5 / §4 修复：

- `upstream/main..main` 非空 → `main` 被污染；
- `git log` 显示 `mine` 历史被重排而非新增 merge；
- `git status` 显示 `main` 有未提交改动。
