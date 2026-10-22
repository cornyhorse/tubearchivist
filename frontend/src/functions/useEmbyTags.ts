import { useEffect, useState } from 'react';
import loadEmbyTags from '../api/loader/loadEmbyTags';

/** the Emby tags to offer; empty until loaded or if the server can't say */
const useEmbyTags = (): string[] => {
  const [tags, setTags] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const { data } = await loadEmbyTags();
        if (active && data?.tags) setTags(data.tags);
      } catch {
        // no tags offered
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  return tags;
};

export default useEmbyTags;
