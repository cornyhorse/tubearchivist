import APIClient from '../../functions/APIClient';

/** switch auto-download of a channel or playlist (off by default) */
const updateAutoDownload = async (kind: 'channel' | 'playlist', id: string, value: boolean) => {
  return APIClient(`/api/${kind}/${id}/`, {
    method: 'POST',
    body: { auto_download: value },
  });
};

export default updateAutoDownload;
