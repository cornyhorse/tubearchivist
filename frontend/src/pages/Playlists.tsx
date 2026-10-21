import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';

import iconAdd from '/img/icon-add.svg';

import { OutletContextType } from './Base';
import loadPlaylistList, { PlaylistsResponseType } from '../api/loader/loadPlaylistList';
import Pagination from '../components/Pagination';
import PlaylistList from '../components/PlaylistList';
import updateBulkPlaylistSubscriptions from '../api/actions/updateBulkPlaylistSubscriptions';
import createCustomPlaylist from '../api/actions/createCustomPlaylist';
import ScrollToTopOnNavigate from '../components/ScrollToTop';
import Button from '../components/Button';
import Modal from '../components/ux/Modal';
import { BulkBar, FilterChips, ToolbarButton, ViewToggle } from '../components/ux/Controls';
import PlaylistTable from '../components/PlaylistTable';
import updatePlaylistSubscription from '../api/actions/updatePlaylistSubscription';
import updateAutoDownload from '../api/actions/updateAutoDownload';
import { ViewStylesEnum, ViewStylesType } from '../configuration/constants/ViewStyle';
import useIsAdmin from '../functions/useIsAdmin';
import { useUserConfigStore } from '../stores/UserConfigStore';
import Notifications from '../components/Notifications';
import updateUserConfig, { UserConfigType } from '../api/actions/updateUserConfig';
import { ApiResponseType } from '../functions/APIClient';

type AutoFilter = 'any' | 'on' | 'off';

const AUTO_FILTER_VALUE: Record<AutoFilter, boolean | null> = { any: null, on: true, off: false };

const Playlists = () => {
  const { userConfig, setUserConfig } = useUserConfigStore();
  const { currentPage, setCurrentPage } = useOutletContext() as OutletContextType;
  const isAdmin = useIsAdmin();

  const [showAddForm, setShowAddForm] = useState(false);
  const [refresh, setRefresh] = useState(false);
  const [showNotification, setShowNotification] = useState(false);
  const [playlistsToAddText, setPlaylistsToAddText] = useState('');
  const [customPlaylistsToAddText, setCustomPlaylistsToAddText] = useState('');
  const [autoFilter, setAutoFilter] = useState<AutoFilter>('any');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const [playlistResponse, setPlaylistReponse] = useState<ApiResponseType<PlaylistsResponseType>>();

  const { data: playlistResponseData } = playlistResponse ?? {};

  const playlistList = playlistResponseData?.data;
  const pagination = playlistResponseData?.paginate;

  const viewStyle = userConfig.view_style_playlist;
  const isTable = viewStyle === ViewStylesEnum.Table;
  const showSubedOnly = userConfig.show_subed_only_playlists;

  useEffect(() => {
    (async () => {
      const playlist = await loadPlaylistList({
        page: currentPage,
        subscribed: showSubedOnly,
        autoDownload: AUTO_FILTER_VALUE[autoFilter],
      });

      setPlaylistReponse(playlist);
      setRefresh(false);
      setShowNotification(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    refresh,
    userConfig.show_subed_only_playlists,
    autoFilter,
    currentPage,
    pagination?.current_page,
  ]);

  const selectedIds = Array.from(selected);

  /** one update per selected playlist, then reload */
  const bulkUpdate = async (update: (id: string) => Promise<unknown>) => {
    await Promise.all(selectedIds.map(update));
    setSelected(new Set());
    setRefresh(true);
  };

  const handleUserConfigUpdate = async (config: Partial<UserConfigType>) => {
    const updatedUserConfig = await updateUserConfig(config);
    const { data: updatedUserConfigData } = updatedUserConfig;

    if (updatedUserConfigData) {
      setUserConfig(updatedUserConfigData);
    }
  };

  return (
    <>
      <title>TA | Playlists</title>
      <ScrollToTopOnNavigate />
      <div className={`boxed-content ${isTable ? 'ux-wide' : ''}`}>
        <div className="title-split">
          <div className="title-bar">
            <h1>Playlists</h1>
          </div>
          {isAdmin && (
            <div className="ux-toolbar">
              <ToolbarButton
                label="Add playlists…"
                help="Subscribe to YouTube playlists by link, or make your own playlist."
                icon={iconAdd}
                primary
                onClick={() => setShowAddForm(true)}
              />
            </div>
          )}
        </div>

        {showAddForm && (
          <Modal title="Add playlists" onClose={() => setShowAddForm(false)}>
            <label htmlFor="subscribe-playlists">
              Subscribe to YouTube playlists, one link or ID per line. Subscribed playlists are
              checked for new videos on the schedule.
            </label>
            <textarea
              id="subscribe-playlists"
              value={playlistsToAddText}
              onChange={event => setPlaylistsToAddText(event.target.value)}
              rows={3}
              placeholder="https://www.youtube.com/playlist?list=…"
            />
            <div className="button-box">
              <Button
                label="Subscribe"
                type="submit"
                onClick={async () => {
                  if (playlistsToAddText.trim()) {
                    await updateBulkPlaylistSubscriptions(playlistsToAddText, true);
                    setShowNotification(true);
                    setShowAddForm(false);
                  }
                }}
              />
            </div>
            <label htmlFor="custom-playlist">Or make your own playlist, by name:</label>
            <input
              id="custom-playlist"
              type="text"
              placeholder="Playlist name"
              value={customPlaylistsToAddText}
              onChange={event => setCustomPlaylistsToAddText(event.target.value)}
            />
            <div className="button-box">
              <Button
                label="Create playlist"
                type="submit"
                onClick={async () => {
                  if (customPlaylistsToAddText.trim()) {
                    await createCustomPlaylist(customPlaylistsToAddText);
                    setShowAddForm(false);
                    setRefresh(true);
                  }
                }}
              />
            </div>
          </Modal>
        )}

        <Notifications
          pageName="all"
          update={showNotification}
          setShouldRefresh={isDone => {
            if (!isDone) {
              setRefresh(true);
            }
          }}
        />

        <div className="ux-filterbar">
          <FilterChips
            label="Show"
            options={[
              { value: 'all', label: 'All' },
              { value: 'true', label: 'Subscribed' },
              { value: 'false', label: 'Not subscribed' },
            ]}
            value={
              userConfig.show_subed_only_playlists === null
                ? 'all'
                : String(userConfig.show_subed_only_playlists)
            }
            onChange={value => {
              handleUserConfigUpdate({
                show_subed_only_playlists: value === 'all' ? null : value === 'true',
              });
              setRefresh(true);
            }}
          />
          <FilterChips
            label="Auto-download"
            options={[
              { value: 'any', label: 'Any auto-download' },
              {
                value: 'on',
                label: 'Auto-download on',
                help: 'Playlists whose new videos download as soon as a check finds them',
              },
              { value: 'off', label: 'Auto-download off' },
            ]}
            value={autoFilter}
            onChange={value => {
              setAutoFilter(value);
              setSelected(new Set());
              setCurrentPage(0);
            }}
          />
          <ViewToggle
            views={['table', 'grid', 'list']}
            value={viewStyle}
            onChange={view =>
              handleUserConfigUpdate({ view_style_playlist: view as ViewStylesType })
            }
          />
        </div>

        {isAdmin && isTable && (
          <BulkBar count={selected.size} noun="playlist" onClear={() => setSelected(new Set())}>
            <button
              type="button"
              title="New videos a check finds download at once, within the rule"
              onClick={() => bulkUpdate(id => updateAutoDownload('playlist', id, true))}
            >
              Auto-download on
            </button>
            <button
              type="button"
              title="New videos wait in Found videos until you download them"
              onClick={() => bulkUpdate(id => updateAutoDownload('playlist', id, false))}
            >
              Auto-download off
            </button>
            <button
              type="button"
              title="Check the selected playlists for new videos on the schedule"
              onClick={() => bulkUpdate(id => updatePlaylistSubscription(id, true))}
            >
              Subscribe
            </button>
            <button
              type="button"
              title="Stop checking the selected playlists. Downloaded videos stay."
              onClick={() => bulkUpdate(id => updatePlaylistSubscription(id, false))}
            >
              Unsubscribe
            </button>
          </BulkBar>
        )}

        {isTable ? (
          playlistList === undefined ? null : (
            <PlaylistTable
              playlists={playlistList}
              isAdmin={isAdmin}
              selected={selected}
              onSelectedChange={setSelected}
              onToggleSubscribe={async playlist => {
                await updatePlaylistSubscription(
                  playlist.playlist_id,
                  !playlist.playlist_subscribed,
                );
                setRefresh(true);
              }}
              onChanged={() => setRefresh(true)}
            />
          )
        ) : (
          <div className={`playlist-list ${viewStyle}`}>
            <PlaylistList playlistList={playlistList} setRefresh={setRefresh} />
          </div>
        )}
      </div>

      <div className="boxed-content">
        {pagination && <Pagination pagination={pagination} setPage={setCurrentPage} />}
      </div>
    </>
  );
};

export default Playlists;
