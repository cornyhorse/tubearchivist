import APIClient from '../../functions/APIClient';

export type EmbyTagsResponse = {
  tags: string[];
};

/** the Emby tag names to offer on channels and playlists */
const loadEmbyTags = async () => {
  return APIClient<EmbyTagsResponse>('/api/emby/tags/');
};

export default loadEmbyTags;
