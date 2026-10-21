import { Link } from 'react-router-dom';
import Routes from '../configuration/routes/RouteList';
import { PlaylistType } from '../api/loader/loadPlaylistById';
import formatDate from '../functions/formatDates';
import DataTable, { Column } from './ux/DataTable';
import AutoDownloadSwitch from './ux/AutoDownloadSwitch';

type PlaylistTableProps = {
  playlists: PlaylistType[];
  isAdmin?: boolean;
  selected: Set<string>;
  onSelectedChange: (selected: Set<string>) => void;
  onToggleSubscribe: (playlist: PlaylistType) => void;
  onChanged: () => void;
};

const isCustom = (playlist: PlaylistType) => playlist.playlist_type === 'custom';

const PlaylistTable = ({
  playlists,
  isAdmin,
  selected,
  onSelectedChange,
  onToggleSubscribe,
  onChanged,
}: PlaylistTableProps) => {
  const columns: Column<PlaylistType>[] = [
    {
      key: 'name',
      header: 'Playlist',
      className: 'ux-col-title',
      render: playlist => (
        <Link to={Routes.Playlist(playlist.playlist_id)}>{playlist.playlist_name}</Link>
      ),
    },
    {
      key: 'channel',
      header: 'Channel',
      render: playlist =>
        isCustom(playlist) ? (
          <span className="ux-muted">Your playlist</span>
        ) : (
          <Link to={Routes.Channel(playlist.playlist_channel_id)}>{playlist.playlist_channel}</Link>
        ),
    },
    {
      key: 'subscribed',
      header: 'Subscribed',
      help: 'Subscribed playlists are checked for new videos on the schedule.',
      render: playlist =>
        playlist.playlist_subscribed ? 'Yes' : <span className="ux-muted">No</span>,
    },
    {
      key: 'auto_download',
      header: 'Auto-download',
      help: 'On: new videos a check finds download at once, within the rule. Off: they wait in Found videos.',
      render: playlist =>
        isCustom(playlist) ? (
          <span className="ux-muted">—</span>
        ) : (
          <AutoDownloadSwitch
            kind="playlist"
            id={playlist.playlist_id}
            name={playlist.playlist_name}
            value={playlist.auto_download}
            canEdit={isAdmin}
            onSaved={onChanged}
          />
        ),
    },
    {
      key: 'last_refresh',
      header: 'Last checked',
      help: 'When TA last refreshed this playlist from YouTube.',
      render: playlist => formatDate(playlist.playlist_last_refresh),
    },
  ];

  if (isAdmin) {
    columns.push({
      key: 'actions',
      header: 'Actions',
      className: 'ux-col-actions',
      render: playlist =>
        isCustom(playlist) ? null : (
          <div className="ux-row-actions">
            <button
              type="button"
              className="ux-row-button"
              title={
                playlist.playlist_subscribed
                  ? 'Stop checking this playlist for new videos. Downloaded videos stay.'
                  : 'Check this playlist for new videos on the schedule.'
              }
              aria-label={`${playlist.playlist_subscribed ? 'Unsubscribe from' : 'Subscribe to'} ${playlist.playlist_name}`}
              onClick={() => onToggleSubscribe(playlist)}
            >
              {playlist.playlist_subscribed ? 'Unsubscribe' : 'Subscribe'}
            </button>
          </div>
        ),
    });
  }

  return (
    <DataTable
      caption="Playlists"
      rows={playlists}
      columns={columns}
      rowKey={playlist => playlist.playlist_id}
      rowLabel={playlist => playlist.playlist_name}
      selected={isAdmin ? selected : undefined}
      onSelectedChange={isAdmin ? onSelectedChange : undefined}
      empty="No playlists match."
    />
  );
};

export default PlaylistTable;
