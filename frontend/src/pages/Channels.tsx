import { DownloadRuleType } from '../api/actions/updateDownloadRule';
import updateDownloadRule from '../api/actions/updateDownloadRule';
import updateEmbyNumbering, {
  EMBY_NUMBERING_LABELS,
  EmbyNumberingType,
} from '../api/actions/updateEmbyNumbering';
import { useOutletContext } from 'react-router-dom';
import loadChannelList, {
  ChannelListFilter,
  ChannelSortKey,
  ChannelsListResponse,
} from '../api/loader/loadChannelList';
import loadDefaultDownloadRule from '../api/loader/loadDownloadRule';
import iconAdd from '/img/icon-add.svg';
import { useCallback, useEffect, useState } from 'react';
import Pagination from '../components/Pagination';
import { OutletContextType } from './Base';
import ChannelList from '../components/ChannelList';
import ChannelTable from '../components/ChannelTable';
import ScrollToTopOnNavigate from '../components/ScrollToTop';
import Notifications from '../components/Notifications';
import Button from '../components/Button';
import updateBulkChannelSubscriptions from '../api/actions/updateBulkChannelSubscriptions';
import updateChannelSubscription from '../api/actions/updateChannelSubscription';
import useIsAdmin from '../functions/useIsAdmin';
import { useUserConfigStore } from '../stores/UserConfigStore';
import updateUserConfig, { UserConfigType } from '../api/actions/updateUserConfig';
import { ApiResponseType } from '../functions/APIClient';
import { ViewStylesEnum, ViewStylesType } from '../configuration/constants/ViewStyle';
import {
  BulkBar,
  ChipOption,
  FilterChips,
  SearchBox,
  ToolbarButton,
  ViewToggle,
} from '../components/ux/Controls';
import { SortState } from '../components/ux/DataTable';
import Modal from '../components/ux/Modal';
import DownloadRuleForm from '../components/DownloadRuleForm';

type ChannelOverwritesType = {
  download_format: string | null;
  autodelete_days: number | null;
  index_playlists: boolean | null;
  integrate_sponsorblock: boolean | null;
  subscriptions_channel_size: number | null;
  subscriptions_live_channel_size: number | null;
  subscriptions_shorts_channel_size: number | null;
};

export type ChannelCountsType = {
  downloaded: number;
  queued: number;
  ignored: number;
  errors: number;
};

export type ChannelType = {
  channel_active: boolean;
  channel_banner_url: string;
  channel_description: string;
  channel_id: string;
  channel_last_refresh: string;
  channel_name: string;
  channel_overwrites?: ChannelOverwritesType;
  channel_subs: number;
  channel_subscribed: boolean;
  channel_tags?: string[];
  channel_thumb_url: string;
  channel_tvart_url: string;
  emby_numbering?: EmbyNumberingType;
  download_rule?: DownloadRuleType | null;
  /** list view only: per-channel counts of videos */
  counts?: ChannelCountsType;
  /** list view only: newest upload among the channel's videos */
  latest_upload?: string | null;
};

type QuickFilter = 'all' | ChannelListFilter;

const QUICK_FILTERS: ChipOption<QuickFilter>[] = [
  { value: 'all', label: 'All' },
  { value: 'subscribed', label: 'Subscribed' },
  { value: 'unsubscribed', label: 'Not subscribed' },
  { value: 'has_queued', label: 'Has queued videos', help: 'Found videos waiting to download' },
  { value: 'has_rule', label: 'Has a rule', help: 'Channels with their own download rule' },
  { value: 'has_errors', label: 'Has errors', help: 'Channels with failed downloads' },
];

const TABLE_PAGE_SIZE = 100;

const filterFromConfig = (showSubscribed: boolean | null): QuickFilter => {
  if (showSubscribed === null) return 'all';
  return showSubscribed ? 'subscribed' : 'unsubscribed';
};

/** run an update for every id and report the worst status */
const forEachChannel = async (
  ids: string[],
  update: (id: string) => Promise<{ status: number }>,
): Promise<{ status: number }> => {
  const results = await Promise.all(ids.map(update));
  return { status: Math.max(0, ...results.map(result => result.status)) };
};

const Channels = () => {
  const { userConfig, setUserConfig } = useUserConfigStore();
  const { currentPage, setCurrentPage } = useOutletContext() as OutletContextType;
  const isAdmin = useIsAdmin();

  const [channelListResponse, setChannelListResponse] =
    useState<ApiResponseType<ChannelsListResponse>>();
  const [defaultRule, setDefaultRule] = useState<DownloadRuleType | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [refresh, setRefresh] = useState(true);
  const [showNotification, setShowNotification] = useState(false);
  const [channelsToSubscribeTo, setChannelsToSubscribeTo] = useState('');

  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<QuickFilter>(filterFromConfig(userConfig.show_subed_only));
  const [sort, setSort] = useState<SortState>({ key: 'name', order: 'asc' });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkDialog, setBulkDialog] = useState<'rule' | 'numbering' | null>(null);
  const [bulkNumbering, setBulkNumbering] = useState<EmbyNumberingType>('date');

  const viewStyle = userConfig.view_style_channel;
  const isTable = viewStyle === ViewStylesEnum.Table;

  const { data: channelListResponseData } = channelListResponse ?? {};
  const channels = channelListResponseData?.data;
  const pagination = channelListResponseData?.paginate;

  const handleUserConfigUpdate = async (config: Partial<UserConfigType>) => {
    // switch at once; the server copy follows
    setUserConfig({ ...userConfig, ...config });
    try {
      const { data } = await updateUserConfig(config);
      if (data) setUserConfig(data);
    } catch (error) {
      console.error('could not save the view choice', error);
    }
  };

  const reload = useCallback(() => setRefresh(true), []);

  useEffect(() => {
    if (!isAdmin) return;
    (async () => {
      const { data } = await loadDefaultDownloadRule();
      setDefaultRule(data ?? null);
    })();
  }, [isAdmin]);

  useEffect(() => {
    (async () => {
      const response = await loadChannelList({
        page: currentPage,
        filter: filter === 'all' ? null : filter,
        q: query,
        sort: sort.key as ChannelSortKey,
        order: sort.order,
        pageSize: isTable ? TABLE_PAGE_SIZE : undefined,
      });

      setChannelListResponse(response);
      setShowNotification(false);
      setRefresh(false);
    })();
  }, [refresh, filter, query, sort, isTable, currentPage]);

  const changeFilter = (value: QuickFilter) => {
    setFilter(value);
    setSelected(new Set());
    setCurrentPage(0);
    if (value === 'all' || value === 'subscribed' || value === 'unsubscribed') {
      const show_subed_only = value === 'all' ? null : value === 'subscribed';
      if (show_subed_only !== userConfig.show_subed_only) {
        handleUserConfigUpdate({ show_subed_only });
      }
    }
  };

  const changeQuery = useCallback(
    (value: string) => {
      setQuery(value);
      setCurrentPage(0);
    },
    [setCurrentPage],
  );

  const selectedIds = Array.from(selected);

  const bulkSubscribe = async (subscribe: boolean) => {
    await updateBulkChannelSubscriptions(selectedIds.join('\n'), subscribe);
    if (subscribe) setShowNotification(true);
    setSelected(new Set());
    reload();
  };

  const totalHits = pagination?.total_hits;

  return (
    <>
      <title>TA | Channels</title>
      <ScrollToTopOnNavigate />
      <div className="boxed-content ux-wide">
        <div className="title-split">
          <div className="title-bar">
            <h1>Channels</h1>
          </div>
          {isAdmin && (
            <div className="ux-toolbar">
              <ToolbarButton
                label="Subscribe to channels…"
                help="Paste channel links or IDs. Subscribed channels are checked for new videos on the schedule."
                icon={iconAdd}
                primary
                onClick={() => setShowAddForm(true)}
              />
            </div>
          )}
        </div>

        {showAddForm && (
          <Modal title="Subscribe to channels" onClose={() => setShowAddForm(false)}>
            <label htmlFor="subscribe-channels">
              One channel per line: a channel link, its ID, or a link to one of its videos.
            </label>
            <textarea
              id="subscribe-channels"
              value={channelsToSubscribeTo}
              onChange={e => setChannelsToSubscribeTo(e.currentTarget.value)}
              rows={4}
              placeholder="https://www.youtube.com/@channel"
            />
            <div className="button-box">
              <Button
                label="Subscribe"
                type="submit"
                onClick={async () => {
                  if (channelsToSubscribeTo.trim()) {
                    await updateBulkChannelSubscriptions(channelsToSubscribeTo, true);
                    setChannelsToSubscribeTo('');
                    setShowNotification(true);
                    setShowAddForm(false);
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
          <SearchBox
            label="Search channels"
            placeholder="Search channels by name…"
            value={query}
            onChange={changeQuery}
          />
          <FilterChips
            label="Show"
            options={QUICK_FILTERS}
            value={filter}
            onChange={changeFilter}
          />
          <ViewToggle
            views={['table', 'grid', 'list']}
            value={viewStyle}
            onChange={view =>
              handleUserConfigUpdate({ view_style_channel: view as ViewStylesType })
            }
          />
        </div>

        <p className="ux-count" aria-live="polite">
          {channels === undefined
            ? 'Loading…'
            : `${totalHits ?? channels.length} channel${(totalHits ?? channels.length) === 1 ? '' : 's'}`}
          {query && ` matching “${query}”`}
        </p>

        {isAdmin && isTable && (
          <BulkBar count={selected.size} noun="channel" onClear={() => setSelected(new Set())}>
            <button
              type="button"
              title="Give the selected channels the same download rule"
              onClick={() => setBulkDialog('rule')}
            >
              Apply a rule…
            </button>
            <button
              type="button"
              title="Set how Emby numbers the episodes of the selected channels"
              onClick={() => setBulkDialog('numbering')}
            >
              Set numbering…
            </button>
            <button
              type="button"
              title="Check the selected channels for new videos on the schedule"
              onClick={() => bulkSubscribe(true)}
            >
              Subscribe
            </button>
            <button
              type="button"
              title="Stop checking the selected channels. Downloaded videos stay."
              onClick={() => bulkSubscribe(false)}
            >
              Unsubscribe
            </button>
          </BulkBar>
        )}

        {bulkDialog === 'rule' && (
          <Modal
            title={`Download rule for ${selected.size} channels`}
            onClose={() => setBulkDialog(null)}
          >
            <p className="ux-help">
              Saves this rule on every selected channel, replacing their own rules. “Remove rule”
              isn't offered here; to go back to the default rule, edit a channel's badge.
            </p>
            <DownloadRuleForm
              rule={null}
              saveLabel={`Save on ${selected.size} channels`}
              saveRule={rule => forEachChannel(selectedIds, id => updateDownloadRule(id, rule))}
              onSaved={() => {
                setBulkDialog(null);
                reload();
              }}
            />
          </Modal>
        )}

        {bulkDialog === 'numbering' && (
          <Modal
            title={`Emby numbering for ${selected.size} channels`}
            onClose={() => setBulkDialog(null)}
          >
            <label htmlFor="bulk-numbering">Number episodes</label>
            <select
              id="bulk-numbering"
              value={bulkNumbering}
              onChange={event => setBulkNumbering(event.target.value as EmbyNumberingType)}
            >
              {(['date', 'year_frozen', 'absolute_frozen'] as EmbyNumberingType[]).map(option => (
                <option key={option} value={option}>
                  {EMBY_NUMBERING_LABELS[option]}
                </option>
              ))}
            </select>
            <div className="button-box">
              <Button
                label={`Save on ${selected.size} channels`}
                onClick={async () => {
                  await forEachChannel(selectedIds, id =>
                    updateEmbyNumbering('channel', id, bulkNumbering),
                  );
                  setBulkDialog(null);
                  reload();
                }}
              />
            </div>
          </Modal>
        )}

        {isTable ? (
          channels === undefined ? null : (
            <ChannelTable
              channels={channels}
              defaultRule={defaultRule}
              isAdmin={isAdmin}
              sort={sort}
              onSortChange={next => {
                setSort(next);
                setCurrentPage(0);
              }}
              selected={selected}
              onSelectedChange={setSelected}
              onToggleSubscribe={async channel => {
                await updateChannelSubscription(channel.channel_id, !channel.channel_subscribed);
                reload();
              }}
              onChanged={reload}
            />
          )
        ) : (
          <div className={`channel-list ${viewStyle}`}>
            <ChannelList channelList={channels} refreshChannelList={setRefresh} />
          </div>
        )}

        {pagination && (
          <div className="boxed-content">
            <Pagination pagination={pagination} setPage={setCurrentPage} />
          </div>
        )}
      </div>
    </>
  );
};

export default Channels;
