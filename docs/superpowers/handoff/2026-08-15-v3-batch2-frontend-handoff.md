# Batch 2 交接文档：八人专家体系 · 前端实施

> 日期：2026-08-15
> 性质：**交接文档**——给新的对话窗口/实施者，无历史上下文也能直接开工
> 前置：Batch 1（后端）已完成并部署，本批只改前端
> 规范原文：`docs/superpowers/specs/2026-08-15-eight-expert-response-system-design.md`（重点读 §3 八人表、§4 回复契约、§14 前端交互要求）

---

## 0. 项目与环境速览

- 项目根：`/Users/xinyiliu/Desktop/xiegang/`（前端在 `frontend/`，Next.js 15 App Router + Tailwind）
- 本地跑前端：`cd frontend && ./node_modules/.bin/next dev -p 3001`（pnpm 未装，直接用 node_modules 里的 bin）
- 线上：`https://www.liuuuu.xyz`（前端）/ `api.liuuuu.xyz`（API），服务器 `ssh root@106.54.246.170`，项目在 `/opt/xiegang`
- **GitHub 在用户网络下经常连不上**——部署走 scp tar 包，不走 git
- 产品名保持 **邪杠**（用户已拍板：UI 不换"深墨决策台"，那是 spec 内部代号）

---

## 1. Batch 1 已完成的后端事实（前端必须对齐这些）

### 1.1 八人专家库（后端 `app/agents/profiles.py` 已生效）

| ID | 显示名 | lens_title（专属栏目） | 头像字 | 颜色（后端已定，前端保持一致） |
|---|---|---|---|---|
| economist | 经济学家 | 成本与回报 | 经 | `#9B4D68` |
| psychologist | 心理学家 | 动机与冲突 | 心 | `#A45B52` |
| historian | 历史学家 | 路径与规律 | 史 | `#7B6652` |
| mathematician | 数学家 | 变量与阈值 | 数 | `#6F4878` |
| strategist | 战略家 | 行动与退路 | 略 | `#4E6E58` |
| legal_scholar | 法学家 | 法 | `#566573` |
| sociologist | 社会学家 | 社 | `#8C6A4F` |
| contrarian | 逆向思考者 | 逆 | `#276078` |

**已删除**：`artist`、`politician`（历史议会数据里还会有这两个 ID，渲染旧数据时不能崩——参照 LEGACY_MEMBERS 的处理方式）。

### 1.2 新 stance 枚举

后端新议会只产出：`support`（建议推进）/ `oppose`（不建议）/ `cautious`（谨慎推进）/ `neutral`（暂不判断）。
**历史议会 DB 里存的是旧值** `for` / `against` / `neutral`——前端类型必须同时容纳两套，渲染历史数据照常显示（旧 for→支持、against→反对）。

### 1.3 回复契约（transcript entry 的 content 字段）

后端渲染的 `content` 是**纯文本四段**（用 `【】`标题 + `\n\n` 分段）：

```
【当前判断 · 谨慎推进】当前更适合继续了解机会，而不是立即承诺。

【成本与回报】留任收益含稳定现金流；跳槽收益依赖尚未确认的股权条款。

【建议行动】先取得书面股权条件再比较。        ← 可选，无则整段省略

【关键问题】如果股权最终价值为零，你还愿意接受吗？   ← 可选，无则省略
```

**不再出现的旧段名**：`【我的判断】``【关键依据】``【主要风险】``【什么时候我会改变判断】``【风险与未知】`。

### 1.4 transcript entry 字段变化（研究轮 type=research）

| 字段 | Batch 1 后 |
|---|---|
| `duty` | **已删除**（不再写） |
| `basis_level` | 仍写入（内部质量信号），**前端不得再显示**（spec §14.2） |
| `lens_title` | **新增**（专属栏目名，可用于 bubble 内小标签） |
| `position` | 不再写（被删） |
| `silent` | 新增 `silent: false` 字段；`should_speak=false` 的专家**根本没有 transcript entry**，只在 members_state 里有记录（`should_speak: false`） |

`expert_completed` SSE 事件的 `entry` 字段可能为 `null`（silent 专家）——前端收到 null entry 时只更新座位状态为"本轮未发言"，不追加 bubble。

---

## 2. Batch 2 改动清单（5 个任务）

### 任务 2.1 `frontend/lib/types.ts`

- **`ExpertId`** 扩为 8 值：`"mathematician" | "economist" | "psychologist" | "historian" | "strategist" | "legal_scholar" | "sociologist" | "contrarian"`（当前在 `types.ts:1-7`）
- **`MEMBERS`**（`types.ts:38-69`）重写为上表 8 人。新增 4 人的 `role` 字段（卡片副标题）建议：战略家"行动顺序与退路"、法学家"合同与权利边界"、社会学家"关系与环境"、逆向思考者"挑战问题定义"。保留 `model`（一句话职责）与 `signature_line`（标志性问题，从 spec §6 取）
- **`Stance`** 改为联合类型：`"support" | "oppose" | "cautious" | "neutral" | "for" | "against"`（旧值保留渲染历史）
- **`Member`** 接口加 `lens_title?: string`
- **`TranscriptEntry.type`**（`types.ts:157-159`）补 `"debate_degraded"`（后端已在发，前端类型缺口——现存 bug）
- **`TranscriptEntry`** 加 `lens_title?: string`、`silent?: boolean`
- LEGACY_MEMBERS（suan/chen 等，`types.ts:72-98`）**保留不动**（渲染历史议会）

### 任务 2.2 `frontend/app/council/setup/page.tsx` 八卡片

现状：已是完整的选择器（6 卡片、3-5 限制、preview 预选、`selectedExpertIds` 传给 DecisionPathModal）。改动极小：
- `FALLBACK_EXPERTS`（`:15-21`，现无 historian 的 5 人）→ 改为 8 人库默认 5 人：`["economist", "psychologist", "strategist", "legal_scholar", "contrarian"]`
- 文案：`:200` "六种思考方法" → "八种思考方法"；`:172-173` "系统已根据你的问题推荐 5 位专家…保留 3–5 位" 核对语义不变即可；`:207` "默认已选 5 位" 保留
- 卡片网格 `sm:grid-cols-2`（`:211-226`）自动排 8 张卡，无需改布局
- **卡片组件** `components/council/ExpertSelectionCard.tsx` 完全数据驱动，零改动

### 任务 2.3 `frontend/components/ChatMessage.tsx` 新渲染（核心）

现状（`:10-14` STANCE_BADGE、`:36` stance、`:58-61` 轮次标签、`:63-67` duty chip、`:126-134` basis 标签、`:81` content、`:94-113` evidence 列表）。改动：

1. **STANCE_BADGE** 换新枚举 + 旧值兼容：
   ```ts
   const STANCE_BADGE = {
     support: { label: "建议推进", bg: "bg-stance-forBg", text: "text-stance-forText" },
     oppose:  { label: "不建议",  bg: "bg-stance-againstBg", text: "text-stance-againstText" },
     cautious:{ label: "谨慎推进", bg: "bg-stance-neutralBg", text: "text-stance-neutralText" },
     neutral: { label: "中立",    bg: "bg-stance-neutralBg", text: "text-stance-neutralText" },
     // 历史数据
     for:     { label: "支持", ... }, against: { label: "反对", ... },
   }
   ```
   （CSS 类沿用现有 tailwind stance-* 调色，`tailwind.config.js:55-63`）
2. **删 duty chip**（`:63-67`）——entry.duty 不存在了
3. **删 basis 标签渲染**（`:126-134`）——spec §14.2 确定程度由语言表达
4. **修"独立判断"重复**：`:60`（round 0 标签）与 `:143`（formatTime）都显示"独立判断"——只保留 `:143` 一处，`:60` round 0 改显示空串或"调研结论"（spec §14.2 角色名旁只显示一次）
5. content 渲染保持 `whitespace-pre-wrap`（后端已按 `\n\n` 分段 + `【】` 标题，纯文本直接展示即可；如果想美化可在 `【` 前加粗——可选，不强制）
6. `type === "debate_degraded"` 的样式处理（斜体/淡化，与 debate_error 类似但不红色）

### 任务 2.4 `SummaryPanel.tsx` / `ExpertSeatPanel.tsx` 标签同步

- `SummaryPanel.tsx:317-321` MemberStanceBar 标签：加 support/oppose/cautious 新值（旧值保留）
- `ExpertSeatPanel.tsx`：memberStatus 加"未发言"状态（对应 members_state 里 `should_speak: false` 的专家——显示"本轮未发言"灰色，**不是 error**）。现状 `:5-14` STATUS_LABEL 已有 idle/degraded/error 等，加一个 `silent: "本轮未发言"`；`app/parliament/[id]/page.tsx:133-145`（snapshot 播种 memberStatus）与 `:224-250`（expert_completed 处理 entry=null 的 silent 分支）要同步
- `page.tsx:692` `{selectedExpertIds.length || 5}` 兜底数字保持 5

### 任务 2.5 孤儿清理

- **删** `frontend/lib/opening_lines.ts`（无 import 引用 + 硬编码旧 6 ID + legacy 5 ID 三套）
- `components/MemberStatusBar.tsx`、`components/ElectionDetail.tsx` 确认无 import 后删除（探索报告说是孤儿，删除前 grep 确认）

---

## 3. 不要做的事（明确边界）

- ❌ 不改后端（Batch 1 已部署，任何后端改动需要单独评审）
- ❌ 不动 decision_path / clarification 流程（刚修好，`clarification_gate.py` 的 confirmed 跳过逻辑、`state.py` 的 TypedDict decision_path 声明都是修复过的，别碰）
- ❌ 不删 LEGACY_MEMBERS / 不迁移历史议会数据（旧议会 stance for/against 照常渲染）
- ❌ 不做质量编辑模型、跨角色重复检查、LLM 专家推荐（spec 后续增强项）
- ❌ UI 不改名"深墨决策台"，一切品牌文案保持邪杠

---

## 4. 验收标准（跑通才算完）

1. `cd frontend && npx tsc --noEmit` 类型零错误（或 `pnpm build` 成功——但 pnpm 未装，用 `./node_modules/.bin/next build`）
2. 本地 `next dev` 起来后：
   - setup 页显示 **8 张卡片**，preview 预选 5 人（新 4 人卡片头像/颜色正确）
   - 开一场议会（本地后端 8001 或直接连线上），研究轮 bubble 是**四段契约**格式、stance 徽章是新四值之一、无 duty chip、无"判断基础"标签、"独立判断"只出现一次
   - 打开一个**历史议会** `/summary/{旧id}`——旧 stance（支持/反对）、旧专家（艺术家/政治家）渲染不崩
3. 部署到线上（见 §5）后在 https://www.liuuuu.xyz 复测一遍

---

## 5. 部署流程（GitHub 不可靠，走 scp）

```bash
# Mac：打包改动文件
tar czf /tmp/xiegang-v3-batch2.tar.gz -C /Users/xinyiliu/Desktop/xiegang \
  frontend/lib/types.ts frontend/lib/api.ts \
  frontend/app/council/setup/page.tsx \
  frontend/components/ChatMessage.tsx \
  frontend/components/SummaryPanel.tsx \
  frontend/components/council/ExpertSeatPanel.tsx \
  "frontend/app/parliament/[id]/page.tsx"
# （按实际改动文件调整清单；删了 opening_lines.ts 等孤儿则无需打包）

# Mac：上传
scp /tmp/xiegang-v3-batch2.tar.gz root@106.54.246.170:/tmp/

# ssh 进服务器后：
cd /opt/xiegang && tar xzf /tmp/xiegang-v3-batch2.tar.gz && \
  docker compose -f docker-compose.prod.yml restart frontend nginx
```

前端容器是 node:18-alpine 跑 dev 模式（热加载），restart 即可，不需要 rebuild。

---

## 6. 前端现状地图（探索时核实过的行号，改动时以此定位）

| 文件 | 关键位置 |
|---|---|
| `lib/types.ts` | ExpertId `:1-7`；LegacyMemberId `:8`；Stance `:10`；MEMBERS `:38-69`；LEGACY `:72-98`；getMember `:100-102`；TranscriptEntry `:154-170`（type 枚举 `:157-159`）；MemberStatus `:303-311` |
| `app/council/setup/page.tsx` | FALLBACK_EXPERTS `:15-21`；toggleExpert max5 `:78-90`；startCouncil min3 `:92-97`；网格 `:211-226`；文案 `:172-173, :200, :207` |
| `components/ChatMessage.tsx` | STANCE_BADGE `:10-14`；duty chip `:63-67`；basis `:126-134`；"独立判断" `:60` + `:143` |
| `components/SummaryPanel.tsx` | BASIS_LABEL `:19-23`；MemberStanceBar 标签 `:317-321` |
| `app/parliament/[id]/page.tsx` | snapshot 播种 `:118-164`；expert_completed `:224-250`；`:692` 兜底 5 |
| 孤儿 | `lib/opening_lines.ts`（整文件）；`components/MemberStatusBar.tsx`；`components/ElectionDetail.tsx` |

行号是 Batch 1 时点的，若有漂移以 grep 定位为准。

---

## 7. 完成后

1. commit（`git add -A && git commit -m "feat(V3-batch2): 前端八人专家体系 + 四段契约渲染"`）；push 若网络不通不强求
2. 通知用户线上验收：8 卡片选择 → 议会 → 四段回复 → 历史议会不崩
3. Batch 1 的 commit 是 `ae73f7f`（若 push 一直失败，累积一起推）
