import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { AppShell } from "./components/AppShell";
import type { NavItem } from "./components/AppShell";
import { ToastProvider } from "./components/Toast";
import { AedPage } from "./pages/AedPage";
import { AuthPage } from "./pages/AuthPage";
import { BriefingPage } from "./pages/BriefingPage";
import { CitizenPage } from "./pages/CitizenPage";
import { DataPage } from "./pages/DataPage";
import { EvidencePage } from "./pages/EvidencePage";
import { FieldPage } from "./pages/FieldPage";
import { HeatMapPage } from "./pages/HeatMapPage";
import { HomePage } from "./pages/HomePage";
import { ResultsPage } from "./pages/ResultsPage";
import { IS_WALK_FRAMING } from "./config";
import { RunsPage } from "./pages/RunsPage";
import { SilverPage } from "./pages/SilverPage";
import { OpsMapPage } from "./pages/app/OpsMapPage";
import { TwinPage } from "./pages/TwinPage";
import { TerminalPage } from "./pages/TerminalPage";

const NAV_WALK: readonly NavItem[] = [
  { to: "/", label: "개요", end: true },
  { to: "/twin", label: "디지털 트윈" },
  { to: "/field", label: "실버존 에이전트" },
  { to: "/silver", label: "실버존 지도" },
  { to: "/briefing", label: "담당자 브리핑" },
  { to: "/citizen", label: "고령자 화면" },
  { to: "/data", label: "데이터" },
];

/** Combined framing: city ops map, field agent, briefing, evidence hub, citizen view. */
const NAV_COMBINED: readonly NavItem[] = [
  { to: "/", label: "개요", end: true },
  { to: "/twin", label: "디지털 트윈" },
  { to: "/app", label: "도시 운영 지도" },
  { to: "/field", label: "현장 에이전트" },
  { to: "/briefing", label: "담당자 브리핑" },
  { to: "/evidence", label: "근거", also: ["/silver", "/demo", "/aed", "/results"] },
  { to: "/citizen", label: "시민 화면" },
  { to: "/data", label: "데이터" },
];

const NAV: readonly NavItem[] = IS_WALK_FRAMING ? NAV_WALK : NAV_COMBINED;

export function AppRoutes() {
  return (
    <Routes>
      <Route path="twin" element={<TwinPage />} />
      <Route path="terminal" element={<TerminalPage />} />
      <Route element={<AppShell nav={NAV} />}>
        <Route index element={<HomePage />} />
        <Route path="briefing" element={<BriefingPage />} />
        <Route path="demo" element={<HeatMapPage />} />
        <Route path="silver" element={<SilverPage />} />
        <Route path="aed" element={<AedPage />} />
        <Route path="results" element={<ResultsPage />} />
        <Route path="citizen" element={<CitizenPage />} />
        <Route path="app" element={<OpsMapPage />} />
        <Route path="evidence" element={<EvidencePage />} />
        <Route path="field" element={<FieldPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="data" element={<DataPage />} />
        <Route path="login" element={<AuthPage mode="login" />} />
        <Route path="register" element={<AuthPage mode="register" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
