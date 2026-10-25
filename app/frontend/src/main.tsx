import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

const container = document.getElementById("root");
if (container === null) throw new Error("root element missing");
const root = container;
function mount(): void {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
// Public 시연판: the recorded-fixture backend must be installed before anything calls fetch.
if (import.meta.env.VITE_STATIC === "1") void import("./static/install").then((m) => m.installStatic()).then(mount);
else mount();
