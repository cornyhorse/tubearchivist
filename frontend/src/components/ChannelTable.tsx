import { Link } from 'react-router-dom';
import { ChannelType } from '../pages/Channels';
import Routes from '../configuration/routes/RouteList';
import { DownloadRuleType } from '../api/actions/updateDownloadRule';
import formatDate from '../functions/formatDates';
import ChannelIcon from './ChannelIcon';
import DataTable, { Column, SortState } from './ux/DataTable';
import { NumberingBadge, RuleBadge } from './ux/Badges';
import AutoDownloadSwitch from './ux/AutoDownloadSwitch';
import EmbyShowSelect from './ux/EmbyShowSelect';

type ChannelTableProps = {
  channels: ChannelType[];
  defaultRule: DownloadRuleType | null;
  isAdmin?: boolean;
  sort: SortState;
  onSortChange: (sort: SortState) => void;
  selected: Set<string>;
  onSelectedChange: (selected: Set<string>) => void;
  onToggleSubscribe: (channel: ChannelType) => void;
  onChanged: () => void;
  /** set when the server has the work queue */
  onCheckNow?: (channel: ChannelType) => void;
};

/** a count, or a dash while the server doesn't send it yet */
const count = (value: number | undefined) =>
  value === undefined ? <span className="ux-muted">—</span> : value.toLocaleString();

const date = (value: string | null | undefined) =>
  value ? formatDate(value) : <span className="ux-muted">—</span>;

const ChannelTable = ({
  channels,
  defaultRule,
  isAdmin,
  sort,
  onSortChange,
  selected,
  onSelectedChange,
  onToggleSubscribe,
  onChanged,
  onCheckNow,
}: ChannelTableProps) => {
  const columns: Column<ChannelType>[] = [
    {
      key: 'avatar',
      header: '',
      className: 'ux-col-avatar',
      render: channel => (
        <Link to={Routes.Channel(channel.channel_id)} tabIndex={-1} aria-hidden="true">
          <ChannelIcon channelId={channel.channel_id} channelThumbUrl={channel.channel_thumb_url} />
        </Link>
      ),
    },
    {
      key: 'name',
      header: 'Channel',
      sortKey: 'name',
      className: 'ux-col-title',
      render: channel => (
        <Link to={Routes.Channel(channel.channel_id)}>{channel.channel_name}</Link>
      ),
    },
    {
      key: 'subscribed',
      header: 'Subscribed',
      sortKey: 'subscribed',
      help: 'Subscribed channels are checked for new videos on the schedule.',
      render: channel =>
        channel.channel_subscribed ? 'Yes' : <span className="ux-muted">No</span>,
    },
    {
      key: 'downloaded',
      header: 'Downloaded',
      sortKey: 'downloaded',
      className: 'ux-num',
      render: channel => count(channel.counts?.downloaded),
    },
    {
      key: 'queued',
      header: 'Queued',
      sortKey: 'queued',
      className: 'ux-num',
      help: 'Found videos waiting to be downloaded.',
      render: channel =>
        channel.counts?.queued ? (
          <Link to={Routes.DownloadsByChannelId(channel.channel_id)}>
            {channel.counts.queued.toLocaleString()}
          </Link>
        ) : (
          count(channel.counts?.queued)
        ),
    },
    {
      key: 'ignored',
      header: 'Ignored',
      sortKey: 'ignored',
      className: 'ux-num',
      help: 'Found videos that a rule or you set aside.',
      render: channel =>
        channel.counts?.ignored ? (
          <Link to={Routes.IgnoredByChannelId(channel.channel_id)}>
            {channel.counts.ignored.toLocaleString()}
          </Link>
        ) : (
          count(channel.counts?.ignored)
        ),
    },
    {
      key: 'latest_upload',
      header: 'Latest upload',
      sortKey: 'latest_upload',
      render: channel => date(channel.latest_upload),
    },
    {
      key: 'last_checked',
      header: 'Last checked',
      sortKey: 'last_checked',
      help: 'When TA last refreshed this channel from YouTube.',
      render: channel => date(channel.channel_last_refresh),
    },
    {
      key: 'rule',
      header: 'Rule',
      help: 'Which videos get downloaded. Click a rule to change it.',
      render: channel => (
        <RuleBadge
          channelId={channel.channel_id}
          channelName={channel.channel_name}
          rule={channel.download_rule}
          defaultRule={defaultRule}
          canEdit={isAdmin}
          onSaved={onChanged}
        />
      ),
    },
    {
      key: 'auto_download',
      header: 'Auto-download',
      help: 'On: new videos a check finds download at once, within the rule. Off: they wait in Found videos.',
      render: channel => (
        <AutoDownloadSwitch
          kind="channel"
          id={channel.channel_id}
          name={channel.channel_name}
          value={channel.auto_download}
          canEdit={isAdmin}
          onSaved={onChanged}
        />
      ),
    },
    {
      key: 'numbering',
      header: 'Numbering',
      help: 'How Emby numbers the episodes. Click to change it.',
      render: channel => (
        <NumberingBadge
          kind="channel"
          id={channel.channel_id}
          name={channel.channel_name}
          value={channel.emby_numbering}
          options={['date', 'year_frozen', 'absolute_frozen']}
          canEdit={isAdmin}
          onSaved={onChanged}
        />
      ),
    },
    {
      key: 'emby_show',
      header: 'Emby show',
      help: 'Whether this channel has its own show in Emby. Otherwise its videos are in the Other videos show.',
      render: channel => (
        <EmbyShowSelect
          id={channel.channel_id}
          name={channel.channel_name}
          value={channel.emby_show}
          canEdit={isAdmin}
          onSaved={onChanged}
        />
      ),
    },
  ];

  if (isAdmin) {
    columns.push({
      key: 'actions',
      header: 'Actions',
      className: 'ux-col-actions',
      render: channel => (
        <div className="ux-row-actions">
          {onCheckNow && (
            <button
              type="button"
              className="ux-row-button"
              title="Check this channel for new videos now, at high priority"
              aria-label={`Check ${channel.channel_name} now`}
              onClick={() => onCheckNow(channel)}
            >
              Check now
            </button>
          )}
          <button
            type="button"
            className="ux-row-button"
            title={
              channel.channel_subscribed
                ? 'Stop checking this channel for new videos. Downloaded videos stay.'
                : 'Check this channel for new videos on the schedule.'
            }
            onClick={() => onToggleSubscribe(channel)}
          >
            {channel.channel_subscribed ? 'Unsubscribe' : 'Subscribe'}
          </button>
        </div>
      ),
    });
  }

  return (
    <DataTable
      caption="Channels"
      rows={channels}
      columns={columns}
      rowKey={channel => channel.channel_id}
      rowLabel={channel => channel.channel_name}
      sort={sort}
      onSortChange={onSortChange}
      selected={isAdmin ? selected : undefined}
      onSelectedChange={isAdmin ? onSelectedChange : undefined}
      empty="No channels match."
    />
  );
};

export default ChannelTable;
