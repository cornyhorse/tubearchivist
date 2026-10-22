import APIClient from '../../functions/APIClient';

/** replace the Emby tags of a channel or playlist; none hides it from tag-restricted Emby users */
const updateEmbyTags = async (kind: 'channel' | 'playlist', id: string, tags: string[]) => {
  return APIClient(`/api/${kind}/${id}/`, {
    method: 'POST',
    body: { emby_tags: tags },
  });
};

export default updateEmbyTags;
