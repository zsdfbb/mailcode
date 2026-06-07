# MailCode 站点 — MIT 许可证更新计划

> **致 Claude：** 必需子技能：使用 superpowers:executing-plans 逐任务实施本计划。

**目标：** 更新 mailcode-site 网站，反映 MailCode 现已采用 MIT 许可证（完全开源），移除所有"非商业许可"和"商业授权"相关内容。

**架构：** 单页静态站点（英文/中文）。变更内容为文本/文案更新 + 删除无用的 CSS，无逻辑变更。

**技术栈：** HTML、CSS、GitHub Pages

**背景：** MailCode 主项目 LICENSE 从自定义非商业许可证变更为 MIT。`mailcode-site/`（独立仓库）的网站仍引用"非商业许可"并提供"商业授权"。本计划修复全部 5 个文件。

---

### 任务 1：更新 `en/index.html` 定价部分 + 页脚 + CTA

**文件：**
- 修改：`/Users/zs/Develop/mailcode-site/en/index.html:274-276`（定价）
- 修改：`/Users/zs/Develop/mailcode-site/en/index.html:350`（CTA 文本）
- 修改：`/Users/zs/Develop/mailcode-site/en/index.html:355`（CTA 提示）
- 修改：`/Users/zs/Develop/mailcode-site/en/index.html:374`（页脚）

**步骤 1：更新定价部分（第 274-276 行）**

旧：
```html
<h2>Open Source</h2>
<p>MailCode is open source under a non-commercial license. Free for personal use.<br>Commercial licenses and enterprise support available — <a href="mailto:mailcode_official@163.com" style="color:var(--purple-500);text-decoration:underline">contact us</a>.</p>
```

新：
```html
<h2>Open Source</h2>
<p>MailCode is fully open source under the <strong>MIT License</strong> — free for everyone, for any purpose.<br>
Contributions, issues, and stars are welcome on <a href="https://github.com/zsdfbb/mailcode" style="color:var(--purple-500);text-decoration:underline">GitHub</a>.</p>
```

**步骤 2：更新 CTA 文本（第 350 行）**

旧：
```html
<p>Install MailCode in one command and bridge your email to AI in minutes. Free to start, no credit card required.</p>
```

新：
```html
<p>Install MailCode in one command and bridge your email to AI in minutes. Completely free and open source.</p>
```

**步骤 3：更新 CTA 提示（第 355 行）**

旧：
```html
<div class="hint">Open Source · Non-commercial license · Python 3.9+</div>
```

新：
```html
<div class="hint">Open Source · MIT License · Python 3.9+</div>
```

**步骤 4：更新页脚（第 374 行）**

旧：
```html
<div class="footer-copy">© 2026 MailCode. Open source under a non-commercial license.</div>
```

新：
```html
<div class="footer-copy">© 2026 MailCode. Released under the MIT License.</div>
```

---

### 任务 2：更新 `zh/index.html` 定价部分 + 页脚 + CTA

**文件：**
- 修改：`/Users/zs/Develop/mailcode-site/zh/index.html:266-268`（定价）
- 修改：`/Users/zs/Develop/mailcode-site/zh/index.html:340`（CTA 文本）
- 修改：`/Users/zs/Develop/mailcode-site/zh/index.html:345`（CTA 提示）
- 修改：`/Users/zs/Develop/mailcode-site/zh/index.html:363`（页脚）

**步骤 1：更新定价部分（第 266-268 行）**

旧：
```html
<h2>开源</h2>
<p>MailCode 采用非商业开源协议发布，个人使用免费。<br>商业授权与企业支持请联系 <a href="mailto:mailcode_official@163.com" style="color:var(--purple-500);text-decoration:underline">mailcode_official@163.com</a>。</p>
```

新：
```html
<h2>开源</h2>
<p>MailCode 基于 <strong>MIT 协议</strong>完全开源——任何人可自由使用、修改、分发。<br>
欢迎在 <a href="https://github.com/zsdfbb/mailcode" style="color:var(--purple-500);text-decoration:underline">GitHub</a> 上提交 Issue、Star 或贡献代码。</p>
```

**步骤 2：更新 CTA 文本（第 340 行）**

旧：
```html
<p>一条命令安装 MailCode，几分钟内将你的邮箱与 AI 打通。免费开始，无需信用卡。</p>
```

新：
```html
<p>一条命令安装 MailCode，几分钟内将你的邮箱与 AI 打通。完全免费开源。</p>
```

**步骤 3：更新 CTA 提示（第 345 行）**

旧：
```html
<div class="hint">开源 · 非商业许可 · Python 3.9+</div>
```

新：
```html
<div class="hint">开源 · MIT 协议 · Python 3.9+</div>
```

**步骤 4：更新页脚（第 363 行）**

旧：
```html
<div class="footer-copy">© 2026 MailCode。基于非商业开源协议发布。</div>
```

新：
```html
<div class="footer-copy">© 2026 MailCode。基于 MIT 协议开源发布。</div>
```

---

### 任务 3：更新 `design/DESIGN.md`

**文件：**
- 修改：`/Users/zs/Develop/mailcode-site/design/DESIGN.md`

需要变更的内容：
- 第 3.2 节表格中"Pricing"行：将"3 档定价"改为"开源展示"
- 第 4.6 节（PricingCard）：删除整个组件部分（第 260-276 行）——该部分是为旧的包含 `$4.99/月` 的定价表服务的，现已不再使用
- 第 5.7 节（Pricing）：更新以反映当前的单卡片设计（不再有三档定价）
- 第 6.2 节："Pricing 3 档"改为"Pricing 开源展示"

---

### 任务 4：删除未使用的 CSS 定价样式

**文件：**
- 修改：`/Users/zs/Develop/mailcode-site/assets/css/style.css`

删除整个"Pricing"代码块（第 677-793 行）及响应式相关条目（第 1161-1164 行、第 1209 行）。

具体来说：
- 第 677-793 行：删除整个 `/* ===== Pricing ===== */` 代码块
- 第 1161-1165 行：删除 `@media (max-width: 900px)` 中的 3 行定价条目
- 第 1209 行：删除 `.pricing-card { padding: 32px 24px; }`

**步骤 1：删除第 677-793 行（`/* ===== Pricing ===== */` 代码块）**

该代码块从 `/* ===== Pricing ===== */` 开始，到 `/* ===== Tabs (Install) ===== */` 之前结束。

**步骤 2：删除第 1161-1165 行（响应式定价）**

删除 `@media (max-width: 900px)` 代码块中的 `.pricing-grid` 到 `.pricing-card.pro:hover`。

**步骤 3：删除第 1209 行（`.pricing-card { padding: ... }`）**

---

### 任务 5：验证一致性

**检查清单：**
- [ ] `en/index.html`：不再包含"non-commercial"或"commercial license"相关内容
- [ ] `zh/index.html`：同样，不再包含"非商业许可"或"商业授权"相关内容
- [ ] `design/DESIGN.md`：不再包含 `$4.99`、三档定价表或 PricingCard 组件
- [ ] `style.css`：不再包含 `.pricing-*` 选择器
- [ ] HTML 渲染正常（没有布局损坏）
