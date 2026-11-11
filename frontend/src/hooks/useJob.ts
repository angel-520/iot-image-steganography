import { useCallback, useEffect, useRef, useState } from 'react';
import { errorCode, errorMessage, getTask, postTask } from '../services/api';
import type { AnyResult, Task } from '../types';

export function useJob() {
  const [task, setTask] = useState<Task<AnyResult>>();
  const [result, setResult] = useState<AnyResult>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [failureCode, setFailureCode] = useState<string>();
  const controller = useRef<AbortController>();
  const generation = useRef(0);
  useEffect(() => () => { controller.current?.abort(); generation.current += 1; }, []);
  const reset = useCallback(() => {
    controller.current?.abort(); generation.current += 1;
    setTask(undefined); setResult(undefined); setLoading(false); setError(undefined); setFailureCode(undefined);
  }, []);
  const run = useCallback(async (endpoint: string, payload: unknown): Promise<AnyResult | undefined> => {
    controller.current?.abort();
    const own = ++generation.current;
    const abort = new AbortController(); controller.current = abort;
    setTask(undefined); setResult(undefined); setError(undefined); setFailureCode(undefined); setLoading(true);
    try {
      let current = await postTask(endpoint, payload, abort.signal);
      const minutes = endpoint.startsWith('/multimodal/') ? 60 : 10;
      const deadline = Date.now() + minutes * 60_000;
      while (own === generation.current) {
        setTask(current);
        if (current.status === 'completed') { setResult(current.result); return current.result; }
        if (current.status === 'failed' || current.status === 'cancelled') {
          throw Object.assign(new Error(typeof current.error === 'string' ? current.error : current.error?.message ?? '任务未完成，请检查图片和参数。'), { code: typeof current.error === 'object' ? current.error?.code : undefined });
        }
        if (Date.now() > deadline) throw new Error(`任务等待超过 ${minutes} 分钟，可稍后重新检查或减少实验规模。`);
        await new Promise(resolve => window.setTimeout(resolve, 500));
        if (own !== generation.current) return;
        current = await getTask(current.id, abort.signal);
      }
    } catch (err) { if (!abort.signal.aborted && own === generation.current) { setError(errorMessage(err)); setFailureCode(errorCode(err)); } }
    finally { if (own === generation.current) setLoading(false); }
    return undefined;
  }, []);
  return { task, result, loading, error, failureCode, run, reset };
}
