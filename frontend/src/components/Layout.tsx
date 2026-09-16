import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
  navigate: (path: string) => void;
}

export function Layout({ children, navigate }: Props) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => navigate("/")}>
          <span className="brand-mark">EF</span>
          <span>EvidenceFlow</span>
        </button>
        <span className="tagline">证据溯源 · 自动核验 · 可恢复执行</span>
      </header>
      <main>{children}</main>
    </div>
  );
}
