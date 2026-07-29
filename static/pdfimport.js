/* Rasterise the first page of a PDF to a PNG, so a PDF schematic can be traced
   over exactly like an image — the rest of the trace machinery only ever sees
   a raster.

   PDF.js is vendored under static/vendor/pdfjs (not a CDN) and imported here
   lazily: this module — and the ~1.6 MB library behind it — loads only when
   someone actually opens a PDF, so the app stays a no-build, offline,
   Flask-only tool for everyone who never does. */

const WORKER = "/static/vendor/pdfjs/pdf.worker.min.mjs";
const TARGET_LONGEST = 2000;   // px on the longer side: crisp to trace, bounded

let pdfjs = null;
async function lib() {
  if (!pdfjs) {
    pdfjs = await import("./vendor/pdfjs/pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = WORKER;
  }
  return pdfjs;
}

export function isPdf(file) {
  return file.type === "application/pdf" || /\.pdf$/i.test(file.name || "");
}

/** The first page as a PNG data URL, plus its pixel size. Rendered onto a white
 *  backing so a page with a transparent background isn't invisible on the dark
 *  canvas, and scaled so the longer side is about TARGET_LONGEST px. */
export async function pdfFirstPageToPng(file) {
  const data = new Uint8Array(await file.arrayBuffer());
  const pdfjsLib = await lib();
  const pdf = await pdfjsLib.getDocument({ data }).promise;
  try {
    const page = await pdf.getPage(1);
    const unit = page.getViewport({ scale: 1 });
    const scale = Math.min(4, Math.max(1, TARGET_LONGEST /
      Math.max(unit.width, unit.height)));
    const viewport = page.getViewport({ scale });
    const cv = document.createElement("canvas");
    cv.width = Math.ceil(viewport.width);
    cv.height = Math.ceil(viewport.height);
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, cv.width, cv.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    return { dataUrl: cv.toDataURL("image/png"),
             width: cv.width, height: cv.height };
  } finally {
    if (pdf.cleanup) pdf.cleanup();
    if (pdf.destroy) pdf.destroy();
  }
}
