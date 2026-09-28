import { describe, expect, it } from "vitest";

import { aimBoxRect } from "./labelFrame";

describe("label aim box", () => {
  it("reads only the centred box of a 720p frame", () => {
    expect(aimBoxRect(1280, 720)).toEqual({ sx: 192, sy: 180, sw: 896, sh: 360 });
  });

  it("stays inside the frame for any size", () => {
    for (const [width, height] of [[640, 480], [1920, 1080], [3, 2]] as const) {
      const rect = aimBoxRect(width, height);
      expect(rect.sx + rect.sw).toBeLessThanOrEqual(width);
      expect(rect.sy + rect.sh).toBeLessThanOrEqual(height);
      expect(rect.sw).toBeGreaterThan(0);
    }
  });

});
