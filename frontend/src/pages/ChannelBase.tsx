import { Link, Outlet, useOutletContext, useParams } from 'react-router-dom';
import Routes from '../configuration/routes/RouteList';
import { OutletContextType } from './Base';
import Notifications from '../components/Notifications';
import { useEffect, useState } from 'react';
import ChannelBanner from '../components/ChannelBanner';
import loadChannelNav, { ChannelNavResponseType } from '../api/loader/loadChannelNav';
import loadChannelById, { ChannelResponseType } from '../api/loader/loadChannelById';
import useIsAdmin from '../functions/useIsAdmin';
import { ApiResponseType } from '../functions/APIClient';
import NotFound from './NotFound';
import loadDefaultDownloadRule from '../api/loader/loadDownloadRule';
import { DownloadRuleType } from '../api/actions/updateDownloadRule';
import { NumberingBadge, RuleBadge } from '../components/ux/Badges';
import EmbyShowSelect, { EMBY_SHOW_HELP } from '../components/ux/EmbyShowSelect';
import AutoDownloadSwitch, { AUTO_DOWNLOAD_HELP } from '../components/ux/AutoDownloadSwitch';

type ChannelParams = {
  channelId: string;
};

const ChannelBase = () => {
  const { channelId } = useParams() as ChannelParams;
  const { currentPage, setCurrentPage } = useOutletContext() as OutletContextType;
  const isAdmin = useIsAdmin();

  const [channelResponse, setChannelResponse] = useState<ApiResponseType<ChannelResponseType>>();
  const [channelNav, setChannelNav] = useState<ApiResponseType<ChannelNavResponseType>>();
  const [startNotification, setStartNotification] = useState(false);
  const [defaultRule, setDefaultRule] = useState<DownloadRuleType | null>(null);
  const [refreshChannel, setRefreshChannel] = useState(0);

  const { data: channelResponseData, error: channelResponseError } = channelResponse ?? {};
  const { data: channelNavData } = channelNav ?? {};

  const channel = channelResponseData;
  const { has_streams, has_shorts, has_playlists, has_pending, has_ignored } = channelNavData || {};

  useEffect(() => {
    (async () => {
      const channelResponse = await loadChannelById(channelId);
      setChannelResponse(channelResponse);

      const channelNavResponse = await loadChannelNav(channelId);
      setChannelNav(channelNavResponse);
    })();
  }, [channelId]);

  useEffect(() => {
    if (refreshChannel === 0) return;
    (async () => {
      setChannelResponse(await loadChannelById(channelId));
    })();
  }, [channelId, refreshChannel]);

  useEffect(() => {
    if (!isAdmin) return;
    (async () => {
      const { data } = await loadDefaultDownloadRule();
      setDefaultRule(data ?? null);
    })();
  }, [isAdmin]);

  const errorMessage = channelResponseError?.error;

  if (errorMessage) {
    return <NotFound failType="channel" />;
  }

  if (!channelId) {
    return [];
  }

  return (
    <>
      <div className="boxed-content">
        <div className="channel-banner">
          <Link to={Routes.ChannelVideo(channelId)}>
            <ChannelBanner channelId={channelId} channelBannerUrl={channel?.channel_banner_url} />
          </Link>
        </div>
        {channel && (
          <div className="ux-header-badges">
            <span className="ux-header-name">{channel.channel_name}</span>
            <span className="ux-badge-label">Rule</span>
            <RuleBadge
              channelId={channelId}
              channelName={channel.channel_name}
              rule={channel.download_rule}
              defaultRule={defaultRule}
              canEdit={isAdmin}
              onSaved={() => setRefreshChannel(current => current + 1)}
            />
            <span className="ux-badge-label">Emby numbering</span>
            <NumberingBadge
              kind="channel"
              id={channelId}
              name={channel.channel_name}
              value={channel.emby_numbering}
              options={['date', 'year_frozen', 'absolute_frozen']}
              canEdit={isAdmin}
              onSaved={() => setRefreshChannel(current => current + 1)}
            />
            <span className="ux-badge-label" title={EMBY_SHOW_HELP}>
              Emby show
            </span>
            <EmbyShowSelect
              id={channelId}
              name={channel.channel_name}
              value={channel.emby_show}
              canEdit={isAdmin}
              onSaved={() => setRefreshChannel(current => current + 1)}
            />
            <span className="ux-badge-label" title={AUTO_DOWNLOAD_HELP}>
              Auto-download
            </span>
            <AutoDownloadSwitch
              kind="channel"
              id={channelId}
              name={channel.channel_name}
              value={channel.auto_download}
              canEdit={isAdmin}
              onSaved={() => setRefreshChannel(current => current + 1)}
            />
          </div>
        )}
        <div className="info-box-item child-page-nav">
          <Link to={Routes.ChannelVideo(channelId)}>
            <h3>Videos</h3>
          </Link>
          {has_streams && (
            <Link to={Routes.ChannelStream(channelId)}>
              <h3>Streams</h3>
            </Link>
          )}
          {has_shorts && (
            <Link to={Routes.ChannelShorts(channelId)}>
              <h3>Shorts</h3>
            </Link>
          )}
          {has_playlists && (
            <Link to={Routes.ChannelPlaylist(channelId)}>
              <h3>Playlists</h3>
            </Link>
          )}
          <Link to={Routes.ChannelAbout(channelId)}>
            <h3>About</h3>
          </Link>
          {has_pending && isAdmin && (
            <Link to={Routes.DownloadsByChannelId(channelId)}>
              <h3>Downloads</h3>
            </Link>
          )}
          {has_ignored && isAdmin && (
            <Link to={Routes.IgnoredByChannelId(channelId)}>
              <h3>Ignored</h3>
            </Link>
          )}
        </div>

        <Notifications
          pageName="channel"
          includeReindex={true}
          update={startNotification}
          setShouldRefresh={() => setStartNotification(false)}
        />
      </div>

      <Outlet
        context={{
          currentPage,
          setCurrentPage,
          startNotification,
          setStartNotification,
        }}
      />
    </>
  );
};

export default ChannelBase;
