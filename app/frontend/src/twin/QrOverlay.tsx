import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { loadLanUrl } from "./api";

const QR_PX = 340;
const QUIET_ZONE_MODULES = 4;

/** Full-page overlay with one large QR of the terminal URL (GET /twin/lan-url). Esc or a click outside closes it. */
export function QrOverlay({ onClose }: { onClose: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [url, setUrl] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    loadLanUrl().then((u) => { if (alive) setUrl(u); }).catch(() => { if (alive) setUrl(null); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    if (typeof url !== "string" || canvas.current === null) return;
    void QRCode.toCanvas(canvas.current, url, { width: QR_PX, margin: QUIET_ZONE_MODULES, errorCorrectionLevel: "M", color: { dark: "#0B1F3A", light: "#FFFFFF" } });
  }, [url]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape" || e.key === "q" || e.key === "Q") { e.stopImmediatePropagation(); onClose(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/55" data-testid="qr-overlay" role="dialog" aria-modal="true" aria-label="단말 연결 QR">
      <button type="button" aria-label="닫기" onClick={onClose} className="absolute inset-0 cursor-default" />
      <div className="relative flex w-[440px] flex-col items-center rounded-2xl bg-white px-8 pb-6 pt-8 shadow-2xl">
        {typeof url === "string" ? (
          <canvas ref={canvas} width={QR_PX} height={QR_PX} style={{ width: QR_PX, height: QR_PX }} data-testid="qr-canvas" aria-label="단말 연결 QR 코드" />
        ) : (
          <div className="flex h-[200px] w-full items-center justify-center text-center text-[20px] font-semibold text-[#5B6676]" data-testid="qr-none">{url === undefined ? "불러오는 중…" : "와이파이 연결 없음 (노트북 핫스팟 사용)"}</div>
        )}
        <p className="m-0 mt-5 text-[28px] font-bold text-[#0F4C9A]">휴대폰으로 찍어 보세요</p>
        <p className="m-0 mt-1 text-[18px] text-[#1F2937]">이 휴대폰이 정류장 안내단말이 됩니다</p>
        {typeof url === "string" && <p className="m-0 mt-4 break-all text-center text-[13px] text-[#5B6676]" data-testid="qr-url">{url} · 같은 와이파이 · 읽기 전용</p>}
        <p className="m-0 mt-2 text-[13px] text-[#5B6676]">Esc 또는 Q로 닫기</p>
      </div>
    </div>
  );
}
