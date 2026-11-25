// Region geometry for the Cast stage. Regions are {x, y, w, h} in source-image pixels, matching
// the engine's Region schema and the X / Y / W / H fields in the inspector.

export const MIN_REGION = 32; // the engine refuses smaller regions (src/studio/pipeline.py)

const int = (v) => Math.round(Number(v) || 0);

/** Keep a region inside a width x height image and at least MIN_REGION on each side (when the
 * image allows it). Position is adjusted before size so nudging into an edge stops at the edge. */
export function clampRegion(region, width, height, min = MIN_REGION) {
  const minW = Math.min(min, width);
  const minH = Math.min(min, height);
  const w = Math.min(Math.max(int(region.w), minW), width);
  const h = Math.min(Math.max(int(region.h), minH), height);
  const x = Math.min(Math.max(int(region.x), 0), width - w);
  const y = Math.min(Math.max(int(region.y), 0), height - h);
  return { x, y, w, h };
}

export const moveRegion = (r, dx, dy, width, height) =>
  clampRegion({ ...r, x: r.x + dx, y: r.y + dy }, width, height);

/** Resize from the bottom-right corner (keyboard: Alt+Arrow). */
export function resizeRegion(r, dw, dh, width, height) {
  const w = Math.min(Math.max(int(r.w + dw), Math.min(MIN_REGION, width)), width - r.x);
  const h = Math.min(Math.max(int(r.h + dh), Math.min(MIN_REGION, height)), height - r.y);
  return clampRegion({ ...r, w, h }, width, height);
}

/** Problems that would make the engine reject this region (shown next to the fields). */
export function regionProblems(r, width, height) {
  const problems = [];
  if (!width || !height) return problems;
  if (r.w < MIN_REGION || r.h < MIN_REGION)
    problems.push(`Make the region at least ${MIN_REGION}×${MIN_REGION} px (it is ${r.w}×${r.h}).`);
  if (r.x < 0 || r.y < 0 || r.x + r.w > width || r.y + r.h > height)
    problems.push(`Keep the region inside the ${width}×${height} image: X+W ≤ ${width} and Y+H ≤ ${height}.`);
  return problems;
}

/** CSS percentages for drawing a region over the displayed image. */
export function regionStyle(r, width, height) {
  const pct = (v, of) => `${((v / of) * 100).toFixed(3)}%`;
  return `left:${pct(r.x, width)};top:${pct(r.y, height)};width:${pct(r.w, width)};height:${pct(r.h, height)}`;
}

/** A centered region a quarter of the image's size, for "Add region". */
export function defaultRegion(width, height) {
  const w = Math.max(MIN_REGION, Math.round(width / 4));
  const h = Math.max(MIN_REGION, Math.round(height / 4));
  return clampRegion({ x: Math.round((width - w) / 2), y: Math.round((height - h) / 2), w, h }, width, height);
}

/** Head-and-shoulders box around a detected face (same proportions as the engine's). */
export function headRegion(face, width, height) {
  const cx = face.x + face.w / 2;
  const x1 = Math.max(0, cx - (face.w * 2.4) / 2);
  const y1 = Math.max(0, face.y - face.h * 0.8);
  const x2 = Math.min(width, cx + (face.w * 2.4) / 2);
  const y2 = Math.min(height, y1 + face.h * 2.6);
  return clampRegion({ x: x1, y: y1, w: x2 - x1, h: y2 - y1 }, width, height);
}

const center = (r) => ({ x: r.x + r.w / 2, y: r.y + r.h / 2 });
const contains = (r, p) => p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h;

/** The detected face that belongs to this region: one whose center lies inside it (closest to
 * the region's center if several), else the nearest face overall. */
export function detectionFor(region, detections) {
  if (!detections?.length) return null;
  const c = center(region);
  const dist = (d) => Math.hypot(center(d.face).x - c.x, center(d.face).y - c.y);
  const inside = detections.filter((d) => contains(region, center(d.face)));
  const pool = inside.length ? inside : detections;
  return pool.reduce((best, d) => (dist(d) < dist(best) ? d : best));
}

/** "Snap to Face": head-and-shoulders box around this region's face. */
export function snapToFace(region, detections, width, height) {
  const d = detectionFor(region, detections);
  return d ? headRegion(d.face, width, height) : null;
}

/** "Expand to Tile": the panel/tile the engine found around this region's face, if any. */
export function expandToTile(region, detections, width, height) {
  const d = detectionFor(region, detections);
  return d && d.region_source === 'tile' ? clampRegion(d.region, width, height) : null;
}

/** IoU of two regions; used so Auto-Cast doesn't add a face that is already cast. */
export function overlap(a, b) {
  const ix = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const iy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  const inter = ix * iy;
  const union = a.w * a.h + b.w * b.h - inter;
  return union > 0 ? inter / union : 0;
}

/** Where the scene lands in the output frame (mirrors the engine's compute_layout). */
export function frameLayout(srcW, srcH, outW, outH, fit = 'fit') {
  if (!srcW || !srcH) return null;
  if (!outW || !outH) return { width: srcW + (srcW % 2), height: srcH + (srcH % 2), scale: 1, offsetX: 0, offsetY: 0 };
  const scale = (fit === 'fit' ? Math.min : Math.max)(outW / srcW, outH / srcH);
  const sw = Math.round(srcW * scale);
  const sh = Math.round(srcH * scale);
  return { width: outW, height: outH, scale, scaledW: sw, scaledH: sh,
    offsetX: Math.floor((outW - sw) / 2), offsetY: Math.floor((outH - sh) / 2) };
}
