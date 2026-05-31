import { useState, useEffect, useCallback, useRef } from "react";
import {
  PdfLoader,
  PdfHighlighter,
  Highlight,
  Popup,
} from "react-pdf-highlighter";
import type { IHighlight } from "react-pdf-highlighter";

import "react-pdf-highlighter/dist/style.css";
import "./PdfViewer.css";

// Definitive stable worker URL
const workerUrl = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs`;

interface PdfViewerProps {
  url: string;
  pageNumber: number;
  searchSnippet?: string;
}

export function PdfViewer({ url, pageNumber, searchSnippet }: PdfViewerProps) {
  const [highlights, setHighlights] = useState<Array<IHighlight>>([]);
  const [loadedPdf, setLoadedPdf] = useState<any>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Store the scrollTo function from PdfHighlighter's scrollRef.
  // We must NOT call scrollTo inside scrollRef itself — the pdf.js viewer's
  // internal getPageView map is not ready at that point and will throw:
  //   "Cannot read properties of undefined (reading 'getPageView')"
  // Instead we store it and trigger it from a useEffect once highlights are set.
  const scrollToRef = useRef<((h: IHighlight) => void) | null>(null);

  // Normalise text for robust matching: collapse whitespace, strip non-breaking
  // spaces and common PDF ligature glyphs so snippet words reliably match the
  // text layer regardless of how the PDF was exported.
  const normalizeText = (t: string) =>
    t
      .replace(/ /g, " ")   // non-breaking space
      .replace(/ﬀ/g, "ff")  // ﬀ
      .replace(/ﬁ/g, "fi")  // ﬁ
      .replace(/ﬂ/g, "fl")  // ﬂ
      .replace(/ﬃ/g, "ffi") // ﬃ
      .replace(/ﬄ/g, "ffl") // ﬄ
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();

  // Common English stop-words that add noise to word-matching.
  const STOP_WORDS = new Set([
    "the", "and", "for", "this", "that", "with", "are", "not", "its",
    "shall", "may", "will", "been", "such", "any", "all", "has", "have",
  ]);

  const generateHighlightsForPage = useCallback(
    async (doc: any, pageNum: number, snippet: string) => {
      if (!doc || !snippet) return;

      try {
        const page = await doc.getPage(pageNum);
        const textContent = await page.getTextContent();
        const viewport = page.getViewport({ scale: 1.0 });

        const searchWords = Array.from(
          new Set(
            normalizeText(snippet)
              .split(/[^a-z0-9]+/)
              // 3-char minimum catches short legal terms ("tax", "law", "fee")
              // while still filtering single letters and common noise
              .filter((w) => w.length >= 3 && !STOP_WORDS.has(w)),
          ),
        );

        if (searchWords.length === 0) {
          setHighlights([]);
          return;
        }

        const rects: any[] = [];
        textContent.items.forEach((item: any) => {
          const str = normalizeText(item.str || "");
          if (str && searchWords.some((w) => str.includes(w))) {
            const tx = item.transform[4];
            const ty = item.transform[5];
            // Prefer item.height; fall back to the transform scale component
            // (index 3 is the vertical scale in a PDF text matrix), then 12pt.
            const fontHeight =
              item.height || Math.abs(item.transform[3]) || 12;

            rects.push({
              x1: tx,
              y1: viewport.height - ty - fontHeight,
              x2: tx + (item.width || 100),
              y2: viewport.height - ty,
              width: viewport.width,
              height: viewport.height,
            });
          }
        });

        if (rects.length > 0) {
          setHighlights([
            {
              id: `rag-${pageNum}-${Date.now()}`,
              content: { text: snippet },
              position: {
                pageNumber: pageNum,
                // Use the first rect as the bounding box anchor for scroll targeting
                boundingRect: rects[0],
                // Cap at 20 rects — very common words could otherwise produce
                // hundreds of highlight boxes and tank render performance.
                rects: rects.slice(0, 20),
              },
              comment: { text: "Evidence Chunk", emoji: "⚖️" },
            },
          ]);
        } else {
          setHighlights([]);
        }
      } catch (e) {
        console.error("Highlight generation failed:", e);
      }
    },
    // normalizeText and STOP_WORDS are defined in component scope but are
    // stable (no captured state), so the empty dep array is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Re-generate highlights whenever the PDF doc, page, or snippet changes.
  useEffect(() => {
    if (loadedPdf) {
      generateHighlightsForPage(
        loadedPdf,
        pageNumber || 1,
        searchSnippet || "",
      );
    }
  }, [loadedPdf, pageNumber, searchSnippet, generateHighlightsForPage]);

  // Scroll to the first highlight after it has been set.
  // The setTimeout gives pdf.js one event-loop tick to finish rendering the
  // page views so that getPageView() returns a valid object instead of undefined.
  useEffect(() => {
    if (highlights.length === 0) return;

    const timer = setTimeout(() => {
      if (scrollToRef.current) {
        scrollToRef.current(highlights[0]);
      }
    }, 150);

    return () => clearTimeout(timer);
  }, [highlights]);

  // Reset state when the URL changes (new citation clicked).
  useEffect(() => {
    setLoadError(null);
    setLoadedPdf(null);
    setHighlights([]);
    scrollToRef.current = null;
  }, [url]);

  const cleanUrl = url.split("#")[0];

  return (
    <div className="pdf-viewer-wrapper">
      {loadError ? (
        <div className="pdf-error-container">
          <div className="pdf-error-title">PDF Engine Error</div>
          <div className="pdf-error-msg">{loadError}</div>
          <div className="pdf-error-hint">
            This usually means the document was not found or the connection
            timed out.
          </div>
        </div>
      ) : (
        <PdfLoader
          url={cleanUrl}
          workerSrc={workerUrl}
          beforeLoad={
            <div className="pdf-loading">
              Initializing Legal High-Fidelity Engine...
            </div>
          }
          errorMessage={
            <div className="pdf-error">
              Unexpected engine failure. (URL Type:{" "}
              {url.startsWith("blob") ? "Validated Blob" : "Remote Stream"})
            </div>
          }
          onError={(err: Error) => setLoadError(err.message)}
        >
          {(pdfDocument) => {
            // Sync the loaded document into state so our highlight effect fires.
            // We guard with !== to avoid triggering a re-render on every parent render.
            if (loadedPdf !== pdfDocument) {
              setLoadedPdf(pdfDocument);
            }

            return (
              <div className="pdf-highlighter-container">
                <PdfHighlighter
                  pdfDocument={pdfDocument}
                  enableAreaSelection={(event) => event.altKey}
                  onScrollChange={() => {}}
                  scrollRef={(scrollTo) => {
                    // Just store the function — never call it here.
                    // Calling it here crashes with getPageView undefined because
                    // the viewer hasn't mounted its pages yet.
                    scrollToRef.current = scrollTo;
                  }}
                  onSelectionFinished={() => {}}
                  highlightTransform={(
                    highlight,
                    index,
                    setTip,
                    hideTip,
                    _viewportToScaled,
                    _screenshot,
                    isScrolledTo,
                  ) => (
                    <Popup
                      popupContent={
                        <div className="highlight-popup">⚖️ Evidence Found</div>
                      }
                      onMouseOver={(p) => setTip(highlight, () => p)}
                      onMouseOut={hideTip}
                      key={index}
                    >
                      <Highlight
                        isScrolledTo={isScrolledTo}
                        position={highlight.position}
                        comment={highlight.comment}
                      />
                    </Popup>
                  )}
                  highlights={highlights}
                />
              </div>
            );
          }}
        </PdfLoader>
      )}
    </div>
  );
}
