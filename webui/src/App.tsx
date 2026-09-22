import { useEffect, useState } from "react";
import PlatformHome from "./workbench/PlatformHome";
import Workbench from "./workbench/Workbench";
export default function App() {
  const [manage, setManage] = useState(location.hash.startsWith("#manage"));
  useEffect(() => {
    const change = () => setManage(location.hash.startsWith("#manage"));
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  return manage ? <Workbench /> : <PlatformHome />;
}
