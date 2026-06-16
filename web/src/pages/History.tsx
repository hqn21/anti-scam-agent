import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { AnalysisRow } from "../types";
import VerdictBadge from "../components/VerdictBadge";
import type { Verdict } from "../types";

const sourceLabel: Record<string, string> = {
  web: "網站",
  extension: "擴充",
  cli: "CLI",
};

function StatusPill({ status }: { status: string }) {
  if (status === "queued") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
        排隊中
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-600">
        分析中
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-600">
        錯誤
      </span>
    );
  }
  return null;
}

export default function History() {
  const [rows, setRows] = useState<AnalysisRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const navigate = useNavigate();

  const fetchList = () => {
    setLoading(true);
    setError(false);
    api
      .list()
      .then((data) => {
        setRows(data);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  };

  useEffect(() => {
    fetchList();
  }, []);

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-800">歷史紀錄</h1>
        <button
          onClick={fetchList}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
        >
          重新整理
        </button>
      </div>

      {loading && (
        <p className="text-gray-500 text-center py-12">載入中…</p>
      )}

      {!loading && error && (
        <p className="text-red-500 text-center py-12">載入失敗</p>
      )}

      {!loading && !error && rows !== null && rows.length === 0 && (
        <p className="text-gray-400 text-center py-12">尚無分析紀錄。</p>
      )}

      {!loading && !error && rows !== null && rows.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">
                  時間
                </th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">
                  網域
                </th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">
                  判定
                </th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">
                  來源
                </th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">
                  連結
                </th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600 whitespace-nowrap">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row) => {
                const isDone = row.status === "done";
                return (
                  <tr
                    key={row.id}
                    onClick={isDone ? () => navigate(`/report/${row.id}`) : undefined}
                    className={`${
                      isDone
                        ? "cursor-pointer hover:bg-gray-50"
                        : ""
                    } transition-colors`}
                  >
                    <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                      {new Date(row.created_at).toLocaleString("zh-TW")}
                    </td>
                    <td className="px-4 py-3 font-medium text-gray-800 whitespace-nowrap">
                      {row.domain}
                    </td>
                    <td className="px-4 py-3">
                      {isDone && row.verdict !== null ? (
                        <VerdictBadge verdict={row.verdict as Verdict} />
                      ) : (
                        <StatusPill status={row.status} />
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                      {sourceLabel[row.source] ?? row.source}
                    </td>
                    <td className="px-4 py-3 max-w-xs truncate text-gray-500" title={row.url}>
                      {row.url}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {isDone ? (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/report/${row.id}`);
                          }}
                          className="text-blue-600 hover:underline text-sm font-medium"
                        >
                          查看
                        </button>
                      ) : (
                        <span className="text-gray-300 text-sm">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
