# 单词卡钉钉定时推送（GitHub Actions 版）

电脑关机也能每天 9:00 左右准时往钉钉群推单词卡。卡片内容已全部预生成在本仓库，云端只负责「到点发送」。

## 一次性配置（约 10 分钟）

### 1. 创建 GitHub 仓库

1. 登录 [github.com](https://github.com)（没有账号就免费注册一个）
2. 右上角 **+** → **New repository**
3. 仓库名填 `word-card-daily`，选 **Public**（公开仓库图片才能被钉钉显示，内容只有单词卡，无隐私）
4. **不要**勾选任何初始化选项（README / .gitignore / license 都不勾），点 **Create repository**

### 2. 推送代码

把下面两行里的 `<你的用户名>` 换成你的 GitHub 用户名，发给助手执行（或自己在仓库目录 dingtalk-cron/ 里执行）：

```bash
git remote add origin https://github.com/<你的用户名>/word-card-daily.git
git push -u origin main
```

推送时 Windows 会弹出浏览器让你登录 GitHub 授权，点一下即可。

### 3. 填入钉钉机器人密钥（Secrets 是加密的，不会泄露）

1. 仓库页面 → **Settings** → 左侧 **Secrets and variables** → **Actions**
2. 点 **New repository secret**，添加两条：
   - Name：`DINGTALK_WEBHOOK`　Value：钉钉机器人的 Webhook 地址（oapi.dingtalk.com 开头）
   - Name：`DINGTALK_SECRET`　Value：加签密钥（SEC 开头）

### 4. 开启跟读页（可选但推荐）

1. **Settings** → 左侧 **Pages**
2. Source 选 **Deploy from a branch**，Branch 选 **main**、文件夹选 **/site**，保存
3. 一分钟后 `https://<你的用户名>.github.io/word-card-daily/` 即可打开索引页（带语音跟读）

### 5. 测试

1. 仓库页面 → **Actions** → 左侧 **daily-push** → 右侧 **Run workflow** → **Run workflow**
2. 半分钟后出现绿色对勾、钉钉群收到当天卡片，即全部打通
3. 之后每天北京时间 9:00~9:15 自动推送，无需任何人工操作，电脑关机也照发

## 日常维护

- **换群**：换新的钉钉机器人后，更新两条 Secrets 即可
- **补内容**：卡片按 50 天课程阶梯（L1 四年级 → L5 初三）预生成在 `cards/`，推完自动顺延；快推完时在 WorkBuddy 里说「排下一批」
- **进度**：每次推送后 `cards/.push-log.jsonl` 自动记录并回传仓库，重复触发不会重发
- **临时停推**：Actions → daily-push → `···` → **Disable workflow**

## 目录结构

```
cards/    每天 2 张卡的 JSON + 推送台账 .push-log.jsonl
site/     预生成的网页与图片（图片供钉钉内嵌，网页供跟读）
scripts/  push_today.py（云端调度）+ push_dingtalk.py（钉钉发送）+ build_card.py（渲染）
```
