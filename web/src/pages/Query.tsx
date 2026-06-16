import { useState } from "react";
import { Link } from "react-router-dom";
import ReportBody from "../components/ReportBody";
import { useAnalysisPolling } from "../hooks/useAnalysisPolling";

export default function Query() {
  const [url, setUrl] = useState("");
  const { start, status, elapsed, result, error, jobId, reset } =
    useAnalysisPolling();

  const isActive = status === "queued" || status === "running";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = url.trim();
    if (trimmed && !isActive) {
      start(trimmed);
    }
  };

  const handleReset = () => {
    reset();
    setUrl("");
  };

  return (
    <div className="p-6 space-y-6 max-w-3xl mx-auto">
      {/* Page heading */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">查詢</h1>
        <p className="mt-1 text-sm text-gray-500">
          輸入網址後，系統將啟動真實瀏覽器造訪並完整模擬使用者行為，分析過程約需
          1–3 分鐘。
        </p>
      </div>

      {/* URL input form */}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="輸入網址，例如 example.com"
          className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          disabled={isActive}
          aria-label="待查詢的網址"
        />
        <button
          type="submit"
          disabled={isActive || !url.trim()}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
        >
          開始檢查
        </button>
      </form>

      {/* Analysing — spinner card */}
      {isActive && (
        <div className="bg-white border border-gray-200 rounded-lg p-8 flex flex-col items-center gap-4 shadow-sm">
          {/* CSS-only spinner via Tailwind */}
          <div className="w-10 h-10 rounded-full border-4 border-blue-100 border-t-blue-600 animate-spin" />
          <p className="text-base font-medium text-gray-800">
            {status === "queued" ? "排隊中" : "分析中"}…{" "}
            <span className="tabular-nums">{elapsed}s</span>
          </p>
          <p className="text-sm text-gray-500 text-center">
            真實瀏覽器造訪需要一點時間，請耐心等候（通常 1–3 分鐘）。
          </p>
        </div>
      )}

      {/* Done — full report + actions */}
      {status === "done" && result && (
        <div className="space-y-4">
          {/* Actions strip */}
          <div className="flex flex-wrap gap-3">
            <Link
              to={`/report/${jobId}`}
              className="inline-flex items-center px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 transition-colors"
            >
              查看完整報告
            </Link>
            <button
              onClick={handleReset}
              className="px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 hover:bg-gray-50 transition-colors"
            >
              再查一個
            </button>
          </div>

          {/* Full report body */}
          <ReportBody data={result} />
        </div>
      )}

      {/* Error card */}
      {status === "error" && error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 space-y-3 shadow-sm">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={handleReset}
            className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-md hover:bg-red-700 transition-colors"
          >
            重新查詢
          </button>
        </div>
      )}
    </div>
  );
}
