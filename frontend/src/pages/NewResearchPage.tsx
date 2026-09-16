import { useState, type FormEvent } from "react";
import { createResearch } from "../api";

interface Props {
  navigate: (path: string) => void;
}

export function NewResearchPage({ navigate }: Props) {
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const taskId = await createResearch(query.trim());
      navigate(`/research/${encodeURIComponent(taskId)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建任务失败");
      setSubmitting(false);
    }
  }

  return (
    <section className="hero page-grid">
      <div className="hero-copy">
        <p className="eyebrow">RELIABLE TECH RESEARCH</p>
        <h1>让每个技术结论，都能回到原始证据。</h1>
        <p>
          EvidenceFlow 将规划、检索、证据提取、写作与审核拆成受约束的工作流，
          并公开展示每一次真实执行事件。
        </p>
      </div>
      <form className="panel research-form" onSubmit={submit}>
        <label htmlFor="query">调研问题</label>
        <textarea
          id="query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="例如：比较 LangGraph 与 OpenAI Agents SDK 的持久化和人工介入能力"
          rows={7}
          maxLength={5000}
        />
        <div className="field-row">
          <div>
            <label htmlFor="depth">调研深度</label>
            <select id="depth" disabled value="standard">
              <option value="standard">标准（V1）</option>
            </select>
          </div>
          <div>
            <label htmlFor="materials">本地材料（可选）</label>
            <input
              id="materials"
              type="file"
              multiple
              onChange={(event) =>
                setFiles(Array.from(event.target.files ?? []).map((file) => file.name))
              }
            />
          </div>
        </div>
        {files.length > 0 && (
          <p className="notice">
            已选择 {files.length} 个文件。本地材料检索已预留接口，V1 暂不上传文件内容。
          </p>
        )}
        {error && <p className="error-text">{error}</p>}
        <button className="primary" disabled={submitting || !query.trim()}>
          {submitting ? "正在创建…" : "开始调研"}
        </button>
      </form>
    </section>
  );
}
