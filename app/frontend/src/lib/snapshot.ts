/** One JPEG of the already face-masked canvas, produced only when the user turned on scene description. */
const JPEG_QUALITY = 0.8;
const DATA_URL_PREFIX = "data:image/jpeg;base64,";

export function canvasToJpegBase64(canvas: HTMLCanvasElement): string | null {
  const url = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  return url.startsWith(DATA_URL_PREFIX) ? url.slice(DATA_URL_PREFIX.length) : null;
}
