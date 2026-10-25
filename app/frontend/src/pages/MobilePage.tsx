import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { loadEffects } from "../twin/api";
import type { EffectChip } from "../twin/types";
import { VIEW_KEY } from "../static/device";

const BASE = import.meta.env.BASE_URL;
const VIDEO_URL = `${BASE}demo.mp4`;
const DISCLAIMER = "관악구 및 정부기관과 무관 · 모든 출력은 훈련용";
const SOURCES = "자료: 국토교통부 GIS건물통합정보 · 기상청 ASOS · 질병관리청 온열질환 · TAAS 노인 보행사고다발지 · 행정안전부 재난문자 기준 · 공공데이터포털 AED·횡단보도·정류장";

interface Func { no: number; title: string; line: string; img: string; effectKey: string | null }
const FUNCS: readonly Func[] = [
  { no: 1, title: "폭염 대응", line: "특보보다 먼저, 위험한 날을 골라 재난문자 초안과 공문을 만듭니다", img: "heat", effectKey: "when" },
  { no: 2, title: "실버존 재배치", line: "같은 개수의 실버존을 사고가 나는 곳으로 옮겨 놓습니다", img: "silver", effectKey: "where" },
  { no: 3, title: "낙상 대응", line: "낙상을 감지해 관제요원 확인 뒤 119 연계와 가까운 열린 AED 길을 안내합니다", img: "fall", effectKey: "what" },
  { no: 4, title: "야간 AED", line: "밤에 닫히는 AED를 찾아 24시간 개방 요청 공문 초안을 만듭니다", img: "night", effectKey: "night" },
  { no: 5, title: "신호 연장 협의", line: "고령자가 건너기에 짧은 신호를 찾아 경찰 협의 요청서 초안을 만듭니다", img: "signal", effectKey: null },
  { no: 6, title: "정류장 단말", line: "휴대폰이 없어도 정류장 단말과 마을방송으로 안내가 닿습니다", img: "stop", effectKey: null },
];

function Result({ e }: { e: EffectChip | undefined }) {
  if (e === undefined) return null;
  return (
    <p className="num m-0 mt-2 flex flex-wrap items-baseline gap-2" data-testid="m-result">
      <span className="text-[26px] font-extrabold leading-8 text-[#0F4C9A]">{e.ours}</span>
      {e.baseline !== null && <span className="text-[15px] text-[#4B5563]">← {e.baseline}</span>}
    </p>
  );
}

/** Phone landing (public 시연판): video first, the phone terminal demo, the six functions, then the full 3D screen. */
export function MobilePage() {
  const video = useRef<HTMLVideoElement>(null);
  const [effects, setEffects] = useState<Record<string, EffectChip> | null>(null);
  useEffect(() => {
    document.title = "고령자 안전 스마트도시 에이전트 · 시연 (모바일)";
    loadEffects().then(setEffects).catch(() => setEffects(null));
  }, []);
  const play = (): void => {
    const v = video.current;
    if (v === null) return;
    v.scrollIntoView({ block: "center", behavior: "smooth" });
    void v.play().catch(() => undefined);
  };
  const setView = (v: "pc" | "mobile"): void => { try { localStorage.setItem(VIEW_KEY, v); } catch { /* storage unavailable: the link still navigates */ } };
  return (
    <div className="mobile-root min-h-screen bg-[#F3F6FA] pb-16 text-[#1F2937]" style={{ fontFamily: '"Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", system-ui, sans-serif' }} data-testid="mobile-root">
      <header className="bg-white px-4 pb-4 pt-5 shadow-sm">
        <h1 className="m-0 text-[22px] font-extrabold leading-8 text-[#0F4C9A]">고령자 안전 스마트도시 에이전트</h1>
        <p className="m-0 mt-1 text-[16px] leading-6 text-[#374151]">폭염, 사고, 밤 공백에서 고령자를 먼저 지키는 도시 관제 시연 · 관악구 2025-07-22</p>
        <span className="mt-2 inline-block rounded-[5px] border border-[#F2D58A] bg-[#FFF3CD] px-2.5 py-1 text-[14px] font-bold text-[#8A5A00]" data-testid="m-training">학생 해커톤 작품 시연 · 훈련용</span>
      </header>

      <main className="mx-auto max-w-[560px] px-4">
        <section className="mt-4 overflow-hidden rounded-xl bg-white shadow" aria-label="시연 영상" data-testid="m-video-card">
          {/* eslint-disable-next-line jsx-a11y/media-has-caption -- the demo video has no audio track; captions are burned into the picture */}
          <video ref={video} src={VIDEO_URL} poster={`${BASE}m/poster.webp`} controls playsInline preload="none" muted={false} className="block aspect-video w-full bg-black" data-testid="m-video" />
          <div className="p-3">
            <button type="button" onClick={play} className="flex h-14 w-full items-center justify-center gap-2 rounded-lg bg-[#0F4C9A] text-[18px] font-extrabold text-white active:bg-[#0B3A78]" data-testid="m-play">
              <span aria-hidden="true">▶</span> 시연 영상 보기 (약 3분)
            </button>
          </div>
        </section>

        <section className="mt-3" aria-label="재난문자 체험">
          <Link to="/terminal?view=phone" className="flex min-h-14 items-center justify-between gap-3 rounded-xl border-2 border-[#0F4C9A] bg-white px-4 py-3 text-[#0F4C9A] no-underline" data-testid="m-terminal">
            <span><b className="block text-[18px]">재난문자 화면 체험</b><span className="text-[15px] text-[#374151]">이 휴대폰에서 폭염 알림이 어떻게 보이는지 확인</span></span>
            <span className="text-[26px]" aria-hidden="true">›</span>
          </Link>
        </section>

        <h2 className="mb-2 mt-6 text-[19px] font-extrabold">여섯 가지 기능</h2>
        <ol className="m-0 flex list-none flex-col gap-3 p-0" data-testid="m-funcs">
          {FUNCS.map((f) => (
            <li key={f.no} className="overflow-hidden rounded-xl bg-white shadow" data-testid="m-func">
              <img src={`${BASE}m/${f.img}.webp`} alt={`${f.title} 화면`} loading="lazy" className="block aspect-[16/9] w-full object-cover object-top" />
              <div className="p-3">
                <h3 className="m-0 text-[18px] font-extrabold"><span className="mr-2 inline-flex h-7 w-7 items-center justify-center rounded-full bg-[#0F4C9A] text-[15px] text-white">{f.no}</span>{f.title}</h3>
                <p className="m-0 mt-1 text-[16px] leading-6 text-[#374151]">{f.line}</p>
                {f.effectKey !== null && <Result e={effects?.[f.effectKey]} />}
              </div>
            </li>
          ))}
        </ol>

        <Link to="/twin" onClick={() => setView("pc")} className="mt-5 flex min-h-14 items-center justify-center rounded-xl bg-white px-4 text-[17px] font-bold text-[#0F4C9A] shadow" data-testid="m-twin">3D 관제 화면 열기 (컴퓨터 권장)</Link>
        <Link to="/twin" onClick={() => setView("pc")} className="mt-1 flex min-h-12 items-center justify-center text-[15px] text-[#374151] underline" data-testid="m-pc">PC 화면으로 보기</Link>

        <footer className="mt-6 text-[14px] leading-5 text-[#4B5563]">
          <p className="m-0" data-testid="m-sources">{SOURCES}</p>
          <p className="m-0 mt-2 font-bold" data-testid="m-disclaimer">{DISCLAIMER}</p>
        </footer>
      </main>
    </div>
  );
}
