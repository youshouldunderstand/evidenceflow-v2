export type Route =
  | { page: "new" }
  | { page: "execution"; taskId: string }
  | { page: "report"; taskId: string };

export function parseRoute(pathname: string): Route {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "research" && parts[1]) {
    return { page: "execution", taskId: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "report" && parts[1]) {
    return { page: "report", taskId: decodeURIComponent(parts[1]) };
  }
  return { page: "new" };
}
