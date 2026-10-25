import { formatMeters } from "../../lib/field";

const VB_W = 640;
const VB_H = 190;
const ROAD_X0 = 56;
const ROAD_X1 = 584;
const BAND_Y0 = 62;
const BAND_H = 66;
const STRIPE_H = 8;
const PED_Y = BAND_Y0 + BAND_H / 2;

interface Props {
  lengthM: number;
  lanes: number;
  positionM: number;
  /** Where the pedestrian would be when the green ends, if that is before the far curb. */
  greenEndM: number | null;
  signalGreen: boolean;
}

/** Top-down crosswalk. The road width is the data length; lane dividers come from the lane count. */
export function CrosswalkScene({ lengthM, lanes, positionM, greenEndM, signalGreen }: Props) {
  const px = (m: number): number => ROAD_X0 + (m / lengthM) * (ROAD_X1 - ROAD_X0);
  const stripes = Math.floor(BAND_H / (STRIPE_H * 2)) + 1;
  const dividers = Array.from({ length: Math.max(0, lanes - 1) }, (_, i) => px(((i + 1) * lengthM) / lanes));
  return (
    <svg
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label={`횡단보도 위에서 본 그림. 길이 ${formatMeters(lengthM)}, ${lanes}차로. 보행자 위치 ${positionM.toFixed(1)} m. 신호 ${signalGreen ? "녹색" : "적색"}.`}
      className="block h-auto w-full border border-line bg-surface"
    >
      <rect x={0} y={0} width={ROAD_X0} height={VB_H} fill="#eceef1" />
      <rect x={ROAD_X1} y={0} width={VB_W - ROAD_X1} height={VB_H} fill="#eceef1" />
      <rect x={ROAD_X0} y={0} width={ROAD_X1 - ROAD_X0} height={VB_H} fill="#d9dde3" />
      {dividers.map((x) => (
        <line key={x} x1={x} x2={x} y1={0} y2={VB_H} stroke="#ffffff" strokeWidth={1.5} strokeDasharray="10 8" />
      ))}
      {Array.from({ length: stripes }, (_, i) => (
        <rect key={i} x={ROAD_X0} y={BAND_Y0 + i * STRIPE_H * 2} width={ROAD_X1 - ROAD_X0} height={STRIPE_H} fill="#ffffff" opacity={0.9} />
      ))}
      <line x1={ROAD_X0} x2={px(positionM)} y1={PED_Y} y2={PED_Y} stroke="#0072B2" strokeWidth={3} />
      {greenEndM !== null && (
        <g>
          <line x1={px(greenEndM)} x2={px(greenEndM)} y1={20} y2={VB_H - 28} stroke="#b3261e" strokeWidth={2} strokeDasharray="5 4" />
          <text x={px(greenEndM)} y={14} textAnchor="middle" fontSize={12} fill="#b3261e">녹색 종료 시 위치 {greenEndM.toFixed(1)} m</text>
        </g>
      )}
      <circle cx={px(positionM)} cy={PED_Y} r={8} fill="#0072B2" stroke="#ffffff" strokeWidth={2} />
      <text x={ROAD_X0} y={VB_H - 8} textAnchor="middle" fontSize={12} fill="#4a515c">0 m</text>
      <text x={ROAD_X1} y={VB_H - 8} textAnchor="middle" fontSize={12} fill="#4a515c">{formatMeters(lengthM)}</text>
      <text x={(ROAD_X0 + ROAD_X1) / 2} y={VB_H - 8} textAnchor="middle" fontSize={12} fill="#4a515c">{lanes}차로</text>
    </svg>
  );
}
