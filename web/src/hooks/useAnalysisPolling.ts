import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Curated, JobStatus } from "../types";

type PollingStatus = JobStatus["status"] | "idle";

export interface AnalysisPollingResult {
  start: (url: string) => void;
  status: PollingStatus;
  elapsed: number;
  result: Curated | null;
  error: string | null;
  jobId: string | null;
  reset: () => void;
}

export function useAnalysisPolling(): AnalysisPollingResult {
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<PollingStatus>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<Curated | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Refs hold interval ids so they can be reliably cleared even from async callbacks
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Prevents state updates after unmount
  const mountedRef = useRef(true);
  // Incremented on every new start/reset; async callbacks bail out if generation doesn't match,
  // preventing a stale callback from a previous run from clearing new timers.
  const generationRef = useRef(0);

  const clearTimers = useCallback(() => {
    if (elapsedTimerRef.current !== null) {
      clearInterval(elapsedTimerRef.current);
      elapsedTimerRef.current = null;
    }
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    generationRef.current++;
    clearTimers();
    setJobId(null);
    setStatus("idle");
    setElapsed(0);
    setResult(null);
    setError(null);
  }, [clearTimers]);

  const start = useCallback(
    (url: string) => {
      // Capture this run's generation before the async work begins
      const gen = ++generationRef.current;
      clearTimers();
      setJobId(null);
      setStatus("queued");
      setElapsed(0);
      setResult(null);
      setError(null);

      void (async () => {
        let jid: string;
        try {
          const resp = await api.analyze(url, "web");
          jid = resp.id;
        } catch (e) {
          if (mountedRef.current && generationRef.current === gen) {
            setError(e instanceof Error ? e.message : "Analysis failed");
            setStatus("error");
          }
          return;
        }

        if (!mountedRef.current || generationRef.current !== gen) return;
        setJobId(jid);

        const startTs = Date.now();

        // 1-second interval: update elapsed counter
        elapsedTimerRef.current = setInterval(() => {
          if (mountedRef.current && generationRef.current === gen) {
            setElapsed(Math.floor((Date.now() - startTs) / 1000));
          }
        }, 1000);

        // 2-second interval: poll job status
        // Closes over `jid` (the actual job id from the API response),
        // not the state variable, which hasn't updated within this tick yet.
        pollTimerRef.current = setInterval(() => {
          // Bail immediately if this interval belongs to a stale run
          if (generationRef.current !== gen) return;

          void (async () => {
            try {
              const pollResp = await api.status(jid);
              if (!mountedRef.current || generationRef.current !== gen) return;
              setStatus(pollResp.status);
              if (pollResp.status === "done") {
                setResult(pollResp.curated ?? null);
                clearTimers();
              } else if (pollResp.status === "error") {
                setError(pollResp.error ?? "Analysis failed");
                clearTimers();
              }
            } catch {
              // Network blip — keep polling; next tick will retry
            }
          })();
        }, 2000);
      })();
    },
    [clearTimers]
  );

  // Clean up BOTH timers on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimers();
    };
  }, [clearTimers]);

  return { start, status, elapsed, result, error, jobId, reset };
}
