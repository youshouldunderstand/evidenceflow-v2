# EvidenceFlow Demo Guide

## 0. 真实联网调研演示（推荐）

要展示的是**真实联网调研**，不是一个脱离网络的演示。启动前确认 `backend/.env` 已填好
真实凭据（`LLM_PROVIDER=glm` + `OPENAI_API_KEY`，以及 `SEARCH_PROVIDER=tavily` +
`TAVILY_API_KEY`），然后：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

`backend/.env` 会被**自动加载**。**第一步永远是确认真实 Provider 已生效**，不要靠猜：

```powershell
curl.exe -s http://127.0.0.1:8000/health
```

期望看到 `"provider":"glm+tavily"`，`provider_notice` 里说明真实模型与真实搜索。
**如果出现 `"provider":"fake+fake"` 或 `FAKE PROVIDERS`，立刻停下来**——
`.env` 没被读到，此时提交问题只会得到固定演示数据。

然后另开终端启动前端（`frontend` 目录 `npm.cmd run dev`，打开 `http://127.0.0.1:5173`），
输入一个真实技术问题，观察执行时间线里的真实模型/工具调用与报告页的真实来源链接。

### 演示前的准备清单

| 检查 | 为什么 |
|---|---|
| `/health` 显示 `glm+tavily` | 确认走的是真实 API，而不是静默退回 Fake |
| 提前跑通 1 次真实任务 | 真实调用会受 Provider 网络/限流影响，**不能假设现场一定成功** |
| 确认 Tavily 额度与 GLM 账户可用 | 建议档位是每任务最多 14 次模型调用、200,000 Token；Tavily 免费额度每月 1,000 credits |
| 准备一个问题，不要太宽泛 | 问题越具体，证据链越容易讲清楚 |
| 想好失败时怎么讲 | 真实调用存在失败可能。**失败不是演示事故**——执行页会显示失败节点与原因，报告页会标明未验证项，这正好是讲"系统如何诚实处理不确定性"的机会 |

### 0.1 零凭据的工程链路演示（备用）

没有网络或凭据时的兜底，证明工程链路可运行、零外部调用：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m app.demo_nocreds
```

实测：`/health` 200、终态 `COMPLETED`、1 条引用链、**36 帧 SSE**、零外部调用。
它证明的是工程链路，**不是**互联网调研质量。

## 本地演示（Fake，仅用于链路自查）

### 1. 启动后端

```powershell
Copy-Item backend\.env.example backend\.env
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

访问 `http://127.0.0.1:8000/health`，确认返回 HTTP 200，并**查看 `provider` 字段**：

- `fake+fake`：`.env` 没有可用真实凭据（或未配置），走确定性 Fake；
- `glm+tavily`：走真实模型与真实搜索。

**关于凭据**：`backend/.env` 会被自动加载（`app.config.load_env_file`）。
`LLM_PROVIDER` 未配置时默认 `fake`，所以没有 `.env` 也能启动并跑通完整 Fake 流程。

### 2. 启动前端

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

打开 `http://127.0.0.1:5173`。

### 3. 完成一次调研

1. 在“调研问题”输入任意技术调研问题。
2. 点击“开始调研”。
3. 在执行页观察 Stage、模型/工具调用、SSE 事件和 Evidence 事件。
4. 任务完成后点击“查看报告”。
5. 点击任一 Claim，在右侧检查 `Claim → Evidence → Source`、Original Quote 与来源链接。
6. 确认页面显示 Fake Provider 声明。

本地材料选择器只是 V1 接口占位，不会上传或进入工作流。

## API 演示

```powershell
$body = @{
  query = "验证 EvidenceFlow 的证据引用链"
  depth = "standard"
} | ConvertTo-Json

$task = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/research" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body

curl.exe -N "http://127.0.0.1:8000/api/research/$($task.task_id)/events"
Invoke-RestMethod "http://127.0.0.1:8000/api/reports/$($task.task_id)"
```

## 真实 GLM-5.2 模型演示

在 `backend/.env` 中设置：

```env
LLM_PROVIDER=glm
OPENAI_API_KEY=你的密钥
OPENAI_MODEL=glm-5.2
```

重启后端，再重复演示。此时模型调用真实，但 Fake Search/Web 提供的仍是固定演示来源；不要把报告内容当作真实联网研究。

### 真实 Tavily 搜索演示（推荐）

在 `backend/.env` 填写免费 Tavily Key：

```env
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=你的_Tavily_API_Key
TAVILY_SEARCH_MAX_RESULTS=5
TAVILY_SEARCH_DEPTH=basic
```

重启后端后，Provider Notice 应显示 Tavily Search API 与实时 WebReader。系统只采用搜索结果的标题和 URL，最终 Evidence 仍必须来自实际抓取的网页快照。

### 真实 Google 搜索演示

如果你拥有仍可使用的 Google Custom Search JSON API 凭据，在 `backend/.env` 填写：

```env
SEARCH_PROVIDER=google
GOOGLE_SEARCH_API_KEY=你的_API_Key
GOOGLE_SEARCH_ENGINE_ID=你的_CX
GOOGLE_SEARCH_NUM_RESULTS=5
GOOGLE_SEARCH_LANGUAGE=
```

重启后端后，报告页的 Provider Notice 应显示 Google Custom Search 与实时网页读取。Google 搜索摘要只负责发现 URL，最终证据必须能回溯到系统实际抓取的网页快照。

Google 已停止向新客户开放该 API；如果没有历史凭据，本步骤无法完成真实调用。不要用 Fake 结果替代真实调用截图。

## Docker 演示

```powershell
docker compose up --build
```

打开 `http://127.0.0.1:3000`。后端 OpenAPI 仍可从 `http://127.0.0.1:8000/docs` 访问。

停止：

```powershell
docker compose down
```

命名卷保存业务数据库与 Checkpoint；`docker compose down -v` 会删除它，除非明确想清空数据，否则不要执行 `-v`。

## 演示检查表

- Provider 声明与实际配置一致。
- SSE sequence 严格递增。
- 终态是完成、带警告、失败或预算耗尽之一。
- Factual Claim 能点到 Evidence 和 Source Quote。
- 失败时页面展示分类后的错误，不泄露密钥。
