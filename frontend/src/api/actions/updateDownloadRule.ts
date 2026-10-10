import APIClient from '../../functions/APIClient';

export type VideoTypeOption = 'videos' | 'streams' | 'shorts';

export type DownloadRuleType = {
  enabled: boolean;
  min_duration: number | null;
  max_duration: number | null;
  vid_types: VideoTypeOption[];
  title_include: string;
  title_exclude: string;
  published_after: string | null;
  action: 'ignore' | 'skip';
};

/** channelId undefined: the default rule for channels without their own */
const updateDownloadRule = async (channelId: string | undefined, rule: DownloadRuleType | null) => {
  if (channelId) {
    return APIClient(`/api/channel/${channelId}/`, {
      method: 'POST',
      body: { download_rule: rule },
    });
  }

  if (rule === null) {
    return APIClient('/api/download/rule/', { method: 'DELETE' });
  }

  return APIClient<DownloadRuleType>('/api/download/rule/', {
    method: 'POST',
    body: rule,
  });
};

export default updateDownloadRule;
