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
import { FilterChips, ToolbarButton, ViewToggle } from '../components/ux/Controls';
import useIsAdmin from '../functions/useIsAdmin';
import { useUserConfigStore } from '../stores/UserConfigStore';
import Notifications from '../components/Notifications';
import updateUserConfig, { UserConfigType } from '../api/actions/updateUserConfig';
import { ApiResponseType } from '../functions/APIClient';

const Playlists = () => {
  const { userConfig, setUserConfig } = useUserConfigStore();
  const { currentPage, setCurrentPage } = useOutletContext() as OutletContextType;
  const isAdmin = useIsAdmin();

  const [showAddForm, setShowAddForm] = useState(false);
  const [refresh, setRefresh] = useState(false);
  const [showNotification, setShowNotification] = useState(false);
  const [playlistsToAddText, setPlaylistsToAddText] = useState('');
  const [customPlaylistsToAddText, setCustomPlaylistsToAddText] = useState('');

  const [playlistResponse, setPlaylistReponse] = useState<ApiResponseType<PlaylistsResponseType>>();

  const { data: playlistResponseData } = playlistResponse ?? {};

  const playlistList = playlistResponseData?.data;
  const pagination = playlistResponseData?.paginate;

  const viewStyle = userConfig.view_style_playlist;
  const showSubedOnly = userConfig.show_subed_only_playlists;

  useEffect(() => {
    (async () => {
      const playlist = await loadPlaylistList({
        page: currentPage,
        subscribed: showSubedOnly,
      });

      setPlaylistReponse(playlist);
      setRefresh(false);
      setShowNotification(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, userConfig.show_subed_only_playlists, currentPage, pagination?.current_page]);

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
      <div className="boxed-content">
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
          <ViewToggle
            views={['grid', 'list']}
            value={viewStyle}
            onChange={view => handleUserConfigUpdate({ view_style_playlist: view })}
          />
        </div>

        <div className={`playlist-list ${viewStyle}`}>
          <PlaylistList playlistList={playlistList} setRefresh={setRefresh} />
        </div>
      </div>

      <div className="boxed-content">
        {pagination && <Pagination pagination={pagination} setPage={setCurrentPage} />}
      </div>
    </>
  );
};

export default Playlists;
