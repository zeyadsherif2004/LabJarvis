/**
 * The part of the camera frame that is sent for label reading.
 *
 * OCR reads every piece of text it can find and LabJarvis compares all of it
 * with the expected reagent - deliberately, so nothing it saw is hidden from
 * the comparison. A full webcam frame, though, also contains posters, screens,
 * clothing and keyboard legends, so a perfectly read label arrived as
 * "lab safety first sodium chloride hp 2024" and could never match; and because
 * that background text came and went between frames, a live scan never saw
 * three identical readings and kept asking the operator to hold still.
 *
 * Only this centred box is read. The same fractions draw the guide over the
 * preview, so what the operator lines up is exactly what is read.
 */
export const LABEL_AIM_BOX = { x: 0.15, y: 0.25, width: 0.7, height: 0.5 } as const;

export interface SourceRect {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
}

/** Pixel rectangle of the aim box within a frame of the given size. */
export function aimBoxRect(frameWidth: number, frameHeight: number): SourceRect {
  const sx = Math.round(frameWidth * LABEL_AIM_BOX.x);
  const sy = Math.round(frameHeight * LABEL_AIM_BOX.y);
  return {
    sx,
    sy,
    sw: Math.max(1, Math.round(frameWidth * LABEL_AIM_BOX.width)),
    sh: Math.max(1, Math.round(frameHeight * LABEL_AIM_BOX.height)),
  };
}

/** Capture the aim box of the current video frame as a JPEG. */
export function captureAimedFrame(video: HTMLVideoElement): Promise<Blob | null> {
  const { sx, sy, sw, sh } = aimBoxRect(video.videoWidth || 1280, video.videoHeight || 720);
  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = sh;
  const context = canvas.getContext("2d");
  if (context === null) return Promise.resolve(null);
  context.drawImage(video, sx, sy, sw, sh, 0, 0, sw, sh);
  return new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}
