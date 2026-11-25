import { describe, it, expect } from 'vitest';
import {
  MIN_REGION, clampRegion, moveRegion, resizeRegion, regionProblems, regionStyle, defaultRegion,
  headRegion, detectionFor, snapToFace, expandToTile, overlap, frameLayout,
} from '../src/lib/regions.js';

const W = 977, H = 580;

describe('clamp / move / resize', () => {
  it('keeps regions inside the image and at least MIN_REGION', () => {
    expect(clampRegion({ x: -5, y: 600, w: 10, h: 2000 }, W, H)).toEqual({ x: 0, y: 0, w: MIN_REGION, h: H });
    expect(clampRegion({ x: 970, y: 10, w: 100, h: 100 }, W, H)).toEqual({ x: W - 100, y: 10, w: 100, h: 100 });
  });
  it('moves stop at the edges instead of shrinking', () => {
    const r = { x: 960, y: 0, w: 16 + MIN_REGION, h: 50 };
    expect(moveRegion(r, 50, -5, W, H)).toEqual({ x: W - r.w, y: 0, w: r.w, h: 50 });
  });
  it('resizes from the bottom-right corner within the image', () => {
    expect(resizeRegion({ x: 900, y: 500, w: 50, h: 50 }, 100, 100, W, H)).toEqual({ x: 900, y: 500, w: 77, h: 80 });
    expect(resizeRegion({ x: 10, y: 10, w: 40, h: 40 }, -30, -30, W, H)).toEqual({ x: 10, y: 10, w: MIN_REGION, h: MIN_REGION });
  });
  it('works for images smaller than MIN_REGION', () => {
    expect(clampRegion({ x: 0, y: 0, w: 5, h: 5 }, 20, 20)).toEqual({ x: 0, y: 0, w: 20, h: 20 });
  });
});

describe('problems and display', () => {
  it('explains what the engine would reject', () => {
    expect(regionProblems({ x: 0, y: 0, w: 216, h: 116 }, W, H)).toEqual([]);
    expect(regionProblems({ x: 0, y: 0, w: 10, h: 116 }, W, H)[0]).toMatch(/at least 32×32/);
    expect(regionProblems({ x: 900, y: 0, w: 216, h: 116 }, W, H)[0]).toMatch(/X\+W ≤ 977/);
  });
  it('positions overlays as percentages', () => {
    expect(regionStyle({ x: 0, y: 290, w: 977, h: 58 }, W, H)).toBe('left:0.000%;top:50.000%;width:100.000%;height:10.000%');
  });
  it('adds a centered region a quarter of the size', () => {
    expect(defaultRegion(W, H)).toEqual({ x: 367, y: 218, w: 244, h: 145 });
  });
});

describe('face helpers', () => {
  const detections = [
    { face: { x: 847, y: 76, w: 36, h: 42 }, region: { x: 758, y: 38, w: 205, h: 113 }, region_source: 'tile' },
    { face: { x: 842, y: 204, w: 33, h: 40 }, region: { x: 820, y: 172, w: 79, h: 104 }, region_source: 'head' },
  ];
  it('matches the engine’s head-and-shoulders proportions', () => {
    expect(headRegion({ x: 100, y: 100, w: 40, h: 50 }, W, H)).toEqual({ x: 72, y: 60, w: 96, h: 130 });
  });
  it('finds the face inside a region, else the nearest', () => {
    expect(detectionFor({ x: 750, y: 30, w: 230, h: 130 }, detections)).toBe(detections[0]);
    expect(detectionFor({ x: 700, y: 400, w: 50, h: 50 }, detections)).toBe(detections[1]);
    expect(detectionFor({ x: 0, y: 0, w: 50, h: 50 }, [])).toBeNull();
  });
  it('snaps to the face and expands only to real tiles', () => {
    const r = { x: 760, y: 40, w: 200, h: 100 };
    expect(snapToFace(r, detections, W, H)).toEqual(headRegion(detections[0].face, W, H));
    expect(expandToTile(r, detections, W, H)).toEqual(detections[0].region);
    expect(expandToTile({ x: 830, y: 190, w: 60, h: 80 }, detections, W, H)).toBeNull(); // head-only
  });
  it('measures overlap so Auto-Detect skips faces already cast', () => {
    const a = { x: 0, y: 0, w: 100, h: 100 };
    expect(overlap(a, a)).toBe(1);
    expect(overlap(a, { x: 200, y: 0, w: 10, h: 10 })).toBe(0);
  });
});

describe('frameLayout mirrors the engine', () => {
  it('fits 977×580 into 1280×720 like the recreate guide (1213 wide, 33 px bars)', () => {
    const l = frameLayout(977, 580, 1280, 720, 'fit');
    expect([l.scaledW, l.scaledH, l.offsetX, l.offsetY]).toEqual([1213, 720, 33, 0]);
  });
  it('fill crops (negative offset) and source keeps even dimensions', () => {
    expect(frameLayout(977, 580, 1080, 1920, 'fill').offsetX).toBeLessThan(0);
    expect(frameLayout(977, 580, null, null)).toMatchObject({ width: 978, height: 580, scale: 1 });
  });
});
