import APIClient from '../../functions/APIClient';

export type EmbyNumberingType = 'date' | 'year_frozen' | 'absolute_frozen' | 'playlist_position';

export const EMBY_NUMBERING_LABELS: Record<EmbyNumberingType, string> = {
  date: 'Date: season = year, episode = MMDD×10+n',
  year_frozen: 'Year: season = year, numbered in order, never renumbered',
  absolute_frozen: 'Absolute: one season, numbered in order, never renumbered',
  playlist_position: 'Playlist position: follows the playlist order',
};

const updateEmbyNumbering = async (
  kind: 'channel' | 'playlist',
  id: string,
  numbering: EmbyNumberingType,
) => {
  return APIClient(`/api/${kind}/${id}/`, {
    method: 'POST',
    body: { emby_numbering: numbering },
  });
};

export default updateEmbyNumbering;
