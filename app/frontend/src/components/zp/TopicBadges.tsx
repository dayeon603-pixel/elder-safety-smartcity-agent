import { Badge } from "../Badge";

export type TopicTag = "ai_predict" | "ai_optimize" | "public_data";

const LABEL: Readonly<Record<TopicTag, string>> = { ai_predict: "AI 예측", ai_optimize: "AI 최적화", public_data: "공공데이터 연동" };

/** Small consistent tags that say which contest keyword a module answers. Only pass tags that are true for that module. */
export function TopicBadges({ tags }: { tags: readonly TopicTag[] }) {
  return (
    <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label="사용한 기술">
      {tags.map((t) => (
        <li key={t}><Badge tone={t === "public_data" ? "neutral" : "accent"}>{LABEL[t]}</Badge></li>
      ))}
    </ul>
  );
}
