import { useEffect, useState } from "react";
import { Layout } from "./components/Layout";
import { ExecutionPage } from "./pages/ExecutionPage";
import { NewResearchPage } from "./pages/NewResearchPage";
import { ReportPage } from "./pages/ReportPage";
import { parseRoute } from "./routing";

export default function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname));

  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);

  function navigate(path: string) {
    window.history.pushState({}, "", path);
    setRoute(parseRoute(path));
  }

  return (
    <Layout navigate={navigate}>
      {route.page === "new" && <NewResearchPage navigate={navigate} />}
      {route.page === "execution" && <ExecutionPage key={route.taskId} taskId={route.taskId} navigate={navigate} />}
      {route.page === "report" && <ReportPage key={route.taskId} taskId={route.taskId} />}
    </Layout>
  );
}
