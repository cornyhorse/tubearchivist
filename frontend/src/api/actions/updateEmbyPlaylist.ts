import APIClient from '../../functions/APIClient';

/** keep (or stop keeping) an Emby playlist for a playlist; on by default */
const updateEmbyPlaylist = async (id: string, value: boolean) => {
  return APIClient(`/api/playlist/${id}/`, {
    method: 'POST',
    body: { emby_playlist: value },
  });
};

export default updateEmbyPlaylist;
