# 五贤阁 —— 前端重新设计（现代杂志 + 微信群聊）

- 日期：2026-07-17
- 状态：已确定，待实现
- 配色方案：方向 B 现代杂志

## 一、为什么改

旧版（Task 12 那版）有两个核心问题：

1. **AI 味重**：暗色背景 + 5 个高饱和度点缀色 + 居中卡片布局，是所有 AI demo 的默认观感。
2. **形态不对**：用户的核心诉求是"几个朋友在群里讨论我的事"，旧版做成了 "chat 滚动列表" 偏偏不像聊天。

新版改成「现代杂志配色 + 微信群聊布局」，借助用户对群聊界面的熟悉感，降低产品陌生感。

## 二、配色（写死）

```css
--bg-primary:    #FAFAFA;   /* 冷调米白 主背景 */
--bg-surface:    #FFFFFF;   /* 卡片/消息底 */
--bg-elevated:   #F4F4F5;   /* 悬浮/输入框 */

--text-primary:  #111827;   /* 墨黑 */
--text-secondary:#374151;
--text-muted:    #6B7280;
--text-subtle:   #9CA3AF;

--border-default:#E5E7EB;
--border-strong: #D1D5DB;

--brand:         #0F766E;   /* 深青 主色 */
--brand-hover:   #115E59;

/* 议员色（固定，与后端 member_profiles.color 一致） */
--member-suan:   #1E40AF;   /* 老算 深蓝 */
--member-chen:   #991B1B;   /* 老沉 暗红 */
--member-ji:     #C2410C;   /* 小机 橙 */
--member-nian:   #15803D;   /* 十年 深绿 */
--member-fan:    #6B21A8;   /* 反对 深紫 */

/* 立场标签（用职员色更淡的版本） */
--stance-for-bg:    #DCFCE7;
--stance-for-text:  #15803D;
--stance-against-bg:#FEE2E2;
--stance-against-text:#991B1B;
--stance-neutral-bg:#F3F4F6;
--stance-neutral-text:#4B5563;
```

后端的 `member_profiles.color` 也要同步更新（DB seed 重跑一遍）。

## 三、首页布局

```
┌─────────────────────────────────────────────┐
│ [五贤阁 logo]            [存档] [关于]      │ 顶栏 64px
├─────────────────────────────────────────────┤
│                                             │
│  五贤阁                                     │
│  让决策慢下来，让思路站起来                  │ 大标题区
│  &nbsp;                                     │   顶部 padding 80px
├─────────────────────────────────────────────┤
│  选个场景                                   │
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐              │
│  │🏠  │ │💼  │ │🔄  │ │📈  │              │ 8 个场景卡片
│  │买房 │ │接offer│ │跳槽 │ │投资 │          │ 2×4 网格
│  └────┘ └────┘ └────┘ └────┘              │ gap 16px
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐              │
│  │🚀  │ │🥇  │ │💱  │ │🎓  │              │
│  │创业 │ │买黄金│ │买股 │ │读MBA│           │
│  └────┘ └────┘ └────┘ └────┘              │
├─────────────────────────────────────────────┤
│  或描述你自己的决策                          │
│  ┌────────────────────────────────────┐    │
│  │                                    │    │
│  │  点这里开始写...                    │    │ 大自定义入口
│  │                                    │    │ min-height 120px
│  └────────────────────────────────────┘    │
│  关键背景（选填）                            │
│  ┌────────────────────────────────────┐    │
│  └────────────────────────────────────┘    │
│                       [🪶 召集议会 →]      │
└─────────────────────────────────────────────┘
```

**8 个固定场景**（在 topic_templates 表加 2 个）：

| 类别 | 显示名 | 图标 | 关键变量（部分示例） |
|---|---|---|---|
| real_estate | 买房 | 🏠 | city, price, down_payment |
| offer | 接 offer | 💼 | base_salary, equity, company_stage |
| job_change | 跳槽 | 🔄 | current_role, target_role |
| investment | 投资 | 📈 | amount, asset_class |
| entrepreneurship | 创业 | 🚀 | idea, team, capital |
| gold | 买黄金 | 🥇 | amount, allocation_ratio |
| stocks | 买股票 | 💱 | ticker, amount, horizon |
| mba | 读 MBA | 🎓 | age, current_role, school_rank |

## 四、议会大厅（核心改造）

### 4.1 布局

```
┌─────────────────────────────────────────────┐
│ [<] 议会 · 要不要买房        [⏸] [⏹ 结束]  │ 顶栏
├─────────────────────────────────────────────┤
│ [●老算] [●老沉] [●小机] [●十年] [●反对]      │ 议员状态条
│ 思考中  已发言  发言中  待命   已发言        │ 可点击展开侧栏
├─────────────────────────────────────────────┤
│        你已加入「买房议会」  · 14:32         │ 居中系统消息
│                                             │
│ ●老算  14:32                                │
│ ┌──────────────────────────┐                │
│ │ │ 写字 bubble            │                │ 议员气泡
│ └──────────────────────────┘                │ 左对齐
│  [反对] 置信 75%   展开证据 ▾                │ meta 行
│                                             │
│            ┌──────────────────────────┐     │
│            │ 我说两句 bubble          │     │ 我的气泡
│            └──────────────────────────┘     │ 右对齐
│                              我  · 14:35    │
│                                             │
│ ●老沉  14:35                                │
│ ┌──────────────────────────┐                │
│ │ 但下行 -40% 你扛得住？    │                │
│ └──────────────────────────┘                │
├─────────────────────────────────────────────┤
│ ┌──────────────────────────────────────┐   │
│ │ 补充发言...                         →│   │ 固定底部输入
│ └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

### 4.2 议员气泡设计

```
左侧 4px 色条  内容区（#FFFFFF 底）
┌─┬───────────────────────────┐
│ │ ● 老算            14:32    │  ← 头像（彩色实心圆 24px）+ 名 + 时间
│c│                           │
│o│ 期望值 5%，跑输现职 7 pp   │  ← 正文（14px 行高 1.6）
│l│                           │
│o│ [反对 · 置信 75%] 证据3 ▾ │  ← meta 行（12px 文字）
│r│                           │
│ └───────────────────────────┘
```

- 背景：`var(--bg-surface)` 即 #FFFFFF
- 边框：`var(--border-default)` 1px
- 左色条：`border-left: 4px solid <议员色>`
- 头像：24px 实心圆，色 = 议员色，首字母（"算" / "沉" / "机" / "年" / "对"）写白色单字
- 圆角：8px（左上 0 形成气泡尖角感）
- 阴影：`0 1px 2px rgba(0,0,0,0.04)`（轻微浮起）

### 4.3 我的气泡设计

```
                    内容区（深青底）
            ┌───────────────────────────┐
            │                        ─┤
 14:35 我   │ 家里还有 80 万储备        │
            │                           │
            └───────────────────────────┘
            右对齐，左色条 #FFFFFF 没有
```

- 背景：`var(--brand)` 即 #0F766E
- 文字：#FFFFFF 白色
- 边框：无
- 圆角：8px（**右上 0** 形成尖角，跟议员气泡对称）
- 内容右对齐

### 4.4 系统消息

```
─── 第三轮辩论开始 · 14:38 ───      灰字 #6B7280 居中两侧带横线
```

### 4.5 议员状态条（顶部）

```
[●老算] [●老沉] [●小机] [●十年] [●反对]
 待命   思考中  发言中  已发言   错误
```

- 横向排列，5 个 80px 宽的小卡片
- 卡片背景：白底 + 1px 浅边框
- 圆头像：用议员色，状态变化时加不同的环
  - `idle`:`opacity:0.5`
  - `thinking`：外圈呼吸动画（1.5s）
  - `speaking`:3px 主色环
  - `done`:小对勾 ✓ 在头像右下角
- **点击卡片**：右侧滑出抽屉显示该议员当前状态详情（思考什么、调了什么工具）

### 4.6 底部输入框（用户插话）

```
┌─────────────────────────────────────────────┐
│ ┌─────────────────────────────────┐  [→]  │
│ │ 补充发言...                     │       │
│ └─────────────────────────────────┘       │
└─────────────────────────────────────────────┘
```

- 固定定位（`position: fixed` bottom 0）
- 输入框：圆角 24px（药丸形，类似微信）
- 主色背景的发送按钮（圆形 →）
- 提交后：在群聊里追加一条"我"的气泡（右对齐深青底白字）
- 后端：写入 transcript，下一轮议员自动接住

## 五、议会纪要页

旧版的 5 分区结构保留（共识/分歧/盲区/建议/未决），但视觉重做：
- 改成 **杂志式版面**：左大标题 + 右内容，分隔线明显，无圆角卡片
- 字体：标题用衬线感（`font-family: Georgia, "Source Serif Pro", serif`）
- 内容用 sans-serif，行高 1.7
- 议员名字继续用对应彩色

## 六、需要后端配合的变更

### 6.1 议员颜色更新（member_profiles 表）
所有议员颜色按新配色同步：

```sql
UPDATE member_profiles SET color = '#1E40AF' WHERE id = 'suan';
UPDATE member_profiles SET color = '#991B1B' WHERE id = 'chen';
UPDATE member_profiles SET color = '#C2410C' WHERE id = 'ji';
UPDATE member_profiles SET color = '#15803D' WHERE id = 'nian';
UPDATE member_profiles SET color = '#6B21A8' WHERE id = 'fan';
```

### 6.2 固定议题扩展（top_templates 表）
当前 6 个，加到 8 个（新增 stocks 和 mba）：

```sql
INSERT INTO topic_templates (category, display_name, description, key_variables, icon, sort_order) VALUES
  ('stocks', '买股票', '个股配置决策', '["ticker","amount","horizon","thesis"]'::jsonb, '💱', 7),
  ('mba',   '读 MBA', '教育投资决策', '["age","current_role","school_rank","cost","opportunity_cost"]'::jsonb, '🎓', 8)
ON CONFLICT (category) DO NOTHING;
```

### 6.3 议会 transcript schema 不变
现有 transcript entries 已经有 speaker / round / type / stance / content / evidence 字段，足够群聊展示用。

"我说两句"插入的 transcript 条目：
```json
{
  "speaker": "user",
  "round": <当前 round>,
  "type": "user_interrupt",
  "content": "用户输入的文本",
  "timestamp": ISO8601
}
```

需要新增 API：
```
POST /api/parliament/{id}/interrupt
body: { content: string }
```
后端把消息 append 到 parliaments.transcript + 通过 state["user_interrupt"] 通知状态机在下一轮接住。

（这一条后端实现可以放后面一轮，前端先把"我发言"做成只在 UI 里立刻显示，不写入 DB。）

## 七、文件清单（要改的）

```
frontend/
├── tailwind.config.js          （改配色）
├── app/globals.css             （改主背景 + 字体）
├── app/layout.tsx              （加 root 字体）
├── app/page.tsx                （重写首页 8 入口）
├── app/parliament/[id]/page.tsx（群聊形式重写）
├── app/summary/[id]/page.tsx   （杂志风格重做，可选）
├── components/
│   ├── TopicSelector.tsx       → 改名 HomeSceneTabs （8 卡片 + 大输入）
│   ├── MemberStatusBar.tsx     （新，顶部状态条）
│   ├── MemberDrawer.tsx        （新，点击议员展开侧栏）
│   ├── ChatMessage.tsx         （新，替代 MessageBubble，群聊样式）
│   ├── UserBubble.tsx          （新，右对齐我的消息）
│   ├── SystemMessage.tsx       （新，居中系统提示）
│   ├── ChatInput.tsx           （新，底部插话输入）
│   └── SummaryPanel.tsx        （改为杂志版式）
└── lib/types.ts                （加 MemberStatus / 议员 RGB 等）
```

## 八、验收

- [ ] 首页 8 个场景入口在网格里均匀分布，2×4
- [ ] 自定义输入框在场景下方，且为最大视觉重点
- [ ] 议会大厅 5 议员状态条在顶部，可点击展开
- [ ] 议员气泡左侧色条 + 白底 + 左对齐
- [ ] "我"气泡右对齐 + 深青底白字
- [ ] 系统消息居中
- [ ] 底部有插话输入框
- [ ] 整体不再像 "AI demo"，像"严肃杂志 + 群聊"
