import { useCallback, useEffect, useState } from 'react';
import { getAlgorithms, errorMessage } from '../services/api';
import type { Algorithm } from '../types';

export function useAlgorithms() {
  const [algorithms, setAlgorithms] = useState<Algorithm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const reload = useCallback(async () => {
    setLoading(true); setError(undefined);
    try { setAlgorithms((await getAlgorithms()).filter(algorithm => algorithm.available)); } catch (err) { setError(errorMessage(err)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return { algorithms, loading, error, reload };
}
