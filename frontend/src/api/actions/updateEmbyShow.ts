import APIClient from '../../functions/APIClient';

export type EmbyShowType = 'auto' | 'always' | 'never';

/** whether a channel has its own show in Emby; auto = only while subscribed */
const updateEmbyShow = async (channelId: string, value: EmbyShowType) => {
  return APIClient(`/api/channel/${channelId}/`, {
    method: 'POST',
    body: { emby_show: value },
  });
};

export default updateEmbyShow;
