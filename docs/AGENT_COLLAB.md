# 多个 agent 协作与接手指南

> 这份文件写给**下一个接手的 agent**，也写给用户本人。
> 它不是建议，是纪律——这个仓库真的因此出过事（见最后一节）。
> 配套：`AGENTS.md`（环境与硬约束）、`docs/HANDOFF.md`（做到哪了）。

---

## 0. 一句话版本

**动手前 `git status --short`；收工前 `git commit` + `git push`；接手时先 `git pull`。**
这三步能防住本文件里 90% 的事故。

---

## 1. 先搞清楚代码有几份

代码永远有三份，**GitHub 只是一个中转站，不会自动同步**：

```
   那台电脑  ──推 push──▶  GitHub  ──拉 pull──▶  这台电脑
      │                      │                      │
  未提交的活           已合并的主线            各自的本地分支
  只有这里有           大家都能拿              推送了才共享
```

推论：

- **别人没推的代码，你永远看不到**——不要用「我这边没有」去推断「他没做」。
- **推送不是备份**：`git push` 只上传**已提交**的内容，工作区里没提交的改动不上去。
- 换机器 = 手工同步，**没有任何机制会替你完成**。

---

## 2. 分支所有权

| 谁 | 在哪条分支上干活 | 规则 |
|---|---|---|
| 每个 agent | **自己一条分支**（例：`feature/qt-*`、`feature/tk-*`） | 只推自己那条 |
| 集成者（一位 agent 或用户本人） | `main` | **只有它能合并进 main 并推送** |

**为什么必须这样**：多个 agent 同时推 `main`，后推的人必被 non-fast-forward 拒绝，
然后各自 `pull --rebase`，历史变成一团；而**各推各的分支**，任何时刻都不会互相阻塞。

**绝对不要**：在自己的分支上直接改 `main` 并推送；也不要 `git push --force`。

---

## 3. 每次交接的标准动作

### 交棒前（上一棒）

1. 跑测试：`python tools/run_all.py`（每文件独立进程，退出码可信）
2. **提交**：别把半成品留在工作区——工作区是"没人管"的区域，下一个人一切换分支就没了
3. **推送**：`git push origin <你的分支>`
4. **写交接**：在 `docs/HANDOFF.md` / `docs/QT_MAIN_STATUS.md` 写清「做到哪、下一步、已知坑」
5. 收工前最后一条命令永远是 `git status --short`，应当是**空的**

### 接棒时（下一棒）

1. `git status --short` —— 必须是干净的
2. `git log --oneline -5` —— 看上一棒改了什么
3. `git branch -vv` —— 看本地和远端的差距（**只 `status` 不够**，分支指针可能停在老位置）
4. 先读 `docs/HANDOFF.md` 再动手
5. 看到 `M` 状态的文件但交接说明里没提 → **先问，不要猜着改**

---

## 4. 同一个文件不能两个人同时写

这个环境**没有文件锁**：

| 工具 | 行为 |
|---|---|
| `write` | **整文件覆盖**，别人刚写的直接消失 |
| `edit` | 按原文锚点替换，锚点不存在就报错 |

### 三种结局

1. **改了不同区域** → 两个人都"活下来"（最常见）。危险在于**语义冲突**：
   A 把函数改成返回 `None`，B 的新代码还在用返回值；两边各自测试都过，合起来是坏的。
2. **改了同一段** → 后写的**静默覆盖**前者（最危险）。没有报错，几天后才发现「我明明改过」。
3. **锚点文字已不存在** → `edit` 报错（**这反而是好事**，至少你知道撞了）。
   ⚠️ 绝不要用 `write` 整文件重写去"绕过"它——那会把结局 3 变成结局 2。

### 纪律

- 一个文件一个写者。
- 优先小范围 `edit` + **唯一锚点**；改完立刻 `git diff <文件>` 复查。
- 非改不可：一个人写，另一个人给 diff/patch，由写者应用。

---

## 5. git 挡得住什么、挡不住什么

| 挡得住 | 挡不住 |
|---|---|
| `merge` / `rebase` / `cherry-pick` 的**行级冲突** | 两个 agent 在同一分支各自 commit（不冲突，只是历史一前一后） |
| 已提交内容的丢失（历史里有） | **工作区里未提交的改动**——没人保护它 |

### 危险命令黑名单

| 命令 | 会毁掉什么 |
|---|---|
| `git checkout -- .` | 所有人未提交的改动 |
| `git reset --hard` | 同上（还移动分支指针） |
| `git stash`（不带路径） | 会把**别人**的改动一起 stash 走，别人看起来像凭空消失 |
| `git clean -fd` | 未跟踪的新文件（别人刚写的新模块） |
| `git push --force` | 远端别人的提交 |

**要回退就逐文件还原**：`git checkout -- <单个文件>`，或
`git stash push -- <自己的文件>`。看到别人的文件也别碰。

### 误删了怎么找回

- 未提交但曾 `git add` 过：`git fsck --lost-found` 找 dangling blob
- 已提交被 reset 掉：`git reflog`，找到 SHA 再 `git reset --hard <sha>` 或 `git branch 救回来 <sha>`
- 删掉的分支：`git branch 名字 <sha>`（`reflog` 里一般还有）

---

## 6. 合并的固定流程（集成者用）

```bash
git fetch --all --prune          # 先问远端真实状态，别看缓存
git status -sb                   # 本地干净吗？落后多少？
git merge --no-ff origin/<分支>  # 一次只合一条线，冲突当场可见
git log --oneline --graph -5     # 确认合并结果
git status --short               # 必须干净（有冲突时这里会出现 UU）
grep -rl '<<<<<<<' --include='*.py' --include='*.md' .   # 确认没有残留冲突标记
python -m compileall -q Minecraft迁移工具                 # 语法自检
git push origin main
```

**合并顺序**：先合**离 main 最远/最容易冲突**的，或按时间先后合。
一次合一条，出问题好定位；别把自己三条分支压成一个大合并。

---

## 7. 常见故障速查

| 症状 | 原因 | 处理 |
|---|---|---|
| `fatal: Unable to create '.git/index.lock': File exists` | 上次 git 被中断，留下 0 字节锁 | 先 `tasklist \| findstr git` 确认没有 git 在跑，再删 `.git/index.lock` |
| `! [rejected] ... non-fast-forward` | 远端有你没有的提交 | `git pull --rebase` 后再推；**别用 `--force`** |
| `git status` 突然一片「全部被修改」 | 两台机器的行尾规则不一致 | 两边都确认已拉到含 `.gitattributes` 的提交；别手改行尾 |
| 工作区的改动不见了 | 别人跑了 `checkout -- .` / `reset --hard` / `stash` | 查 `git reflog`、`git stash list`；以后逐文件操作 |
| 合并冲突 | 两人改了同一段 | 只解冲突区，跑测试后再 commit；拿不准就问用户 |
| 测试单跑全过、全量挂 | Qt 窗口/定时器互相干扰 | 用 `python tools/run_all.py`（每文件独立进程） |

---

## 8. 一页检查表

**开工前**
- [ ] `git status --short` 干净
- [ ] `git pull` 过（或确认自己的分支已同步）
- [ ] 读了 `docs/HANDOFF.md` 最新的交接段
- [ ] 知道自己要改哪些文件，别人是否正在改

**干活时**
- [ ] 一个文件一个写者
- [ ] 用 `edit` 不用 `write` 覆盖
- [ ] 每完成一小块就 `git diff` 看一眼

**收工前**
- [ ] `python tools/run_all.py` 通过（或说明为什么没跑）
- [ ] `git add` 只加自己的文件
- [ ] `git commit` + `git push origin <自己的分支>`
- [ ] 在 HANDOFF 写下「做到哪、下一步、坑」
- [ ] `git status --short` 是空的

---

## 9. 这个仓库真实踩过的坑

1. **同一条分支一星期没提交**：1,320 行改动晾在工作区，导致另一台机器的人误判
   「这是未完成的活」，白做了一个重复提交又删掉。**小步提交**就是为此。
2. **`git status` 里 12 个 `M` 文件是另一个 agent 的产物**：差点被当成自己的活提交。
3. **陈旧 `.git/index.lock`**：0 字节、进程早没了，却挡住所有 git 写操作；
   另一个 agent 因为它在 Linux 沙箱里报错，还把原因误判成「权限不足」。
4. **`.claude/worktrees/` 下留了 19 MB 的整份仓库副本**（两个 worktree），
   污染全局搜索，且让 `git status` 变慢。用完要 `git worktree remove --force`。
5. **没有 `.gitattributes` 时**：同一份代码在一台机器上显示 17 个文件改动，
   在另一台上显示 143 个；1,296 行真改动被显示成 16,925 行。
   现在有了行尾契约（`* text=auto eol=crlf`），**任何人都不要手改行尾**。

---

## 10. 给用户的提醒（人这一侧）

- 换机前：让每个 agent 都 `commit` + `push`，然后你在终端确认 `git status` 干净
- 换机后：先 `git pull`，再让 agent 开工
- 一台机器上同时开多个 agent 时，**不要让它们改同一批文件**；分工按目录
- 看到 agent 说「我没看到这个文件」——先怀疑没拉取，再去查权限
- 任何人说「已经推送了」，都用 `git log origin/<分支>` 验证，不要凭记忆
