import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  addUrls,
  AddResult,
  getRule,
  toRuleBody,
  NumberingScheme,
  notBuilt,
  PreviewResult,
  previewAdd,
  RuleBody,
} from '../api/ux/client';
import updateDownloadQueue from '../api/actions/updateDownloadQueue';
import { EMBY_NUMBERING_LABELS } from '../api/actions/updateEmbyNumbering';
import Routes from '../configuration/routes/RouteList';
import { ApiError } from '../functions/APIClient';
import Modal from './ux/Modal';
import { verdictSentence } from '../functions/verdictSentence';
import RuleEditor from './rules/RuleEditor';
import ChannelIcon from './ChannelIcon';

type Mode = 'subscribe' | 'once' | 'newest_n';

const KIND_LABEL = { video: 'Video', channel: 'Channel', playlist: 'Playlist' };

type AddDialogProps = {
  onClose: () => void;
  onAdded?: () => void;
};

/** paste links, see what the rules would do, then subscribe or download once */
const AddDialog = ({ onClose, onAdded }: AddDialogProps) => {
  const id = useId();
  const [urls, setUrls] = useState('');
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [legacy, setLegacy] = useState(false);
  const [error, setError] = useState('');
  const [rule, setRule] = useState<RuleBody | null>(null);
  const [editRule, setEditRule] = useState(false);
  /** the rule the editor starts from: the one that applies now */
  const [baseRule, setBaseRule] = useState<RuleBody | null>();
  const [numbering, setNumbering] = useState<NumberingScheme | ''>('');
  const [mode, setMode] = useState<Mode>('subscribe');
  const [newestN, setNewestN] = useState(10);
  const [result, setResult] = useState<AddResult | null>(null);
  // legacy form
  const [autostart, setAutostart] = useState(true);
  const [flat, setFlat] = useState(false);
  const [force, setForce] = useState(false);
  const [previewedFor, setPreviewedFor] = useState('');

  const runPreview = async (ruleOverride: RuleBody | null = rule) => {
    if (!urls.trim()) return;
    setLoading(true);
    setError('');
    try {
      const response = await previewAdd({
        urls,
        rule: ruleOverride,
        newest_n: mode === 'newest_n' ? newestN : undefined,
      });
      if (notBuilt(response)) {
        setLegacy(true);
      } else if (response.data) {
        setPreview(response.data);
        setPreviewedFor(urls);
        const first = response.data.items[0];
        if (first && !numbering) setNumbering(first.numbering.scheme);
        if (first && !first.subscribed && first.kind === 'video') setMode('once');
      }
    } catch (failure) {
      setError((failure as ApiError).message ?? 'Could not read those links.');
    }
    setLoading(false);
  };

  // a changed draft rule re-runs the preview (the server caches the listing)
  useEffect(() => {
    if (!rule || !previewedFor) return;
    const timer = setTimeout(() => runPreview(rule), 500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rule]);

  const add = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await addUrls({
        urls,
        mode,
        newest_n: mode === 'newest_n' ? newestN : undefined,
        rule: rule ?? undefined,
        save_rule: mode === 'subscribe' && rule !== null,
        numbering: numbering || undefined,
        download_now: true,
        priority: 'high',
      });
      if (response.data) {
        setResult(response.data);
        onAdded?.();
      }
    } catch (failure) {
      setError((failure as ApiError).message ?? 'Could not add those links.');
    }
    setLoading(false);
  };

  const addLegacy = async () => {
    await updateDownloadQueue({ youtubeIdStrings: urls, autostart, flat, force });
    onAdded?.();
    onClose();
  };

  const items = preview?.items ?? [];
  const stale = preview !== null && previewedFor !== urls;
  const anyChannel = items.some(item => item.kind !== 'video');

  if (result) {
    return (
      <Modal title="Added" onClose={onClose}>
        <p role="status">
          Queued {result.jobs.length} check{result.jobs.length === 1 ? '' : 's'} at high priority.
          Videos the rules allow start downloading as they’re found.
        </p>
        {result.errors.length > 0 && (
          <ul>
            {result.errors.map(item => (
              <li key={item.url} className="danger-zone">
                {item.url}: {item.error}
              </li>
            ))}
          </ul>
        )}
        <div className="button-box">
          <Link to={Routes.Queue} onClick={onClose}>
            Watch it on the Queue page
          </Link>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Add videos, channels or playlists" onClose={onClose} wide>
      <label htmlFor={`${id}-urls`}>
        Paste one or more links or IDs, one per line. TA works out whether each is a video, a
        channel or a playlist.
      </label>
      <textarea
        id={`${id}-urls`}
        rows={3}
        value={urls}
        placeholder="https://www.youtube.com/@channel"
        onChange={event => setUrls(event.target.value)}
      />

      {legacy ? (
        <>
          <p className="ux-help">
            This server can’t preview links yet, so they’re added straight to Found videos.
          </p>
          <fieldset className="ux-options">
            <legend>Options</legend>
            <label>
              <input
                type="checkbox"
                checked={autostart}
                onChange={() => setAutostart(!autostart)}
              />{' '}
              Start downloading now
            </label>
            <label>
              <input type="checkbox" checked={flat} onChange={() => setFlat(!flat)} /> Add quickly
              (fetch details later)
            </label>
            <label>
              <input type="checkbox" checked={force} onChange={() => setForce(!force)} /> Download
              again if already downloaded
            </label>
          </fieldset>
          <div className="button-box">
            <button type="button" disabled={!urls.trim()} onClick={addLegacy}>
              Add
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="button-box">
            <button type="button" disabled={!urls.trim() || loading} onClick={() => runPreview()}>
              {preview && !stale ? 'Preview again' : 'Preview'}
            </button>
            {loading && (
              <span className="ux-muted">Reading YouTube… a few seconds per channel.</span>
            )}
          </div>
          {error && <p className="danger-zone">{error}</p>}

          {preview && (
            <div className={`ux-add-preview ${stale ? 'stale' : ''}`} aria-live="polite">
              {stale && <p className="ux-help">The links changed. Preview again to update.</p>}
              {preview.errors.map(item => (
                <p key={item.url} className="danger-zone">
                  {item.url}: {item.error}
                </p>
              ))}
              {items.map(item => (
                <article key={item.url} className="ux-add-item">
                  <span className="ux-shelf-avatar" aria-hidden="true">
                    <ChannelIcon
                      channelId={item.channel_id ?? item.id}
                      channelThumbUrl={item.thumb_url ?? undefined}
                    />
                  </span>
                  <div>
                    <h3>
                      {item.title}{' '}
                      <span className="ux-badge numbering">{KIND_LABEL[item.kind]}</span>
                      {item.subscribed && <span className="ux-badge rule own">Subscribed</span>}
                    </h3>
                    {item.kind !== 'channel' && item.channel_name && (
                      <p className="ux-muted">by {item.channel_name}</p>
                    )}
                    <p>
                      <strong>{verdictSentence(item)}</strong>
                    </p>
                    <p className="ux-muted">
                      Rule:{' '}
                      {rule
                        ? 'your changes below'
                        : (item.rule?.summary ?? 'none, everything downloads')}
                    </p>
                  </div>
                </article>
              ))}

              <div className="button-box">
                <button
                  type="button"
                  className="ux-link-button"
                  aria-expanded={editRule}
                  onClick={async () => {
                    const ref = items[0]?.rule;
                    if (!editRule && baseRule === undefined) {
                      if (ref && ref.scope !== 'video') {
                        const current = await getRule(
                          ref.scope as 'default' | 'channel' | 'playlist',
                          ref.target_id ?? undefined,
                        );
                        setBaseRule(notBuilt(current) ? null : toRuleBody(current.data));
                      } else {
                        setBaseRule(null);
                      }
                    }
                    setEditRule(!editRule);
                  }}
                >
                  {editRule ? 'Hide the rule' : 'Change the rule for this add…'}
                </button>
              </div>
              {editRule && baseRule !== undefined && (
                <RuleEditor
                  scope={items[0]?.kind === 'playlist' ? 'playlist' : 'channel'}
                  targetId={
                    items[0]?.kind === 'playlist'
                      ? items[0].id
                      : (items[0]?.channel_id ?? undefined)
                  }
                  rule={rule ?? baseRule}
                  mode="draft"
                  onChange={setRule}
                />
              )}

              {anyChannel && (
                <label className="ux-inline-form">
                  Emby numbering{' '}
                  <select
                    value={numbering}
                    onChange={event => setNumbering(event.target.value as NumberingScheme)}
                  >
                    {(Object.keys(EMBY_NUMBERING_LABELS) as NumberingScheme[])
                      .filter(
                        scheme => scheme !== 'playlist_position' || items[0]?.kind === 'playlist',
                      )
                      .map(scheme => (
                        <option key={scheme} value={scheme}>
                          {EMBY_NUMBERING_LABELS[scheme]}
                        </option>
                      ))}
                  </select>
                </label>
              )}

              <fieldset className="ux-options">
                <legend>What should TA do?</legend>
                {anyChannel && (
                  <label>
                    <input
                      type="radio"
                      name={`${id}-mode`}
                      checked={mode === 'subscribe'}
                      onChange={() => setMode('subscribe')}
                    />{' '}
                    Subscribe and keep checking for new videos
                    {rule && <span className="ux-muted"> (and keep this rule)</span>}
                  </label>
                )}
                <label>
                  <input
                    type="radio"
                    name={`${id}-mode`}
                    checked={mode === 'once'}
                    onChange={() => setMode('once')}
                  />{' '}
                  Download {anyChannel ? 'these' : 'it'} once, don’t subscribe
                </label>
                {anyChannel && (
                  <label>
                    <input
                      type="radio"
                      name={`${id}-mode`}
                      checked={mode === 'newest_n'}
                      onChange={() => setMode('newest_n')}
                    />{' '}
                    Only the newest{' '}
                    <input
                      type="number"
                      min={1}
                      aria-label="How many of the newest videos"
                      value={newestN}
                      onChange={event => setNewestN(Number(event.target.value) || 1)}
                      onFocus={() => setMode('newest_n')}
                    />{' '}
                    videos
                  </label>
                )}
              </fieldset>

              <div className="button-box">
                <button
                  type="button"
                  disabled={loading || stale || items.length === 0}
                  onClick={add}
                >
                  Add
                </button>
                <span className="ux-muted">
                  Checks only {items.length === 1 ? 'this link' : 'these links'}, at high priority.
                </span>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
};

export default AddDialog;
