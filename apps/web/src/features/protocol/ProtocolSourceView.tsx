import { useEffect, useMemo, useRef } from "react";
import { FileSearch } from "lucide-react";

import type { ProtocolSource } from "../../types";

function normalise(line: string): string {
  return line.trim().replace(/\s+/g, " ").toLowerCase();
}

interface ProtocolSourceViewProps {
  source: ProtocolSource | null;
  loading: boolean;
  error: string | null;
  /** The excerpt of the step the reviewer is currently on, if any. */
  highlightExcerpt: string | null;
}

/**
 * The complete extracted document, beside the structured steps.
 *
 * A one-line excerpt per step is enough to catch a typo and useless for the
 * thing review actually exists to catch: a paragraph of the original document
 * that was never turned into a step at all. The reviewer is asserting that the
 * structured steps *are* the protocol, and they cannot assert that against an
 * excerpt.
 *
 * This shows the extracted text, not the original PDF or DOCX. LabJarvis does
 * not retain the uploaded binary - keeping laboratory documents on disk is a
 * privacy and storage decision the project deliberately did not take - so the
 * extracted text is the most complete record that exists, and the limitation is
 * stated on screen rather than left for the reviewer to discover.
 */
export function ProtocolSourceView({
  source,
  loading,
  error,
  highlightExcerpt,
}: ProtocolSourceViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => (source === null ? [] : source.text.split("\n")), [source]);

  // An excerpt can span several source lines (a step with its sub-steps, or a
  // block of text that was not used as a step). The block is located by its
  // first line and highlighted for as many lines as it has.
  const [matchIndex, matchLength] = useMemo((): [number, number] => {
    if (highlightExcerpt === null) return [-1, 0];
    const excerptLines = highlightExcerpt
      .split("\n")
      .map(normalise)
      .filter((line) => line.length > 0);
    const needle = excerptLines[0];
    if (needle === undefined) return [-1, 0];
    const normalised = lines.map(normalise);
    let start = normalised.findIndex((line) => line === needle);
    // Extraction normalises whitespace and drops rendered list indentation, so
    // an excerpt line may be a substring of the source line rather than equal.
    if (start === -1) start = normalised.findIndex((line) => line.includes(needle));
    return start === -1 ? [-1, 0] : [start, excerptLines.length];
  }, [highlightExcerpt, lines]);

  const isMatch = (index: number) =>
    matchIndex !== -1 && index >= matchIndex && index < matchIndex + matchLength;

  useEffect(() => {
    if (matchIndex < 0 || containerRef.current === null) return;
    const target = containerRef.current.querySelector(`[data-source-line="${matchIndex}"]`);
    // Scrolling is a convenience. The matching line is marked either way, so an
    // environment without smooth scrolling still shows the reviewer where to
    // look rather than failing to render at all.
    if (target instanceof HTMLElement && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "center", behavior: "auto" });
    }
  }, [matchIndex]);

  return (
    <section className="source-document" aria-labelledby="source-document-title">
      <h2 id="source-document-title">
        <FileSearch aria-hidden="true" /> Source document
      </h2>
      {loading ? (
        <p aria-busy="true">Loading the extracted source...</p>
      ) : error !== null ? (
        <p role="alert">{error}</p>
      ) : source === null ? (
        <p>No extracted source text is stored for this version.</p>
      ) : (
        <>
          <p className="source-document-meta">
            {source.character_count.toLocaleString()} characters extracted. Checksum{" "}
            <code>{source.source_sha256.slice(0, 12)}</code>.
          </p>
          <p className="source-document-limitation">
            This is the text LabJarvis extracted. The uploaded file itself is not kept,
            so images, diagrams, and scanned pages are not shown here and must be
            checked against your own copy of the document.
          </p>
          <div className="source-document-body" ref={containerRef} tabIndex={0}>
            {lines.map((line, index) => (
              <p
                // Source lines have no identity of their own; position is what
                // makes them addressable, and the list never reorders.
                key={`${index}-${line.slice(0, 24)}`}
                data-source-line={index}
                className={
                  isMatch(index)
                    ? "source-document-line source-document-line--match"
                    : "source-document-line"
                }
              >
                {index === matchIndex ? (
                  <span className="visually-hidden">Matches the selected item: </span>
                ) : null}
                {line || " "}
              </p>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
