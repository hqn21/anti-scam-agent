import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import ReportBody from "../components/ReportBody";
import type { Curated } from "../types";

export default function Report() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<Curated | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!id) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    api
      .detail(id)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => {
        setNotFound(true);
        setLoading(false);
      });
  }, [id]);

  if (loading) {
    return <p className="text-sm text-gray-500">載入中…</p>;
  }

  if (notFound || !data) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-gray-700">
          找不到報告，或分析尚未完成。
        </p>
        <Link
          to="/history"
          className="inline-block text-sm text-blue-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 rounded"
        >
          ← 返回歷史紀錄
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Link
        to="/history"
        className="inline-block text-sm text-blue-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 rounded"
      >
        ← 返回歷史紀錄
      </Link>
      <ReportBody data={data} />
    </div>
  );
}
