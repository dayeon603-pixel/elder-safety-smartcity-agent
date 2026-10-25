import { useCallback, useEffect, useRef, useState } from "react";

export interface SimClock {
  t: number;
  playing: boolean;
  play: () => void;
  pause: () => void;
  reset: () => void;
  seek: (t: number) => void;
}

/** Simulation clock in simulated seconds. Advances by rate x wall time while playing; stops at maxT. */
export function useSimClock(maxT: number, rate: number): SimClock {
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const tRef = useRef(0);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last: number | null = null;
    const tick = (now: number): void => {
      const dt = last === null ? 0 : (now - last) / 1000;
      last = now;
      tRef.current = Math.min(maxT, tRef.current + dt * rate);
      setT(tRef.current);
      if (tRef.current >= maxT) {
        setPlaying(false);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, rate, maxT]);

  const play = useCallback(() => {
    if (tRef.current >= maxT) {
      tRef.current = 0;
      setT(0);
    }
    setPlaying(true);
  }, [maxT]);
  const pause = useCallback(() => setPlaying(false), []);
  const reset = useCallback(() => {
    setPlaying(false);
    tRef.current = 0;
    setT(0);
  }, []);
  const seek = useCallback((v: number) => {
    tRef.current = v;
    setT(v);
  }, []);
  return { t: Math.min(t, maxT), playing, play, pause, reset, seek };
}
