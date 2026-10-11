import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import iconRescan from '/img/icon-rescan.svg';
import iconDownload from '/img/icon-download.svg';
import iconAdd from '/img/icon-add.svg';
import {
  createJob,
  foundAction,
  FoundActionName,
  FoundList,
  FoundTab,
  FoundVideo,
  listFound,
  notBuilt,
} from '../api/ux/client';
import updateTaskByName from '../api/actions/updateTaskByName';
import Routes from '../configuration/routes/RouteList';
import formatDate from '../functions/formatDates';
import { ago } from '../functions/timeText';
import useIsAdmin from '../functions/useIsAdmin';
import { useUserConfigStore } from '../stores/UserConfigStore';
import updateUserConfig, { UserConfigType } from '../api/actions/updateUserConfig';
import { ViewStylesType } from '../configuration/constants/ViewStyle';
import DataTable, { Column, SortState } from '../components/ux/DataTable';
import {
  BulkBar,
  ChipOption,
  FilterChips,
  SearchBox,
  Tabs,
  ToolbarButton,
  ViewToggle,
} from '../components/ux/Controls';
import VerdictBadge from '../components/rules/VerdictBadge';
import AddDialog from '../components/AddDialog';
import VideoThumbnail from '../components/VideoThumbail';
import ScrollToTopOnNavigate from '../components/ScrollToTop';
import LoadingIndicator from '../components/LoadingIndicator';
import Download from './Download';
import { Filters, NO_FILTERS, toApiFilter } from '../functions/foundFilter';

const PAGE_SIZE = 100;

const TYPE_CHIPS: ChipOption<'all' | 'videos' | 'streams' | 'shorts'>[] = [
  { value: 'all', label: 'Any type' },
  { value: 'videos', label: 'Videos' },
  { value: 'streams', label: 'Livestreams' },
  { value: 'shorts', label: 'Shorts' },
];

const TYPE_NAMES: Record<string, string> = {
  videos: 'Video',
  streams: 'Livestream',
  shorts: 'Short',
};

const STATE_TEXT = (video: FoundVideo) => {
  switch (video.state) {
    case 'waiting':
      return 'Waiting for you';
    case 'queued':
      return video.job?.position ? `Queued, #${video.job.position}` : 'Queued';
    case 'downloading':
      return video.job?.progress
        ? `Downloading ${Math.round(video.job.progress * 100)}%`
        : 'Downloading';
    case 'ignored':
      return 'Ignored';
    case 'failed':
      return 'Failed';
    default:
      return video.downloaded_at ? `Downloaded ${ago(video.downloaded_at)}` : 'Downloaded';
  }
};

/** row actions per tab, as [action, label, tooltip] */
const ROW_ACTIONS: Record<FoundTab, [FoundActionName, string, string][]> = {
  waiting: [
    ['download_now', 'Download now', 'Download this next, ahead of the queue'],
    ['ignore', 'Ignore', 'Set this aside; it won’t download'],
    ['forget', 'Forget', 'Remove from this list. A later check may find it again.'],
  ],
  ignored: [
    ['download_anyway', 'Download anyway', 'Download this even though a rule skipped it'],
    ['always_ignore', 'Always ignore', 'Never download this video, whatever the rules say'],
    ['forget', 'Forget', 'Remove from this list. A later check may find it again.'],
  ],
  failed: [
    ['clear_error', 'Try again', 'Clear the error and queue it again'],
    ['ignore', 'Ignore', 'Set this aside; it won’t download'],
    ['forget', 'Forget', 'Remove from this list'],
  ],
  downloaded: [],
};

const BULK_ACTIONS: Record<FoundTab, [FoundActionName, string][]> = {
  waiting: [
    ['download_now', 'Download'],
    ['ignore', 'Ignore'],
    ['forget', 'Forget'],
  ],
  ignored: [
    ['download_anyway', 'Download anyway'],
    ['always_ignore', 'Always ignore'],
    ['forget', 'Forget'],
  ],
  failed: [
    ['clear_error', 'Try again'],
    ['ignore', 'Ignore'],
    ['forget', 'Forget'],
  ],
  downloaded: [],
};

const FoundVideos = () => {
  const isAdmin = useIsAdmin();
  const [searchParams] = useSearchParams();
  const { userConfig, setUserConfig } = useUserConfigStore();

  // old links: /downloads/?channel=…&ignored=true
  const [tab, setTab] = useState<FoundTab>(
    searchParams.get('ignored') === 'true' ? 'ignored' : 'waiting',
  );
  const [filters, setFilters] = useState<Filters>({
    ...NO_FILTERS,
    channel: searchParams.get('channel') ?? '',
  });
  const [showMore, setShowMore] = useState(false);
  const [sort, setSort] = useState<SortState>({ key: 'found', order: 'desc' });
  const [page, setPage] = useState(1);
  const [response, setResponse] = useState<FoundList & { facets?: FoundFacets }>();
  const [legacy, setLegacy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [message, setMessage] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [checking, setChecking] = useState(false);

  const viewStyle: ViewStylesType = userConfig.view_style_downloads === 'grid' ? 'grid' : 'table';
  const apiFilter = useMemo(() => toApiFilter(tab, filters), [tab, filters]);

  const reload = useCallback(() => setNonce(current => current + 1), []);

  useEffect(() => {
    (async () => {
      const result = await listFound({
        ...apiFilter,
        sort: `${sort.order === 'desc' ? '-' : ''}${sort.key}`,
        page,
        page_size: PAGE_SIZE,
      });
      if (notBuilt(result)) {
        setLegacy(true);
        return;
      }
      if (result.data) setResponse(result.data as FoundList & { facets?: FoundFacets });
    })();
  }, [apiFilter, sort, page, nonce]);

  // keep queued and downloading rows moving while they're on screen
  const busy = response?.data.some(item => item.state === 'queued' || item.state === 'downloading');
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [busy, reload]);

  const changeFilters = (changes: Partial<Filters>) => {
    setFilters(current => ({ ...current, ...changes }));
    setSelected(new Set());
    setAllMatching(false);
    setPage(1);
  };

  const changeTab = (next: FoundTab) => {
    setTab(next);
    setSelected(new Set());
    setAllMatching(false);
    setPage(1);
  };

  const run = async (action: FoundActionName, ids?: string[]) => {
    const body = ids ? { action, ids } : { action, filter: apiFilter };
    const result = await foundAction(body);
    if (result.data) {
      setMessage(
        `${result.data.changed} of ${result.data.matched} video${result.data.matched === 1 ? '' : 's'} updated.`,
      );
    }
    setSelected(new Set());
    setAllMatching(false);
    reload();
  };

  if (legacy) return <Download />;

  const rows = response?.data ?? [];
  const counts = response?.counts;
  const total = response?.paginate?.total_hits ?? rows.length;
  const selectedCount = allMatching ? total : selected.size;
  const pageAllSelected = rows.length > 0 && rows.every(row => selected.has(row.youtube_id));

  const columns: Column<FoundVideo>[] = [
    {
      key: 'thumb',
      header: '',
      className: 'ux-col-thumb',
      render: video => <VideoThumbnail videoThumbUrl={video.thumb_url ?? undefined} />,
    },
    {
      key: 'title',
      header: 'Title',
      sortKey: 'title',
      className: 'ux-col-title',
      render: video => (
        <a
          href={`https://www.youtube.com/watch?v=${video.youtube_id}`}
          target="_blank"
          rel="noreferrer"
        >
          {video.title}
        </a>
      ),
    },
    {
      key: 'channel',
      header: 'Channel',
      sortKey: 'channel',
      render: video =>
        video.channel_indexed ? (
          <Link to={Routes.Channel(video.channel_id)}>{video.channel_name}</Link>
        ) : (
          video.channel_name
        ),
    },
    { key: 'type', header: 'Type', render: video => TYPE_NAMES[video.vid_type] ?? video.vid_type },
    {
      key: 'duration',
      header: 'Length',
      sortKey: 'duration',
      className: 'ux-num',
      render: video => video.duration,
    },
    {
      key: 'published',
      header: 'Published',
      sortKey: 'published',
      render: video => (video.published ? formatDate(video.published) : ''),
    },
    {
      key: 'found',
      header: 'Found',
      sortKey: 'found',
      help: 'When TA found it, and how',
      render: video => (
        <span title={formatDate(video.found_at)}>
          {video.found_by.label}
          <br />
          <span className="ux-muted">{ago(video.found_at)}</span>
        </span>
      ),
    },
    {
      key: 'verdict',
      header: 'Rule',
      help: 'What your rules say about this video. Click to see or change the rule.',
      render: video => (
        <VerdictBadge verdict={video.verdict} canEdit={isAdmin} onChanged={reload} />
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: video =>
        video.state === 'failed' ? (
          <span className="ux-failed" title={video.error ?? undefined}>
            Failed: {video.error}
          </span>
        ) : video.state === 'queued' || video.state === 'downloading' ? (
          <Link to={Routes.Queue}>{STATE_TEXT(video)}</Link>
        ) : (
          STATE_TEXT(video)
        ),
    },
  ];

  if (isAdmin && ROW_ACTIONS[tab].length > 0) {
    columns.push({
      key: 'actions',
      header: 'Actions',
      className: 'ux-col-actions',
      render: video => (
        <div className="ux-row-actions">
          {ROW_ACTIONS[tab].map(([action, label, help]) => (
            <button
              key={action}
              type="button"
              className="ux-row-button"
              title={help}
              aria-label={`${label}: ${video.title}`}
              onClick={() => run(action, [video.youtube_id])}
            >
              {label}
            </button>
          ))}
        </div>
      ),
    });
  }

  const facets = response?.facets;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      <title>TA | Found videos</title>
      <ScrollToTopOnNavigate />
      <div className="boxed-content ux-wide">
        <div className="title-split">
          <div className="title-bar">
            <h1>Found videos</h1>
            <p className="ux-help">
              Videos TA knows about but hasn’t downloaded yet, and what your rules say about each.
              What’s running right now is on the <Link to={Routes.Queue}>Queue</Link>.
            </p>
          </div>
          {isAdmin && (
            <div className="ux-toolbar" role="toolbar" aria-label="Found video actions">
              <ToolbarButton
                label="Check channels for new videos"
                help="Queues a check of every subscribed channel and playlist. Found videos appear here, and the ones your rules allow start downloading as they're found."
                icon={iconRescan}
                busy={checking}
                onClick={async () => {
                  setChecking(true);
                  const result = await createJob({ type: 'check_subscriptions' });
                  if (notBuilt(result)) await updateTaskByName('update_subscribed');
                  setMessage(
                    result.data?.merged
                      ? 'A check was already waiting; it moved up the queue.'
                      : 'Channel check queued. Follow it on the Queue page.',
                  );
                  setChecking(false);
                }}
              />
              <ToolbarButton
                label="Download all allowed"
                help="Queues downloads for every found video that isn't ignored."
                icon={iconDownload}
                primary
                onClick={async () => {
                  const result = await foundAction({
                    action: 'download',
                    filter: { tab: 'waiting' },
                  });
                  if (notBuilt(result)) await updateTaskByName('download_pending');
                  setMessage(
                    result.data ? `${result.data.changed} downloads queued.` : 'Downloads started.',
                  );
                  reload();
                }}
              />
              <ToolbarButton
                label="Add…"
                help="Paste a video, channel or playlist link."
                icon={iconAdd}
                onClick={() => setShowAdd(true)}
              />
            </div>
          )}
        </div>

        {showAdd && <AddDialog onClose={() => setShowAdd(false)} onAdded={reload} />}

        {message && (
          <p className="ux-toast" role="status">
            {message}{' '}
            <button type="button" className="ux-link-button" onClick={() => setMessage('')}>
              Dismiss
            </button>
          </p>
        )}

        <Tabs
          label="Found videos"
          value={tab}
          onChange={changeTab}
          tabs={[
            { value: 'waiting', label: 'Waiting', count: counts?.waiting },
            { value: 'ignored', label: 'Ignored', count: counts?.ignored },
            { value: 'failed', label: 'Failed', count: counts?.failed },
            { value: 'downloaded', label: 'Downloaded recently', count: counts?.downloaded },
          ]}
        />

        <div className="ux-filterbar">
          <SearchBox
            label="Search found videos"
            placeholder="Search by title or channel…"
            value={filters.q}
            onChange={q => changeFilters({ q })}
          />
          {facets?.channels && facets.channels.length > 0 && (
            <select
              aria-label="Channel"
              value={filters.channel}
              onChange={event => changeFilters({ channel: event.target.value })}
            >
              <option value="">All channels</option>
              {facets.channels.map(channel => (
                <option key={channel.id} value={channel.id}>
                  {channel.name} ({channel.count})
                </option>
              ))}
            </select>
          )}
          <FilterChips
            label="Type"
            options={TYPE_CHIPS}
            value={(filters.vid_type || 'all') as 'all'}
            onChange={value => changeFilters({ vid_type: value === 'all' ? '' : value })}
          />
          <button
            type="button"
            className="ux-link-button"
            aria-expanded={showMore}
            onClick={() => setShowMore(!showMore)}
          >
            {showMore ? 'Fewer filters' : 'More filters'}
          </button>
          <ViewToggle
            views={['table', 'grid']}
            value={viewStyle}
            onChange={async view => {
              const config: Partial<UserConfigType> = { view_style_downloads: view };
              setUserConfig({ ...userConfig, ...config });
              try {
                await updateUserConfig(config);
              } catch {
                // older server: the choice lasts for this visit
              }
            }}
          />
        </div>

        {showMore && (
          <div className="ux-more-filters">
            <label>
              Longer than{' '}
              <input
                type="number"
                min={0}
                value={filters.min_minutes}
                onChange={event => changeFilters({ min_minutes: event.target.value })}
              />{' '}
              min
            </label>
            <label>
              Shorter than{' '}
              <input
                type="number"
                min={0}
                value={filters.max_minutes}
                onChange={event => changeFilters({ max_minutes: event.target.value })}
              />{' '}
              min
            </label>
            <label>
              Published from{' '}
              <input
                type="date"
                value={filters.published_from}
                onChange={event => changeFilters({ published_from: event.target.value })}
              />
            </label>
            <label>
              to{' '}
              <input
                type="date"
                value={filters.published_to}
                onChange={event => changeFilters({ published_to: event.target.value })}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={filters.skipped_by_rule}
                onChange={event => changeFilters({ skipped_by_rule: event.target.checked })}
              />{' '}
              Skipped by a rule
            </label>
            <label>
              <input
                type="checkbox"
                checked={filters.error}
                onChange={event => changeFilters({ error: event.target.checked })}
              />{' '}
              Has an error
            </label>
            <button
              type="button"
              className="ux-link-button"
              onClick={() => changeFilters(NO_FILTERS)}
            >
              Clear filters
            </button>
          </div>
        )}

        <p className="ux-count" aria-live="polite">
          {response === undefined ? 'Loading…' : `${total.toLocaleString()} videos`}
        </p>

        {isAdmin && BULK_ACTIONS[tab].length > 0 && (
          <BulkBar
            count={selectedCount}
            noun="video"
            onClear={() => {
              setSelected(new Set());
              setAllMatching(false);
            }}
          >
            {BULK_ACTIONS[tab].map(([action, label]) => (
              <button
                key={action}
                type="button"
                onClick={() => run(action, allMatching ? undefined : Array.from(selected))}
              >
                {label} {selectedCount.toLocaleString()}
              </button>
            ))}
            {pageAllSelected && !allMatching && total > rows.length && (
              <button type="button" className="ux-link-button" onClick={() => setAllMatching(true)}>
                Select all {total.toLocaleString()} matching
              </button>
            )}
            {allMatching && <span>All {total.toLocaleString()} matching the filters</span>}
          </BulkBar>
        )}

        {response === undefined && <LoadingIndicator />}
        {response !== undefined && viewStyle === 'table' && (
          <DataTable
            caption="Found videos"
            rows={rows}
            columns={columns}
            rowKey={video => video.youtube_id}
            rowLabel={video => video.title}
            sort={sort}
            onSortChange={next => {
              setSort(next);
              setPage(1);
            }}
            selected={isAdmin && BULK_ACTIONS[tab].length > 0 ? selected : undefined}
            onSelectedChange={
              isAdmin && BULK_ACTIONS[tab].length > 0
                ? next => {
                    setSelected(next);
                    setAllMatching(false);
                  }
                : undefined
            }
            empty={
              tab === 'waiting'
                ? 'Nothing is waiting. Check channels to find new videos.'
                : 'Nothing here.'
            }
          />
        )}
        {response !== undefined && viewStyle === 'grid' && (
          <div className="ux-found-grid">
            {rows.map(video => (
              <article key={video.youtube_id} className="ux-video-card">
                <div className="ux-video-card-thumb">
                  <VideoThumbnail videoThumbUrl={video.thumb_url ?? undefined} />
                  <span className="ux-duration">{video.duration}</span>
                </div>
                <div className="ux-video-card-text">
                  <span className="ux-video-title">{video.title}</span>
                  <span className="ux-muted">{video.channel_name}</span>
                  <VerdictBadge verdict={video.verdict} canEdit={isAdmin} onChanged={reload} />
                  {isAdmin && (
                    <div className="ux-row-actions">
                      {ROW_ACTIONS[tab].map(([action, label, help]) => (
                        <button
                          key={action}
                          type="button"
                          className="ux-row-button"
                          title={help}
                          aria-label={`${label}: ${video.title}`}
                          onClick={() => run(action, [video.youtube_id])}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}

        {pages > 1 && (
          <nav className="ux-pager" aria-label="Pages">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>
              Page {page} of {pages}
            </span>
            <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>
              Next
            </button>
          </nav>
        )}
      </div>
    </>
  );
};

type FoundFacets = {
  channels?: { id: string; name: string; count: number }[];
};

export default FoundVideos;
