import { PageHeader } from "../components/AppShell";
import { ModuleCards } from "./HomePage";

/** /evidence: the three module results side by side, each linking to its full page. */
export function EvidencePage() {
  return (
    <>
      <PageHeader eyebrow="근거" title="공식 기준과 같은 예산으로 비교했습니다" lead="실버존, 폭염, AED 밤 공백. 숫자는 모두 서버가 계산한 결과를 그대로 읽어 옵니다." />
      <ModuleCards collapseExtensions={false} />
    </>
  );
}
