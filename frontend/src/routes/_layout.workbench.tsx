import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import "./workbench.css";
import {
  ChatService,
  OpenAPI,
  type CitationOut,
  type ConversationPublic,
  type DocumentPublic,
  DocumentsService,
} from "@/client";
import { PdfViewer } from "@/components/PdfViewer/PdfViewer";

export const Route = createFileRoute("/_layout/workbench")({
  component: Workbench,
});

function Workbench() {
  const [query, setQuery] = useState("");
  const [chatLog, setChatLog] = useState<
    { role: string; content: React.ReactNode }[]
  >([
    {
      role: "assistant",
      content:
        "Hello! Select documents below and ask a question about your case.",
    },
  ]);
  const [documents, setDocuments] = useState<DocumentPublic[]>([]);
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfPage, setPdfPage] = useState<number>(1);
  const [pdfSnippet, setPdfSnippet] = useState<string>("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isPdfLoading, setIsPdfLoading] = useState(false);
  const [currentConversation, setCurrentConversation] =
    useState<ConversationPublic | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // ── Stable core helpers ───────────────────────────────────────────────────

  const loadPdfCitation = useCallback(async (citation: CitationOut) => {
    setIsPdfLoading(true);
    try {
      const { url } =
        await DocumentsService.getDocumentUrlApiV1DocumentsDocumentIdUrlGet({
          documentId: citation.document_id,
        });

      let blob: Blob | null = null;
      const token = await (OpenAPI.TOKEN as any)();

      // Step 1: Try direct fetch from storage (CORS-dependent)
      try {
        const response = await fetch(url);
        if (response.ok) blob = await response.blob();
      } catch {
        console.warn(
          "Direct storage access blocked by CORS. Falling back to backend proxy.",
        );
      }

      // Step 2: Fallback to Backend Proxy (CORS-safe)
      if (!blob) {
        const baseUrl = OpenAPI.BASE.replace(/\/api\/v1\/?$/, "");
        const proxyUrl = `${baseUrl}/api/v1/documents/${citation.document_id}/proxy`;
        const proxyRes = await fetch(proxyUrl, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (proxyRes.ok) blob = await proxyRes.blob();
      }

      if (blob) {
        const localUrl = URL.createObjectURL(blob);
        setPdfUrl(localUrl);
        setPdfPage(citation.page_number);
        setPdfSnippet(citation.snippet);
      } else {
        throw new Error("All document retrieval paths failed.");
      }
    } catch (e) {
      console.error("Critical error loading citation:", e);
      setChatLog((prev) => [
        ...prev,
        {
          role: "error",
          content:
            "Failed to retrieve document from storage. Please check backend logs.",
        },
      ]);
    } finally {
      setIsPdfLoading(false);
    }
  }, []);

  const formatAnswerWithCitations = useCallback(
    (text: string, citations: CitationOut[]) => {
      const parts = text.split(/(\[[^\[\]]+\])/g);
      return parts.map((part, index) => {
        if (part.startsWith("[") && part.endsWith("]")) {
          const innerText = part.slice(1, -1);
          const citation = citations.find(
            (c) => c.alias.toLowerCase() === innerText.toLowerCase(),
          );
          if (citation) {
            return (
              <button
                key={index}
                onClick={() => loadPdfCitation(citation)}
                className="citation-chip"
                type="button"
                title={`📄 ${citation.source_filename} — Page ${citation.page_number}\n\n"${citation.snippet}"`}
              >
                📄 {citation.alias}
              </button>
            );
          }
        }
        return <span key={index}>{part}</span>;
      });
    },
    [loadPdfCitation],
  );

  // ── Data fetching ─────────────────────────────────────────────────────────

  const fetchDocuments = useCallback(async () => {
    try {
      const data = await DocumentsService.listDocumentsApiV1DocumentsGet({
        limit: 50,
      });
      setDocuments(data.documents);
    } catch (e) {
      console.error("Failed to fetch documents", e);
    }
  }, []);

  /** Resume the most recent conversation or create a fresh one. */
  const initConversation = useCallback(async () => {
    try {
      const existing =
        await ChatService.listConversationsApiV1ChatConversationsGet({
          limit: 1,
        });
      if (existing.conversations.length > 0) {
        setCurrentConversation(existing.conversations[0]);
      } else {
        const conv =
          await ChatService.createConversationApiV1ChatConversationsPost({
            requestBody: { title: `Chat ${new Date().toLocaleTimeString()}` },
          });
        setCurrentConversation(conv);
      }
    } catch (e) {
      console.error("Failed to init conversation", e);
    }
  }, []);

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  useEffect(() => {
    fetchDocuments();
    initConversation();
    const interval = setInterval(fetchDocuments, 5000);
    return () => clearInterval(interval);
  }, [fetchDocuments, initConversation]);

  /** Load message history whenever the active conversation changes. */
  useEffect(() => {
    if (!currentConversation) return;

    const loadHistory = async () => {
      try {
        const { messages } =
          await ChatService.getMessagesApiV1ChatConversationsConversationIdMessagesGet(
            {
              conversationId: currentConversation.id,
            },
          );

        if (messages.length === 0) return;

        const mapped = messages.map((msg) => {
          let content: React.ReactNode = msg.content;
          if (msg.role === "assistant" && msg.citations_json) {
            try {
              const citations = JSON.parse(msg.citations_json) as CitationOut[];
              content = formatAnswerWithCitations(msg.content, citations);
            } catch {
              // keep raw text if parsing fails
            }
          }
          return { role: msg.role, content };
        });

        setChatLog(mapped);
      } catch (e) {
        console.error("Failed to load message history", e);
      }
    };

    loadHistory();
  }, [currentConversation, formatAnswerWithCitations]);

  /** Auto-scroll chat to the bottom whenever the log or processing state changes. */
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatLog, isProcessing]);

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    try {
      await DocumentsService.uploadDocumentApiV1DocumentsUploadPost({
        formData: { file },
      });
      await fetchDocuments();
    } catch {
      alert("Upload failed.");
    } finally {
      setIsUploading(false);
      // reset so the same file can be re-uploaded if needed
      e.target.value = "";
    }
  };

  const toggleDoc = (id: string) => {
    setSelectedDocIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id],
    );
  };

  const handleQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !currentConversation) return;

    const userQuestion = query.trim();
    setChatLog((prev) => [...prev, { role: "user", content: userQuestion }]);
    setQuery("");

    // ── TEST/DEBUG MOCK: Bypass LLM to verify highlighting ──────────────────
    if (userQuestion.toLowerCase().startsWith("/mock")) {
      const docId =
        selectedDocIds.length > 0 ? selectedDocIds[0] : documents[0]?.id;
      const doc = documents.find((d) => d.id === docId);
      if (!doc) {
        setChatLog((prev) => [
          ...prev,
          { role: "error", content: "Upload a document first to test /mock." },
        ]);
        return;
      }
      const mockCitations: CitationOut[] = [
        {
          alias: `${doc.original_filename}, §MOCK`,
          chunk_id: "mock-123",
          document_id: doc.id,
          source_filename: doc.original_filename,
          page_number: 1,
          section_title: "MOCK",
          snippet: "This is a synthetic mock snippet for highlighting testing.",
        },
      ];
      const formatted = formatAnswerWithCitations(
        `This is a mock response citing ${doc.original_filename} to verify the highlighting engine works. [${doc.original_filename}, §MOCK]`,
        mockCitations,
      );
      setChatLog((prev) => [
        ...prev,
        { role: "assistant", content: formatted },
      ]);
      return;
    }

    setIsProcessing(true);
    try {
      const result =
        await ChatService.queryConversationApiV1ChatConversationsConversationIdQueryPost(
          {
            conversationId: currentConversation.id,
            requestBody: {
              question: userQuestion,
              document_ids: selectedDocIds.length > 0 ? selectedDocIds : null,
            },
          },
        );
      const formatted = formatAnswerWithCitations(
        result.answer,
        result.citations,
      );
      setChatLog((prev) => [
        ...prev,
        { role: "assistant", content: formatted },
      ]);
    } catch (err: any) {
      const msg: string =
        err.body?.detail || err.message || "Error communicating with backend.";
      const isQuota =
        msg.toLowerCase().includes("quota") ||
        msg.toLowerCase().includes("exhausted") ||
        msg.toLowerCase().includes("rate limit") ||
        err.status === 429;
      setChatLog((prev) => [
        ...prev,
        { role: isQuota ? "rate-limit" : "error", content: msg },
      ]);
    } finally {
      setIsProcessing(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleQuery(e as any);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  const scopeLabel =
    selectedDocIds.length === 0
      ? "All documents"
      : `${selectedDocIds.length} of ${documents.length} selected`;

  return (
    <div className="workbench-container">
      {/* ── LEFT PANEL ── */}
      <div className="workbench-left">
        {/* Document Shelf */}
        <div className="doc-shelf">
          <div className="shelf-header">
            <div className="shelf-title-group">
              <h3>Documents</h3>
              <span className="scope-badge">{scopeLabel}</span>
            </div>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              className="upload-btn"
            >
              {isUploading ? "Uploading…" : "+ Upload"}
            </button>
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileUpload}
              accept=".pdf"
              style={{ display: "none" }}
            />
          </div>

          <div className="doc-list">
            {documents.length === 0 ? (
              <p className="empty-msg">No documents yet.</p>
            ) : (
              documents.map((doc) => {
                const isSelected = selectedDocIds.includes(doc.id);
                return (
                  <div
                    key={doc.id}
                    className={`doc-item ${doc.status}${isSelected ? " selected" : ""}`}
                    onClick={() => toggleDoc(doc.id)}
                    role="checkbox"
                    aria-checked={isSelected}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === " " && toggleDoc(doc.id)}
                  >
                    <input
                      type="checkbox"
                      className="doc-checkbox"
                      checked={isSelected}
                      onChange={() => toggleDoc(doc.id)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <span
                      className="doc-name text-truncate"
                      title={doc.original_filename}
                    >
                      {doc.original_filename}
                    </span>
                    {doc.status !== "ready" && (
                      <span className="doc-status-badge">{doc.status}</span>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Chat History */}
        <div className="chat-history">
          {chatLog.map((log, i) => (
            <div key={i} className={`chat-bubble ${log.role}`}>
              {log.content}
            </div>
          ))}
          {isProcessing && (
            <div className="chat-bubble assistant processing">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          )}
          {/* Scroll anchor */}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <form onSubmit={handleQuery} className="chat-input-area">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              selectedDocIds.length > 0
                ? `Ask about ${selectedDocIds.length} selected document${selectedDocIds.length > 1 ? "s" : ""}… (Enter to send, Shift+Enter for newline)`
                : "Ask a question across all documents… (Enter to send)"
            }
            className="chat-textarea"
            rows={2}
          />
          <button
            type="submit"
            disabled={isProcessing || !query.trim()}
            className="chat-submit-btn"
          >
            {isProcessing ? "…" : "Send"}
          </button>
        </form>
      </div>

      {/* ── RIGHT PANEL ── */}
      <div className="workbench-right">
        {isPdfLoading ? (
          <div className="pdf-loading-overlay">
            <div className="pdf-loading-spinner" />
            <span>Loading Evidence…</span>
          </div>
        ) : pdfUrl ? (
          <PdfViewer
            url={pdfUrl}
            pageNumber={pdfPage}
            searchSnippet={pdfSnippet}
          />
        ) : (
          <div className="pdf-placeholder">
            <div className="placeholder-content">
              <div className="placeholder-icon">⚖️</div>
              <h3>No Evidence Selected</h3>
              <p>
                Click a citation chip in the chat to open the source document
                here.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
