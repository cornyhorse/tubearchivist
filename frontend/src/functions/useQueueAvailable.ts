import { useEffect, useState } from 'react';
import { getQueueSummary, notBuilt } from '../api/ux/client';

let cached: Promise<boolean> | null = null;

/** whether the server has the work queue (jobs engine); asked once per page load */
const useQueueAvailable = () => {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    cached ??= getQueueSummary()
      .then(response => !notBuilt(response))
      .catch(() => false);
    cached.then(setAvailable);
  }, []);
  return available;
};

/** for tests */
export const resetQueueAvailable = () => {
  cached = null;
};

export default useQueueAvailable;
