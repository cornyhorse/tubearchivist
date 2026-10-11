import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  clearVideoOverride,
  listRules,
  listVideoOverrides,
  notBuilt,
  RuleBody,
  RuleRow,
  RuleScope,
  toRuleBody,
  VideoOverride,
} from '../api/ux/client';
import loadChannelList from '../api/loader/loadChannelList';
import loadDefaultDownloadRule from '../api/loader/loadDownloadRule';
import Routes from '../configuration/routes/RouteList';
import { ChannelType } from './Channels';
import { describeRuleParts } from '../functions/describeRule';
import { ago } from '../functions/timeText';
import DataTable, { Column } from '../components/ux/DataTable';
import Modal from '../components/ux/Modal';
import RuleEditor from '../components/rules/RuleEditor';
import ScrollToTopOnNavigate from '../components/ScrollToTop';
import LoadingIndicator from '../components/LoadingIndicator';

type Editing = { scope: RuleScope; targetId?: string; title: string; rule: RuleBody | null };

const SCOPE_LABEL: Record<RuleScope, string> = {
  default: 'Everything',
  channel: 'Channel',
  playlist: 'Playlist',
};

const summary = (row: RuleRow) =>
  row.rule.summary || describeRuleParts(toRuleBody(row.rule)!).join(' · ') || 'All videos';

/** rows from the old API: the default rule plus each channel's own rule */
const legacyRows = async (): Promise<RuleRow[]> => {
  const [defaultRule, channels] = await Promise.all([
    loadDefaultDownloadRule(),
    loadChannelList({ page: 0, filter: 'has_rule', pageSize: 500 }),
  ]);
  const rows: RuleRow[] = [];
  if (defaultRule.data) {
    rows.push({
      scope: 'default',
      target_id: '',
      target_title: 'Default rule',
      target_subscribed: true,
      rule: { ...defaultRule.data, summary: describeRuleParts(defaultRule.data).join(' · ') },
    });
  }
  for (const channel of (channels.data?.data ?? []) as ChannelType[]) {
    if (!channel.download_rule) continue;
    rows.push({
      scope: 'channel',
      target_id: channel.channel_id,
      target_title: channel.channel_name,
      target_subscribed: channel.channel_subscribed,
      rule: {
        ...channel.download_rule,
        summary: describeRuleParts(channel.download_rule).join(' · '),
      },
    });
  }
  return rows;
};

const Rules = () => {
  const [rows, setRows] = useState<RuleRow[]>();
  const [overrides, setOverrides] = useState<VideoOverride[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [channels, setChannels] = useState<ChannelType[]>([]);
  const [newChannel, setNewChannel] = useState('');

  const reload = useCallback(async () => {
    const response = await listRules();
    if (notBuilt(response)) {
      setRows(await legacyRows());
    } else {
      setRows(response.data?.data ?? []);
      const videos = await listVideoOverrides();
      setOverrides(videos.data?.data ?? []);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload();
    (async () => {
      const response = await loadChannelList({ page: 0, pageSize: 500, sort: 'name' });
      setChannels(response.data?.data ?? []);
    })();
  }, [reload]);

  const hasDefault = rows?.some(row => row.scope === 'default');
  const ruled = new Set(rows?.map(row => row.target_id));

  const columns: Column<RuleRow>[] = [
    {
      key: 'scope',
      header: 'Applies to',
      render: row =>
        row.scope === 'default' ? (
          <strong>Every channel and playlist without its own rule</strong>
        ) : (
          <>
            <span className="ux-muted">{SCOPE_LABEL[row.scope]}:</span>{' '}
            <Link
              to={
                row.scope === 'channel'
                  ? Routes.ChannelAbout(row.target_id ?? '')
                  : Routes.Playlist(row.target_id ?? '')
              }
            >
              {row.target_title}
            </Link>
            {!row.target_subscribed && <span className="ux-muted"> (not subscribed)</span>}
          </>
        ),
    },
    {
      key: 'rule',
      header: 'Downloads only',
      className: 'ux-col-title',
      render: row => (row.rule.enabled ? summary(row) : <span className="ux-muted">Rule off</span>),
    },
    {
      key: 'action',
      header: 'Other videos',
      render: row => (row.rule.action === 'skip' ? 'Left out' : 'Kept as ignored'),
    },
    {
      key: 'edit',
      header: 'Actions',
      render: row => (
        <button
          type="button"
          className="ux-row-button"
          aria-label={`Edit the rule for ${row.target_title}`}
          onClick={() =>
            setEditing({
              scope: row.scope,
              targetId: row.target_id || undefined,
              title: row.scope === 'default' ? 'Default rule' : (row.target_title ?? ''),
              rule: toRuleBody(row.rule),
            })
          }
        >
          Edit
        </button>
      ),
    },
  ];

  return (
    <>
      <title>TA | Rules</title>
      <ScrollToTopOnNavigate />
      <div className="boxed-content ux-wide">
        <div className="title-bar">
          <h1>Rules</h1>
          <p className="ux-help">
            Which videos TA downloads when it checks a channel or playlist. The first rule that
            applies wins: your choice for a single video, then the playlist’s rule, then the
            channel’s, then the default. Videos you add by link with “Download once” always
            download.
          </p>
        </div>

        <div className="ux-toolbar">
          {!hasDefault && (
            <button
              type="button"
              onClick={() => setEditing({ scope: 'default', title: 'Default rule', rule: null })}
            >
              Set a default rule…
            </button>
          )}
          <label className="ux-inline-form">
            Add a rule for{' '}
            <select value={newChannel} onChange={event => setNewChannel(event.target.value)}>
              <option value="">choose a channel…</option>
              {channels
                .filter(channel => !ruled.has(channel.channel_id))
                .map(channel => (
                  <option key={channel.channel_id} value={channel.channel_id}>
                    {channel.channel_name}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="button"
            disabled={!newChannel}
            onClick={() => {
              const channel = channels.find(item => item.channel_id === newChannel);
              setEditing({
                scope: 'channel',
                targetId: newChannel,
                title: channel?.channel_name ?? newChannel,
                rule: null,
              });
              setNewChannel('');
            }}
          >
            Add rule…
          </button>
        </div>

        {rows === undefined ? (
          <LoadingIndicator />
        ) : (
          <DataTable
            caption="Download rules"
            rows={rows}
            columns={columns}
            rowKey={row => `${row.scope}:${row.target_id}`}
            empty="No rules: every video found is downloaded."
          />
        )}

        <section className="ux-queue-section" aria-labelledby="video-choices">
          <h2 id="video-choices">Your choices for single videos</h2>
          <p className="ux-help">
            Made with “Download anyway” or “Always ignore” on Found videos or a video’s page. They
            beat every rule.
          </p>
          {overrides.length === 0 ? (
            <p className="ux-muted">None yet.</p>
          ) : (
            <table className="ux-table ux-compact">
              <caption className="sr-only">Per-video choices</caption>
              <thead>
                <tr>
                  <th scope="col">Video</th>
                  <th scope="col">Choice</th>
                  <th scope="col">Made</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {overrides.map(item => (
                  <tr key={item.youtube_id}>
                    <td className="ux-col-title">
                      <Link to={Routes.Video(item.youtube_id)}>
                        {item.title ?? item.youtube_id}
                      </Link>
                    </td>
                    <td>{item.override === 'download' ? 'Always download' : 'Always ignore'}</td>
                    <td>{ago(item.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="ux-row-button"
                        aria-label={`Forget the choice for ${item.title ?? item.youtube_id}`}
                        onClick={async () => {
                          await clearVideoOverride(item.youtube_id);
                          reload();
                        }}
                      >
                        Let the rules decide
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {editing && (
          <Modal wide title={`Rule: ${editing.title}`} onClose={() => setEditing(null)}>
            <RuleEditor
              scope={editing.scope}
              targetId={editing.targetId}
              rule={editing.rule}
              onSaved={() => {
                setEditing(null);
                reload();
              }}
            />
          </Modal>
        )}
      </div>
    </>
  );
};

export default Rules;
